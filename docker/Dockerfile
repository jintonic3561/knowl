# Knowl の常時起動コンテナ。
# - cron で uv run knowl run-once を毎時実行
# - docker.sock をマウントして登録リポジトリのコンテナを操作する想定 (docker-out-of-docker)
FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/knowl/.venv

# 基本ツール + GitHub CLI 用 apt リポジトリ
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git cron tini docker-cli gnupg \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI (script installer)
RUN curl -fsSL https://claude.ai/install.sh | bash

# uv (Astral)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /opt/knowl
COPY pyproject.toml uv.lock* README.md /opt/knowl/
COPY src /opt/knowl/src
COPY templates /opt/knowl/templates

RUN uv sync --frozen || uv sync

# 設定・credentials は実行時にマウントする
ENV KNOWL_CONFIG=/etc/knowl/config.yaml \
    KNOWL_CREDENTIALS=/root/.claude/.credentials.json \
    PATH=/opt/knowl/.venv/bin:/root/.local/bin:$PATH

COPY docker/entrypoint.sh /usr/local/bin/knowl-entrypoint
COPY docker/run-cycle.sh /usr/local/bin/knowl-run-cycle
RUN chmod +x /usr/local/bin/knowl-entrypoint /usr/local/bin/knowl-run-cycle

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/knowl-entrypoint"]
