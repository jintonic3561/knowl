# Knowl

複数の自リポジトリの GitHub issue を、Claude Code の使用量に余裕がある時間帯に自動消化する小さなオーケストレータ。Pro / Max subscription の OAuth トークンを使い、5h ローリング枠と週次枠の残量を見てゲート判定し、登録リポジトリの open issue から最優先 1 件を Claude に判定させ、対応するローカルの作業コンテナで `claude -p` を起動して作業を行う。

## ワークフロー

| No | 内容 | 実装 |
| --- | --- | --- |
| 1 | リポジトリ登録 / container 自動起動 | `knowl.config`, `knowl.container` |
| 2 | Claude usage API 取得 (5h / 週次) | `knowl.usage` |
| 3 | 起動ゲート判定 (デフォルト 30% / 10%) | `knowl.gate` |
| 4 | 全リポ open issue 収集 + Claude 優先度判定 | `knowl.github_client`, `knowl.prioritize` |
| 5 | 実装タスク → PR / 自動 merge (`knowl-implementation` ラベルで明示可) | `templates/implementation.md`, `knowl.tasks` |
| 6 | 調査タスク → issue コメント (`knowl-investigation` ラベルで明示可) | `templates/investigation.md`, `knowl.tasks` |
| 7 | 後続タスクの起票 | テンプレ内手順 + `knowl.tasks` |
| 8 | Slack サマリ通知 / limit アラート | `knowl.slack` |

## 使い方 (常時起動コンテナ運用)

```bash
# 1. 設定ファイルを用意
cp knowl.example.yaml knowl.yaml
$EDITOR knowl.yaml

# 2. ホスト側でClaude Code に予めログインしておく 
#    -> ~/.claude/.credentials.json が生成される

# 3. .envもしくは環境変数にgit認証トークン、(必要ならば) Slackトークンを格納

cat > .env <<'EOF'
GH_TOKEN="ghp_..."
SLACK_BOT_TOKEN="xoxb-..."
SLACK_CHANNEL="#通知したいチャンネル"
EOF

# 4. 起動 / 停止 / 単発実行 (Makefile 経由)
make start     # 監視開始 (build + 常駐コンテナ起動)
make stop      # 監視終了
make run-once  # 1 サイクルだけ ephemeral コンテナで実行

# ログ確認
make logs
```

`make` か `make help` でターゲット一覧を確認できる。中身は `docker compose -f docker/docker-compose.yml ...` の薄いラッパなので、素の docker compose を直接使っても同等に動く。

cron は `cron_interval_minutes` 設定 (デフォルト 60 分) に従って `knowl run-once` を起動する。ゲート判定で余裕がなければ no-op で次回まで待機する。

タスクタイプ (実装 / 調査) は issue に専用ラベルを付けると Claude を介さず決定する。`knowl-implementation` / `knowl-investigation` のどちらか一方を付けるとそのタイプで実行される。両方付いている / どちらも無い場合は従来通り Claude 判定にフォールバックする。各リポジトリで `gh label create knowl-implementation` / `gh label create knowl-investigation` で作成しておく。

make start 時は起動直後に 1 サイクル動く。

### OAuth トークンの自動 refresh (host 側 keepalive)

Claude Code の OAuth access token は概ね 8h で expire する。夜間も走らせる前提だと寝てる間に切れて翌朝まで no-op を続けるので、host 側の cron でトークンを保たせる仕組みを別途用意している。

```bash
make keepalive-start    # crontab に登録 (デフォルト */30 * * * *)
make keepalive-status   # 登録状況の確認
make keepalive-now      # 即時実行 (cron を待たない動作確認)
make keepalive-logs     # .logs/keepalive.log を tail
make keepalive-stop     # 登録解除
```

中身は `scripts/keepalive.sh` → `uv run knowl keepalive`。`~/.claude/.credentials.json` の `expiresAt` を見て、残り寿命が閾値 (デフォルト 2h) を切ったときだけ `claude -p ok` を叩く。これにより Claude Code CLI が refreshToken で access token を更新し、ファイルが書き換わる。コンテナ側は `~/.claude` を ro bind mount しているのでそのまま新しいトークンを読む (再ビルド・再起動不要)。

- refresh が走るのは閾値割れ時だけなので、API 消費は 1 日数回 (~$0.1 オーダ)。
- 周期や閾値は上書き可: `make keepalive-start KEEPALIVE_CRON='*/15 * * * *'`、`scripts/keepalive.sh --threshold-hours 1`。
- 動作ログは `.logs/keepalive.log`。

### 対象リポジトリ container 側で必要な前提

Knowl は `docker exec <target> claude -p ...` で対象リポジトリ container 内の Claude を起動するだけで、credentials の中継は一切しない。対象 container 側で:

- Claude Code がインストール済 + 認証済 (`~/.claude/.credentials.json` 相当が存在)
- `gh` がインストール済 + 認証済

Knowl は `claude -p` に既定で `--dangerously-skip-permissions` を付ける。これは「対象 container はサンドボックスである」前提に基づく。container の隔離設計が不十分な場合は、自前ラッパで `--allowed-tools` 制限などに置き換えること。

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
      #                       # devcontainer の remoteUser で claude を入れている場合等に指定。
      # exec_prefix: ["direnv", "exec", "."]
      #                       # argv の前に prepend する任意のラッパ (任意)。
      #                       # docker exec 非対話で direnv 等を発火させたい時に使う。
```

## 状況

開発状況は `status.html` を参照。

## ライセンス

Apache License 2.0. 詳細は `LICENSE` を参照。
