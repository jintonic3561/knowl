"""knowl.filters のテスト."""

from __future__ import annotations

from collections.abc import Callable

from knowl.filters import INVESTIGATED_LABEL, exclude_blocked_issues
from knowl.github_client import IssueRef


def test_exclude_blocked_issues_passes_clean_issues(
    make_issue: Callable[..., IssueRef],
) -> None:
    issues = [make_issue(number=1), make_issue(number=2)]
    assert exclude_blocked_issues(issues) == issues


def test_exclude_blocked_issues_drops_linked_pr(
    make_issue: Callable[..., IssueRef],
) -> None:
    clean = make_issue(number=1)
    linked = make_issue(number=2, linked_pr_count=1)
    assert exclude_blocked_issues([clean, linked]) == [clean]


def test_exclude_blocked_issues_drops_investigated_label(
    make_issue: Callable[..., IssueRef],
) -> None:
    clean = make_issue(number=1)
    done = make_issue(number=2, labels=(INVESTIGATED_LABEL,))
    assert exclude_blocked_issues([clean, done]) == [clean]


def test_exclude_blocked_issues_drops_both_conditions(
    make_issue: Callable[..., IssueRef],
) -> None:
    clean = make_issue(number=1)
    linked = make_issue(number=2, linked_pr_count=2)
    done = make_issue(number=3, labels=("other", INVESTIGATED_LABEL))
    both = make_issue(number=4, labels=(INVESTIGATED_LABEL,), linked_pr_count=1)
    result = exclude_blocked_issues([clean, linked, done, both])
    assert result == [clean]


def test_exclude_blocked_issues_returns_empty_when_all_blocked(
    make_issue: Callable[..., IssueRef],
) -> None:
    blocked = [
        make_issue(number=1, linked_pr_count=1),
        make_issue(number=2, labels=(INVESTIGATED_LABEL,)),
    ]
    assert exclude_blocked_issues(blocked) == []


def test_exclude_blocked_issues_empty_input() -> None:
    assert exclude_blocked_issues([]) == []
