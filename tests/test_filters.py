"""knowl.filters のテスト."""

from __future__ import annotations

from knowl.filters import INVESTIGATED_LABEL, exclude_blocked_issues
from knowl.github_client import IssueRef


def make_issue(
    *,
    number: int = 1,
    labels: tuple[str, ...] = (),
    linked_pr_count: int = 0,
) -> IssueRef:
    return IssueRef(
        repo="acme/widgets",
        number=number,
        title=f"t{number}",
        body="",
        labels=labels,
        url=f"https://github.com/acme/widgets/issues/{number}",
        updated_at="2026-06-01T00:00:00Z",
        linked_pr_count=linked_pr_count,
    )


def test_exclude_blocked_issues_passes_clean_issues() -> None:
    issues = [make_issue(number=1), make_issue(number=2)]
    assert exclude_blocked_issues(issues) == issues


def test_exclude_blocked_issues_drops_linked_pr() -> None:
    clean = make_issue(number=1)
    linked = make_issue(number=2, linked_pr_count=1)
    assert exclude_blocked_issues([clean, linked]) == [clean]


def test_exclude_blocked_issues_drops_investigated_label() -> None:
    clean = make_issue(number=1)
    done = make_issue(number=2, labels=(INVESTIGATED_LABEL,))
    assert exclude_blocked_issues([clean, done]) == [clean]


def test_exclude_blocked_issues_drops_both_conditions() -> None:
    clean = make_issue(number=1)
    linked = make_issue(number=2, linked_pr_count=2)
    done = make_issue(number=3, labels=("other", INVESTIGATED_LABEL))
    both = make_issue(number=4, labels=(INVESTIGATED_LABEL,), linked_pr_count=1)
    result = exclude_blocked_issues([clean, linked, done, both])
    assert result == [clean]


def test_exclude_blocked_issues_returns_empty_when_all_blocked() -> None:
    blocked = [
        make_issue(number=1, linked_pr_count=1),
        make_issue(number=2, labels=(INVESTIGATED_LABEL,)),
    ]
    assert exclude_blocked_issues(blocked) == []


def test_exclude_blocked_issues_empty_input() -> None:
    assert exclude_blocked_issues([]) == []
