#!/usr/bin/env bash
# direnv を導入し、このワークスペースの .envrc を有効化する。
set -euo pipefail

WORKSPACE_DIR="${1:-$(pwd)}"

if ! command -v direnv >/dev/null 2>&1; then
  # devcontainer は root 起動なので通常 sudo は不要だが、将来 remoteUser を変えても動くようガードする。
  if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
  elif command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    echo "direnv のインストールに root か sudo が必要" >&2
    exit 1
  fi
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y --no-install-recommends direnv
fi

ensure_hook() {
  local rc_file="$1"
  local hook_line="$2"

  mkdir -p "$(dirname "${rc_file}")"
  touch "${rc_file}"
  if ! grep -Fqx "${hook_line}" "${rc_file}"; then
    printf '\n%s\n' "${hook_line}" >>"${rc_file}"
  fi
}

ensure_hook "${HOME}/.bashrc" 'eval "$(direnv hook bash)"'
if command -v zsh >/dev/null 2>&1; then
  ensure_hook "${HOME}/.zshrc" 'eval "$(direnv hook zsh)"'
fi

if [ -f "${WORKSPACE_DIR}/.envrc" ]; then
  direnv allow "${WORKSPACE_DIR}"
fi
