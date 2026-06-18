#!/usr/bin/env bash
# cron から呼ばれる 1 サイクル実行スクリプト。
set -euo pipefail

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
