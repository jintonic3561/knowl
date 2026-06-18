#!/usr/bin/env bash
# host cron から呼ぶラッパ。`knowl keepalive` を起動し、結果を .logs/keepalive.log に追記する。
# cron は HOME 以外ほぼ環境変数が無いので、PATH と HOME を明示する。
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# uv / claude が見える PATH を確保する。既存 PATH (shim 系 / Homebrew 等) は維持。
export HOME="${HOME:-/home/$(id -un)}"
export PATH="${HOME}/.local/bin:${PATH:-/usr/local/bin:/usr/bin:/bin}"

LOG_DIR="${REPO_DIR}/.logs"
LOG_FILE="${LOG_DIR}/keepalive.log"
LOG_MAX_BYTES=$((5 * 1024 * 1024))  # 5MB 超で .1 にローテ。世代は 1 つだけ持つ。

mkdir -p "${LOG_DIR}"
if [[ -f "${LOG_FILE}" ]] && (( $(stat -c %s "${LOG_FILE}") > LOG_MAX_BYTES )); then
  mv -f "${LOG_FILE}" "${LOG_FILE}.1"
fi

cd "${REPO_DIR}"

# 周期が短く設定された / claude が詰まった等で前回サイクルが残っている場合、
# 重ねて走らせず黙って降りる (cron はエラー扱いにしない)。
LOCK_FILE="${LOG_DIR}/keepalive.lock"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "--- $(date -Is) --- skip: another keepalive is still running" >> "${LOG_FILE}"
  exit 0
fi

# tee で標準出力にも流す。cron 起動時は端末が無いので tee の出力は捨てられ、
# `make keepalive-now` 等の手動実行では画面とログ双方に書ける。
{
  echo "--- $(date -Is) ---"
  uv run --quiet knowl keepalive "$@"
} 2>&1 | tee -a "${LOG_FILE}"
