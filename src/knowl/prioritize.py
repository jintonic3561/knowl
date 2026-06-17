"""issue 優先度判定.

集約済 open issue リストを Claude に渡し、最優先 1 件と実装/調査の種別を返させる。
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from knowl._jsonutil import extract_first_json_object
from knowl.claude_runner import ClaudeResult, run_claude_local
from knowl.github_client import IssueRef


class TaskKind(StrEnum):
    IMPLEMENTATION = "implementation"
    INVESTIGATION = "investigation"


class PriorityDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    repo: str
    number: int
    kind: TaskKind
    reason: str = ""


class PrioritizationError(RuntimeError):
    """優先度判定の失敗."""


class ClaudeRunner(Protocol):
    def __call__(self, prompt: str, *, model: str) -> ClaudeResult: ...


_PROMPT_HEADER = (
    "You are triaging open GitHub issues across several repositories.\n"
    "Select the single highest-priority issue to work on next, and decide whether the "
    "task is `implementation` (code change → PR) or `investigation` (analysis → issue comment).\n"
    "Reply with ONLY a JSON object: "
    '{"repo": "<owner/repo>", "number": <int>, '
    '"kind": "implementation"|"investigation", "reason": "<short>"}.\n'
    "Do not wrap the JSON in commentary. The chosen (repo, number) MUST exactly match one of "
    "the candidates below.\n\n"
    "Candidates:\n"
)


def build_prioritization_prompt(issues: Sequence[IssueRef]) -> str:
    """優先度判定用プロンプトを生成する."""
    lines = [_PROMPT_HEADER]
    for issue in issues:
        labels = ",".join(issue.labels) if issue.labels else "-"
        snippet = issue.body.strip().splitlines()
        body_preview = " ".join(snippet)[:240] if snippet else ""
        lines.append(
            f"- {issue.repo}#{issue.number} [labels: {labels}] (updated {issue.updated_at})\n"
            f"  title: {issue.title}\n"
            f"  body: {body_preview}"
        )
    lines.append("\nReturn JSON now.")
    return "\n".join(lines)


def parse_priority_response(text: str) -> PriorityDecision:
    """Claude 返答テキストから JSON を抽出し PriorityDecision を組み立てる."""
    payload = extract_first_json_object(text)
    if payload is None:
        raise PrioritizationError("no JSON object found in priority response")
    try:
        return PriorityDecision.model_validate(payload)
    except ValidationError as exc:
        raise PrioritizationError(str(exc)) from exc


def _default_runner(prompt: str, *, model: str) -> ClaudeResult:
    return run_claude_local(prompt, model=model)


def pick_priority(
    issues: Sequence[IssueRef],
    *,
    runner: ClaudeRunner | None = None,
    model: str,
) -> tuple[PriorityDecision, IssueRef]:
    """issue 群から最優先 1 件を選び、Decision とマッチした IssueRef を返す."""
    if not issues:
        raise PrioritizationError("no issues to prioritize")
    runner_fn: ClaudeRunner = runner or _default_runner
    prompt = build_prioritization_prompt(issues)
    result = runner_fn(prompt, model=model)
    decision = parse_priority_response(result.text)
    for issue in issues:
        if issue.repo == decision.repo and issue.number == decision.number:
            return decision, issue
    raise PrioritizationError(
        f"prioritized issue {decision.repo}#{decision.number} not found in candidate list"
    )
