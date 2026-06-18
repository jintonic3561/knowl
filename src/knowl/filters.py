"""作業候補 issue のフィルタリング.

優先度判定の前段で、明らかに「いま着手すべきでない」issue を機械的に除外する。

- closing-keyword で紐づく PR が 1 件でもあれば、レビュー中 or マージ済として除外。
- 調査タスクで完了コメントを残した issue は ``knowl-investigated`` ラベルが付くので除外。
"""

from __future__ import annotations

from collections.abc import Iterable

from knowl.github_client import IssueRef

INVESTIGATED_LABEL = "knowl-investigated"


def exclude_blocked_issues(issues: Iterable[IssueRef]) -> list[IssueRef]:
    """作業候補として進めるべきでない issue を除外する."""
    return [
        issue
        for issue in issues
        if issue.linked_pr_count == 0 and INVESTIGATED_LABEL not in issue.labels
    ]
