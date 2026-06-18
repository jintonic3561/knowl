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
ARG CLAUDE_CODE_VERSION=2.1.153
RUN curl -fsSL https://claude.ai/install.sh | bash -s "${CLAUDE_CODE_VERSION}"

# uv (Astral)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv

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
