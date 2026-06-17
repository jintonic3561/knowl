#!/usr/bin/env bash
# cron から呼ばれる 1 サイクル実行スクリプト。
set -euo pipefail

CONFIG="${KNOWL_CONFIG:-/etc/knowl/config.yaml}"
CREDENTIALS="${KNOWL_CREDENTIALS:-/root/.claude/.credentials.json}"

cd /opt/knowl
exec uv run knowl run-once \
  --config "${CONFIG}" \
  --credentials "${CREDENTIALS}"
