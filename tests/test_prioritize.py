"""knowl.prioritize のテスト."""

from __future__ import annotations

import pytest

from knowl.claude_runner import ClaudeResult
from knowl.github_client import IssueRef
from knowl.prioritize import (
    NoActionableIssue,
    PrioritizationError,
    PriorityDecision,
    TaskKind,
    build_prioritization_prompt,
    parse_priority_response,
    pick_priority,
)


def issue(repo: str = "acme/widgets", number: int = 1, title: str = "T") -> IssueRef:
    return IssueRef(
        repo=repo,
        number=number,
        title=title,
        body="body",
        labels=(),
        url=f"https://github.com/{repo}/issues/{number}",
        updated_at="2026-06-01T00:00:00Z",
    )


def test_build_prompt_contains_all_issues() -> None:
    prompt = build_prioritization_prompt(
        [issue(number=1, title="A"), issue(repo="acme/gizmos", number=7, title="B")]
    )
    assert "acme/widgets#1" in prompt
    assert "acme/gizmos#7" in prompt
    assert "A" in prompt
    assert "B" in prompt
    # JSON で返すよう指示
    assert "JSON" in prompt or "json" in prompt


def test_parse_priority_response_plain_json() -> None:
    text = '{"repo":"acme/widgets","number":12,"kind":"implementation","reason":"smallest"}'
    decision = parse_priority_response(text)
    assert decision == PriorityDecision(
        repo="acme/widgets",
        number=12,
        kind=TaskKind.IMPLEMENTATION,
        reason="smallest",
    )


def test_parse_priority_response_with_code_fence() -> None:
    text = """Here is my pick:
```json
{"repo":"acme/widgets","number":3,"kind":"investigation","reason":"unclear"}
```
done.
"""
    decision = parse_priority_response(text)
    assert isinstance(decision, PriorityDecision)
    assert decision.kind is TaskKind.INVESTIGATION
    assert decision.number == 3


def test_parse_priority_response_invalid() -> None:
    with pytest.raises(PrioritizationError):
        parse_priority_response("nope")


def test_parse_priority_response_unknown_kind() -> None:
    text = '{"repo":"a/b","number":1,"kind":"banana","reason":"x"}'
    with pytest.raises(PrioritizationError):
        parse_priority_response(text)


def test_pick_priority_uses_runner_and_matches_issue() -> None:
    issues = [issue(number=1), issue(number=2, title="bug")]

    def runner(prompt: str, *, model: str) -> ClaudeResult:
        assert "acme/widgets#1" in prompt
        return ClaudeResult(
            text='{"repo":"acme/widgets","number":2,"kind":"implementation","reason":"bug"}',
            payload={},
        )

    result = pick_priority(issues, runner=runner, model="claude-opus-4-7")
    assert isinstance(result, tuple)
    decision, picked = result
    assert decision.number == 2
    assert picked.number == 2
    assert picked.title == "bug"


def test_pick_priority_unknown_issue_raises() -> None:
    issues = [issue(number=1)]

    def runner(prompt: str, *, model: str) -> ClaudeResult:
        return ClaudeResult(
            text='{"repo":"acme/widgets","number":99,"kind":"implementation","reason":"x"}',
            payload={},
        )

    with pytest.raises(PrioritizationError):
        pick_priority(issues, runner=runner, model="claude-opus-4-7")


def test_pick_priority_empty_issues_raises() -> None:
    def runner(prompt: str, *, model: str) -> ClaudeResult:  # pragma: no cover
        raise AssertionError("should not be called")

    with pytest.raises(PrioritizationError):
        pick_priority([], runner=runner, model="claude-opus-4-7")


def test_build_prompt_offers_no_actionable_option() -> None:
    # プロンプトに「全件 review/TODO 待ちなら no-op を返してよい」旨が含まれること。
    prompt = build_prioritization_prompt([issue()])
    assert "actionable" in prompt
    assert "false" in prompt


def test_parse_priority_response_actionable_false_returns_idle() -> None:
    text = '{"actionable": false, "reason": "all blocked on review"}'
    result = parse_priority_response(text)
    assert isinstance(result, NoActionableIssue)
    assert "review" in result.reason


def test_parse_priority_response_actionable_false_default_reason() -> None:
    text = '{"actionable": false}'
    result = parse_priority_response(text)
    assert isinstance(result, NoActionableIssue)
    assert result.reason  # 空でも何らかのフォールバック


def test_pick_priority_returns_no_actionable() -> None:
    issues = [issue(number=1)]

    def runner(prompt: str, *, model: str) -> ClaudeResult:
        return ClaudeResult(
            text='{"actionable": false, "reason": "all waiting for review"}',
            payload={},
        )

    result = pick_priority(issues, runner=runner, model="claude-opus-4-7")
    assert isinstance(result, NoActionableIssue)
    assert "review" in result.reason
