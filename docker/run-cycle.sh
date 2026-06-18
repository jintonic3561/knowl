#!/usr/bin/env bash
# cron から呼ばれる 1 サイクル実行スクリプト。
set -euo pipefail

# 二重起動防止: entrypoint の初回即時実行と cron tick、あるいは手動 docker exec が
# 重なると Slack 通知が複数回飛び、同じ issue で並行 PR が立ちうる。flock で
# 1サイクル1ロックに固定し、ロック取得失敗時は静かに skip する (cron が次 tick で拾う)。
LOCK=/var/run/knowl-cycle.lock
exec 9>"${LOCK}"
if ! flock -n 9; then
  echo "[knowl] another cycle is running; skip"
  exit 0
fi

# 機密 env を 0600 のファイルから読み込む (cron は親プロセスの env を継承しないため)。
# /etc/cron.d/knowl に平文で書くと cat 一発で漏れるのを避ける目的。
SECRETS_ENV=/etc/knowl/secrets.env
if [ -r "${SECRETS_ENV}" ]; then
  set -a
  # shellcheck disable=SC1090
  . "${SECRETS_ENV}"
  set +a
fi

CONFIG="${KNOWL_CONFIG:-/etc/knowl/config.yaml}"
CREDENTIALS="${KNOWL_CREDENTIALS:-/root/.claude/.credentials.json}"

cd /opt/knowl
exec uv run knowl run-once \
  --config "${CONFIG}" \
  --credentials "${CREDENTIALS}"
