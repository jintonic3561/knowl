#!/usr/bin/env bash
# Knowl 常時起動コンテナのエントリポイント。
# - 設定値からcron頻度を読み crontab を生成
# - 環境変数を /etc/environment へ書き出し (cron は親プロセスの env を継承しないため)
# - cron を foreground (-f) で実行し、ジョブの stdout/stderr を PID 1 (tini) の fd に流す
set -euo pipefail

CONFIG="${KNOWL_CONFIG:-/etc/knowl/config.yaml}"
# config 読込が失敗したらコンテナを止める。silent 60 fallback はサイレント故障の温床。
INTERVAL_MIN="$(uv run --project /opt/knowl python -c "
from knowl.config import load_config
print(load_config('${CONFIG}').cron_interval_minutes)
")"

# vixie cron は /etc/environment / PAM を経由せずジョブ環境を組むので、env を確実に届ける必要がある。
# /etc/cron.d/knowl は 0644 で平文公開されるので、機密は別ファイル 0600 に切り出し run-cycle.sh で source する。
# 非機密の値だけ /etc/cron.d/knowl 冒頭の KEY=VALUE 行に並べる。
CRON_ENV_LINES="PATH=${PATH}"
for v in SLACK_CHANNEL KNOWL_CONFIG KNOWL_CREDENTIALS; do
  val="${!v-}"
  if [ -n "${val}" ]; then
    CRON_ENV_LINES="${CRON_ENV_LINES}
${v}=${val}"
  fi
done

# 機密 env は 0600 の別ファイルに書き出す。先に空ファイルを 0600 で作ってから追記することで
# 平文の中身がデフォルト umask の隙間で他者から read 可能になる窓を作らない。
SECRETS_ENV=/etc/knowl/secrets.env
mkdir -p /etc/knowl
install -m 0600 /dev/null "${SECRETS_ENV}"
for v in SLACK_BOT_TOKEN SLACK_APP_TOKEN GH_TOKEN; do
  val="${!v-}"
  if [ -n "${val}" ]; then
    printf '%s=%q\n' "${v}" "${val}" >> "${SECRETS_ENV}"
  fi
done

# cron スケジュール生成 (1h → "0 * * * *" など)
if [ "${INTERVAL_MIN}" -ge 60 ] && [ $((INTERVAL_MIN % 60)) -eq 0 ]; then
  HOURS=$((INTERVAL_MIN / 60))
  if [ "${HOURS}" -eq 1 ]; then
    SCHED="0 * * * *"
  else
    SCHED="0 */${HOURS} * * *"
  fi
else
  SCHED="*/${INTERVAL_MIN} * * * *"
fi

# stdout/stderr を tini (PID 1) の fd に流すことで docker logs に出す。
# /etc/cron.d/* は cron daemon が直接読むので user 付きで書く。
# root crontab に流す `crontab /etc/cron.d/knowl` は user フィールドが crontab(5) で禁則のため行わない。
{
  printf '%s\n' "${CRON_ENV_LINES}"
  echo "${SCHED} root /usr/local/bin/knowl-run-cycle > /proc/1/fd/1 2> /proc/1/fd/2"
} > /etc/cron.d/knowl
chmod 0644 /etc/cron.d/knowl

echo "[knowl] starting cron with schedule: ${SCHED}"
echo "[knowl] config: ${CONFIG}"

# 初回も即時実行。ただし KNOWL_SKIP_INITIAL_RUN が truthy なら skip する。
# `make deploy` のように、稼働中のサイクル直後にリビルド + 再起動する用途では、
# 直前サイクルの完了とほぼ同時にもう 1 サイクル走るのを避けたい。
case "${KNOWL_SKIP_INITIAL_RUN:-0}" in
  1|true|TRUE|yes|YES)
    echo "[knowl] KNOWL_SKIP_INITIAL_RUN set; skipping initial run"
    ;;
  *)
    /usr/local/bin/knowl-run-cycle || true
    ;;
esac

# cron と Slack bot supervisor の両方を background で立てて、最後の wait を foreground にする。
# こうすることで tini が SIGTERM を投げると trap がそれぞれに伝播し、 graceful shutdown が成立する。
# `exec cron -f` の単純構成では bot 側 subshell に signal が届かなかったので、 wait 構成に揃える。
cron -f > /proc/1/fd/1 2> /proc/1/fd/2 &
CRON_PID=$!
echo "[knowl] cron started (pid=${CRON_PID})"

BOT_SUPERVISOR_PID=""
if [ -n "${SLACK_APP_TOKEN-}" ] && [ -n "${SLACK_BOT_TOKEN-}" ]; then
  echo "[knowl] starting Slack bot supervisor"
  (
    trap 'kill -TERM ${BOT_PID-} 2>/dev/null; exit 0' TERM INT
    while true; do
      uv run --project /opt/knowl knowl bot --config "${CONFIG}" \
        --credentials "${KNOWL_CREDENTIALS:-/root/.claude/.credentials.json}" \
        > /proc/1/fd/1 2> /proc/1/fd/2 &
      BOT_PID=$!
      wait "${BOT_PID}" || true
      echo "[knowl] Slack bot exited; restarting in 5s" > /proc/1/fd/1
      sleep 5
    done
  ) &
  BOT_SUPERVISOR_PID=$!
else
  echo "[knowl] SLACK_APP_TOKEN/SLACK_BOT_TOKEN not set; skipping Slack bot"
fi

# tini → entrypoint への SIGTERM/SIGINT を子に伝播してから、 wait で先に exit したものに従う。
# cron が落ちたら container 全体を exit させ restart=unless-stopped に任せる方針。
trap 'kill -TERM ${CRON_PID} ${BOT_SUPERVISOR_PID} 2>/dev/null' TERM INT
wait -n "${CRON_PID}" ${BOT_SUPERVISOR_PID:+"${BOT_SUPERVISOR_PID}"}
EXIT_CODE=$?
kill -TERM ${CRON_PID} ${BOT_SUPERVISOR_PID} 2>/dev/null || true
exit "${EXIT_CODE}"
