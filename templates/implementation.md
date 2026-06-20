# ゴール

対象リポジトリ `{repo}` の issue #{issue_number} を実装し、PR を作成する。

## issue 本文
- title: {issue_title}
- url: {issue_url}
- labels: {issue_labels}

{issue_body}

## ブランチ運用 (必須)

**着手時**:
1. `git fetch origin` した上で、必ず `origin/main` から PR ブランチを切る (古いローカル `main` の HEAD からは切らない)。
2. `git status --porcelain` で未コミット変更の有無を確認する。
   - **未コミット変更あり**: `git stash push --include-untracked` で退避し、退避前のブランチ名を控える。退避した差分と本 issue で触る範囲 (同一ファイル / 同一関数 / 同一設定) が衝突しそうな場合は、本 PR は `5b-needs-review` 固定でオートマージ禁止 (PR 本文・コメントで明示)。
   - **未コミット変更なし**: そのまま進める。

**完了時 (push 後、merge 可否に関わらず必ず実施)**:
- 着手時に **未コミット変更を退避した** 場合: `git checkout <退避前ブランチ>` で戻し、`git stash pop` で元の状態を復元して終了する。ローカル `main` は触らない。`git stash pop` がコンフリクトした場合は自動解決せず、作業ツリーをその状態で残したまま終了し PR コメントで報告する。
- 着手時に **未コミット変更がなかった** 場合: `git checkout main && git pull --ff-only origin main` でローカル `main` をリモートと同期して終了する。`5b-needs-review` で PR を残す場合でも `main` にチェックアウトする。`--ff-only` が失敗する (ローカル `main` が分岐している) 場合は強制同期せずそのまま終了し PR コメントで報告する。

## 進め方

1. 現状把握: 既存コード・テスト・オリジナル issue ・関連 issue/PR を確認。
   - **この issue に `knowl-reviewed` ラベルが付いている場合 (人間レビュー通過後マージ依頼)**:
     1. `gh pr list --repo {repo} --state open --search 'in:body "Closes #{issue_number}"' --json number,headRefName,mergeStateStatus,statusCheckRollup` で紐づく open PR を探す。複数あれば PR 番号最大の 1 件を採用 (前回 5b で残した最新 PR を最優先)。 0 件なら no-op で終了し、 issue に「マージ対象 PR が見つからない」旨をコメントしてから `gh issue edit {issue_number} --repo {repo} --remove-label knowl-reviewed --add-label knowl-needs-review` でラベルを needs-review に戻す (放置すると次サイクルで同じ no-op を繰り返す)。
     2. 対象 PR の CI が green かつ mergeable であれば、 `gh pr merge --repo {repo} --squash --delete-branch <PR番号>` で squash merge して action: `merged` で終了。 merge 完了で issue は自動 close されラベルも消えるので追加操作不要。
     3. CI red / コンフリクト / pending の場合は **`gh issue edit {issue_number} --repo {repo} --remove-label knowl-reviewed --add-label knowl-needs-review` でラベルを needs-review に戻し**、 PR にブロック理由をコメントして no-op で終了。 こうしないと次サイクルでも同じ判定を繰り返してプロンプトを浪費する。
     4. ここから先 (設計・実装) には進まない。 新規実装は走らせない。
2. 設計: 最小コスト・保守性重視・必要十分な範囲で方針を決める。範囲拡大は禁止。
3. 実装 + テスト + レビューループ。
  - 原則 TDD。
  - レビューは原則としてコンテキスト独立なサブエージェントもしくは他のエージェントツールを利用し、本質的な事項について収束するまでループする。
  - リポジトリ規定の品質維持ゲートを必ず通す。
4. コミット → push (ブランチは前述「ブランチ運用」の手順で着手時に切ったものを使う)。
5. PR を作成し、本文に次を必ず含める:
   - 解決した issue へのリンク: `Closes #{issue_number}`
   - 変更の簡潔な概要
   - **次のいずれか 1 行を明示**:
     - `5a-auto-merge: 自動マージ可` — 自明な変更で、回帰リスク・設計判断ともに最小と確信できる場合のみ。
     - `5b-needs-review: 人間レビュー必要` — 設計判断・ユーザ合意が必要、もしくは判断に迷う場合。理由・選択肢・推奨・レビュー箇所を PR コメントへ。
6. `5a-auto-merge` の場合、CI が green になり次第 squash merge を実施し、マージ済みリモートブランチを削除する。`5b-needs-review` の場合は **PR を push できた直後に `gh issue edit {issue_number} --repo {repo} --add-label knowl-needs-review` でレビュー中ラベルを必ず付与する** (ラベル未作成なら `gh label create knowl-needs-review --repo {repo} --color fbca04` で作成してから付与)。 付け忘れると次サイクルで同じ issue が再抽出されて二重実装になるため、 PR コメント等の付帯作業より先に付与する。 人間レビュー通過後はユーザが `knowl-reviewed` ラベルに貼り替え、次回サイクルで上記 1. のマージフローに入る。 ローカルの後始末はいずれの場合も「ブランチ運用」の完了時手順に従う (ここで個別に `git pull` 等は実施しない)。
7. 後続タスクが必要なら GitHub issue を新規起票し、URL を最終出力に含める。

## 制約

- 判断材料が足りないときは "5b-needs-review" を選び、論点を PR コメントにまとめる。
- そもそも実装に移るべきでないと判断した場合は、issue にコメントして no-op で終了。
- 既存の規約・style に従う。

## 最終出力 (必ず単一の JSON で返す)

```json
{{
  "action": "pr-opened" | "merged" | "no-op",
  "pr_url": "<URL or null>",
  "summary": "<1-3 行のサマリ>",
  "followups": ["<新規 issue URL or 説明>", ...]
}}
```
