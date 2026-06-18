# ゴール

対象リポジトリ `{repo}` の issue #{issue_number} に関する調査/分析を行い、結果を issue コメントとしてまとめる。クローズ判断はユーザに委ねる。

## issue 本文
- title: {issue_title}
- url: {issue_url}
- labels: {issue_labels}

{issue_body}

## 進め方

1. 問いの分解と既存コード・履歴・オリジナル issue ・関連 issue/PR の確認。
2. 仮説 → 根拠の列挙 → 反証検討。可能なら最小限の再現や検証コードで確認する(コミット不要、調査用に留める)。
3. 結論・残る不確実性・推奨する次アクションを整理。
4. 結果を Markdown でまとめ、 `gh issue comment {issue_number} --repo {repo}` で issue に投稿する。
5. 投稿後、`gh issue edit {issue_number} --repo {repo} --add-label knowl-investigated` で完了マーカーラベルを付ける(ラベルが存在しない場合は `gh label create knowl-investigated --repo {repo} --color ededed` で作成してから付与する)。これが付いていない調査タスクは次サイクル以降も再抽出されるため、no-op で終わる場合も含めて必ず付与する。
6. 後続タスクが必要なら GitHub issue を新規起票し、URL を最終出力に含める。

## 制約

- リポジトリ本体へのコミット/PR は作らない。
- 結果のレポートは、直接分析を行っていないユーザを想定してわかりやすく。

## 最終出力 (必ず単一の JSON で返す)

```json
{{
  "action": "commented" | "no-op",
  "comment_url": "<URL or null>",
  "summary": "<1-3 行のサマリ>",
  "followups": ["<新規 issue URL or 説明>", ...]
}}
```
