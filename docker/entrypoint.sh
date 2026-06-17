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

# /etc/cron.d/knowl の冒頭に直接 KEY=VALUE を書くため、ここでまず env 行を組み立てる。
# vixie cron は /etc/environment / PAM を経由せずジョブ環境を組むので、確実に届ける手段はこれ。
CRON_ENV_LINES="PATH=${PATH}"
for v in SLACK_BOT_TOKEN SLACK_CHANNEL KNOWL_CONFIG KNOWL_CREDENTIALS; do
  val="${!v-}"
  if [ -n "${val}" ]; then
    CRON_ENV_LINES="${CRON_ENV_LINES}
${v}=${val}"
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

# 初回も即時実行
/usr/local/bin/knowl-run-cycle || true

# cron を foreground で。tini が PID 1 でこのプロセスを監視する。
exec cron -f
