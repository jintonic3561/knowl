# Knowl の常時起動コンテナ。
# - cron で uv run knowl run-once を毎時実行
# - docker.sock をマウントして登録リポジトリのコンテナを操作する想定 (docker-out-of-docker)
#
# マルチステージ構成:
#   base    : devcontainer と運用で共有する OS/ツール層 (uv, gh, claude, docker-cli)
#   runtime : base に knowl のソースと cron entrypoint を載せた本番イメージ (default target)
#
# .devcontainer/devcontainer.json は base ステージをビルドして使う。これにより
# 共通ツールの導入手順が 1 か所に集約され、dev と本番のドリフトを構造的に防ぐ。
FROM python:3.13-slim AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy

# 基本ツール + GitHub CLI 用 apt リポジトリ。
# docker-cli は runtime が docker-out-of-docker (host の /var/run/docker.sock を mount)
# で対象コンテナを操作するために必要。devcontainer 側は features の docker-in-docker
# が同じ CLI を上書きする形で併用するため、base 側に置いていても衝突しない。
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

# Claude Code CLI (script installer)。dev/本番でバージョンを揃えるためピン留めする。
# dev ステージで vscode ユーザーに再インストールするため ENV で派生に引き継ぐ。
ARG CLAUDE_CODE_VERSION=2.1.153
ENV CLAUDE_CODE_VERSION=${CLAUDE_CODE_VERSION}
RUN curl -fsSL https://claude.ai/install.sh | bash -s "${CLAUDE_CODE_VERSION}"

# uv (Astral)
# /root は 0700 で他ユーザーから辿れないため、symlink ではなくバイナリ実体を
# /usr/local/bin に移す。dev ステージの vscode ユーザーも同じ uv を使える。
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && (mv /root/.local/bin/uvx /usr/local/bin/uvx 2>/dev/null || true)

ENV PATH=/root/.local/bin:$PATH


# 本番イメージ。knowl のソースと cron entrypoint を base に載せる。
FROM base AS runtime

# UV_PROJECT_ENVIRONMENT は base に置かない: devcontainer 側は workspace
# (/workspaces/knowl) で uv sync して python.defaultInterpreterPath が指す
# .venv/bin/python を使うため、固定パスを base に焼くと dev の挙動が変わる。
ENV UV_PROJECT_ENVIRONMENT=/opt/knowl/.venv

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


# devcontainer 専用ステージ。
# claude --dangerously-skip-permissions は root で起動できないため、
# vscode ユーザー (uid 1000, sudo NOPASSWD) を作って claude を入れ直す。
# 本番 runtime は base のまま (root 動作) でドリフトしないよう、devcontainer 側だけここで派生する。
FROM base AS dev

RUN apt-get update \
    && apt-get install -y --no-install-recommends sudo \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 vscode \
    && useradd --uid 1000 --gid 1000 -m -s /bin/bash vscode \
    && echo "vscode ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode

USER vscode

# CLAUDE_CODE_VERSION は base から ENV 経由で継承される (base/runtime/dev で同一バージョン)。
RUN curl -fsSL https://claude.ai/install.sh | bash -s "${CLAUDE_CODE_VERSION}"

ENV PATH=/home/vscode/.local/bin:${PATH}
