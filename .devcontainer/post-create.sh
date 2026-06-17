#!/usr/bin/env bash
# devcontainer postCreate の全体セットアップ。
set -euo pipefail

bash .devcontainer/setup-direnv.sh

uv sync
bash -lc 'set -euo pipefail; curl -fsSL https://claude.ai/install.sh | bash'
