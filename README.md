# Knowl

複数の自リポジトリの GitHub issue を、Claude Code の使用量に余裕がある時間帯に自動消化する小さなオーケストレータ。Pro / Max subscription の OAuth トークンを使い、5h ローリング枠と週次枠の残量を見てゲート判定し、登録リポジトリの open issue から最優先 1 件を Claude に判定させ、対応する作業コンテナで `claude -p` を起動して PR / コメントまで進める。

## 動く範囲（spec.md と対応）

| 要件 | 内容 | 実装 |
| --- | --- | --- |
| R1 | リポジトリ登録 / container 自動起動 | `knowl.config`, `knowl.container` |
| R2 | Claude usage API 取得 (5h / 週次) | `knowl.usage` |
| R3 | 起動ゲート判定 (デフォルト 30% / 10%) | `knowl.gate` |
| R4 | 全リポ open issue 収集 + Claude 優先度判定 | `knowl.github_client`, `knowl.prioritize` |
| R5 | 実装タスク → PR / 自動 merge | `templates/implementation.md`, `knowl.tasks` |
| R6 | 調査タスク → issue コメント | `templates/investigation.md`, `knowl.tasks` |
| R7 | 後続タスクの起票 | テンプレ内手順 + `knowl.tasks` |
| R8 | Slack サマリ通知 / limit アラート | `knowl.slack` |

## 使い方 (常時起動コンテナ運用)

```bash
# 1. 設定ファイルを用意
cp knowl.example.yaml knowl.yaml
$EDITOR knowl.yaml

# 2. Claude Code に予めログインしておく (ホスト側で)
#    -> ~/.claude/.credentials.json が生成される

# 3. Slack 連携をしたい場合は .env を用意 (gitignore 済)
cat > .env <<'EOF'
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL=#knowl
EOF

# 4. 起動
docker compose -f docker/docker-compose.yml --env-file .env up -d --build

# ログ確認
docker logs -f knowl
```

cron は `cron_interval_minutes` 設定 (デフォルト 60 分) に従って `knowl run-once` を起動する。ゲート判定で余裕がなければ no-op で次回まで待機する。

### 対象リポジトリ container 側で必要な前提

Knowl は `docker exec <target> claude -p ...` で対象リポジトリ container 内の Claude を起動するだけで、credentials の中継は一切しない。対象 container 側で:

- Claude Code がインストール済 + 認証済 (`~/.claude/.credentials.json` 相当が存在)
- `gh` がインストール済 + 認証済 (または `GH_TOKEN` を環境変数で渡す)
- リポジトリ作業ディレクトリが workdir に bind マウントされており `git push` が可能

Knowl は `claude -p` に既定で `--dangerously-skip-permissions` を付ける。これは「対象 container はサンドボックスである」前提に基づく。container の隔離設計が不十分な場合は、自前ラッパで `--allowed-tools` 制限などに置き換えること。

devcontainer は root 以外 (`vscode` / `node` / `ubuntu` など) をメインユーザにしているのが普通で、`claude` も大抵そのユーザの home 配下 (`~/.local/bin/claude` 等) にインストールされる。`docker exec` は既定で root + 非ログインシェルで動くため、`~/.local/bin` が PATH に乗らず `claude: executable file not found in $PATH` で失敗する。`container.user` を設定すれば、Knowl は `docker exec --user <user> ... bash -lc <argv>` でログインシェル経由で起動するので、対象 user の `.bashrc` / `.profile` が読み込まれて PATH が通る (`bash` が入っている前提)。

また `direnv` のようなシェル hook 型 env ローダ (`PROMPT_COMMAND` で発火) は `docker exec` の非対話実行では発火しないため、`.envrc` の `GH_TOKEN` / `SLACK_BOT_TOKEN` 等が target container 内に流れない。`container.exec_prefix` に `["direnv", "exec", "."]` のような明示的な exec ラッパを設定すると、argv の前に prepend されて env が流れる。`["mise", "exec", "--"]` のように argv を透過渡しするラッパなら同様に使える (`nix-shell --run "<cmd>"` 型のように単一シェル文字列を要求するラッパは prepend では正しく動かない)。

## ローカル開発

```bash
uv sync
uv run ruff check
uv run mypy
uv run pytest
uv run knowl check-config --config knowl.example.yaml
```

## 設定スキーマ (抜粋)

```yaml
model: claude-opus-4-7        # 既定。Claude モデル ID。
cron_interval_minutes: 60     # cron 周期 (分)。
thresholds:
  session_remaining_pct: 30   # 5h 枠の最低残量 (%)
  weekly_remaining_pct: 10    # 週次枠の最低残量 (%)
slack:
  channel: "#knowl"           # SLACK_CHANNEL 環境変数で上書き可
templates:
  implementation: templates/implementation.md
  investigation: templates/investigation.md
repositories:
  - name: owner/repo
    container:
      kind: docker            # docker | devcontainer (どちらも docker exec で扱う)
      name: container-name
      workdir: /workspace
      # user: vscode          # docker exec --user に渡す (任意)。
      #                       # devcontainer の remoteUser で claude を入れている場合に指定。
      # exec_prefix: ["direnv", "exec", "."]
      #                       # argv の前に prepend する任意のラッパ (任意)。
      #                       # docker exec 非対話で direnv 等を発火させたい時に使う。
```

## 状況

開発状況は `status.html` を参照(これ一枚で現在の充足状況・モジュール状態・運用想定・直近 TODO がわかる)。

## ライセンス

Apache License 2.0. 詳細は `LICENSE` を参照。
