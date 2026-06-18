#!/usr/bin/env bash
# devcontainer postCreate の全体セットアップ。
# uv/gh/claude などは docker/Dockerfile (base stage) で導入済 -> ここでは扱わない。
set -euo pipefail

bash .devcontainer/setup-direnv.sh

uv sync
