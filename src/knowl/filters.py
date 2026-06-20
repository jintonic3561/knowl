"""作業候補 issue のフィルタリング.

優先度判定の前段で、明らかに「いま着手すべきでない」issue を機械的に除外する。

- ``knowl-needs-review`` ラベルが付いた issue はレビュー中として除外。
  実装タスクが PR を 5b-needs-review (人間レビュー要) で残すと付与され、
  人間レビュー完了後にユーザが ``knowl-reviewed`` に貼り替えることで
  再度選択候補に戻る (次回サイクルでマージ処理が走る)。
- 調査タスクで完了コメントを残した issue は ``knowl-investigated`` ラベルが付くので除外。
"""

from __future__ import annotations

from collections.abc import Iterable

from knowl.github_client import IssueRef

INVESTIGATED_LABEL = "knowl-investigated"
NEEDS_REVIEW_LABEL = "knowl-needs-review"
REVIEWED_LABEL = "knowl-reviewed"

_BLOCKING_LABELS = frozenset({INVESTIGATED_LABEL, NEEDS_REVIEW_LABEL})


def exclude_blocked_issues(issues: Iterable[IssueRef]) -> list[IssueRef]:
    """作業候補として進めるべきでない issue を除外する."""
    return [
        issue
        for issue in issues
        if _BLOCKING_LABELS.isdisjoint(issue.labels)
    ]
