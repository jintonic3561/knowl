#!/usr/bin/env bash
# devcontainer postCreate の全体セットアップ。
# uv/gh/claude などは docker/Dockerfile (base/dev stage) で導入済 -> ここでは扱わない。
set -euo pipefail

# bind mount の .git が root 所有のまま残ると git/gh が "dubious ownership" を出すため vscode に揃える。
# workspace 全体は WSL ホスト側のファイル所有権に波及しうるので対象を .git に絞る。
if [ "$(id -u)" -ne 0 ] && [ -d .git ] && [ "$(stat -c %u .git)" != "$(id -u)" ]; then
  sudo chown -R "$(id -u):$(id -g)" .git
fi

bash .devcontainer/setup-direnv.sh

uv sync
