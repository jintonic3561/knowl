"""knowl.tasks のテスト."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from knowl.claude_runner import ClaudeResult
from knowl.config import AppConfig, ContainerConfig, RepoConfig, TemplatesConfig
from knowl.github_client import IssueRef
from knowl.prioritize import PriorityDecision, TaskKind
from knowl.tasks import (
    TaskExecutionError,
    TaskOutcome,
    extract_final_json,
    render_template,
    run_task,
)


def issue() -> IssueRef:
    return IssueRef(
        repo="acme/widgets",
        number=12,
        title="Fix login",
        body="Login fails when ...",
        labels=("bug",),
        url="https://github.com/acme/widgets/issues/12",
        updated_at="2026-06-01T00:00:00Z",
    )


def repo() -> RepoConfig:
    return RepoConfig.model_validate(
        {
            "name": "acme/widgets",
            "container": {"kind": "docker", "name": "widgets-dev"},
        }
    )


def write_templates(tmp_path: Path) -> TemplatesConfig:
    impl = tmp_path / "impl.md"
    inv = tmp_path / "inv.md"
    impl.write_text("IMPL repo={repo} num={issue_number} title={issue_title}", encoding="utf-8")
    inv.write_text("INV repo={repo} num={issue_number}", encoding="utf-8")
    return TemplatesConfig(implementation=impl, investigation=inv)


def app_cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        templates=write_templates(tmp_path),
        repositories=[repo()],
    )


def test_render_template_fills_placeholders(tmp_path: Path) -> None:
    impl_path = tmp_path / "impl.md"
    impl_path.write_text(
        "issue {issue_number} on {repo}: {issue_title}\nbody: {issue_body}",
        encoding="utf-8",
    )
    rendered = render_template(impl_path, issue())
    assert "issue 12 on acme/widgets: Fix login" in rendered
    assert "body: Login fails when ..." in rendered


def test_extract_final_json_picks_last_object() -> None:
    text = dedent(
        """
        thinking...
        {"action": "pr-opened", "pr_url": "https://example/pr/1", "summary": "x", "followups": []}
        """
    )
    obj = extract_final_json(text)
    assert obj["action"] == "pr-opened"


def test_extract_final_json_handles_codefence() -> None:
    text = "stuff\n```json\n{\"action\": \"commented\", \"summary\": \"s\", \"followups\": []}\n```\n"
    obj = extract_final_json(text)
    assert obj["action"] == "commented"


def test_extract_final_json_raises_on_missing() -> None:
    with pytest.raises(TaskExecutionError):
        extract_final_json("no json here")


def test_run_task_implementation_records_outcome(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    decision = PriorityDecision(
        repo="acme/widgets", number=12, kind=TaskKind.IMPLEMENTATION, reason="bug"
    )

    captured: dict[str, object] = {}

    def runner(
        container: ContainerConfig,
        prompt: str,
        *,
        workdir: str,
        model: str,
    ) -> ClaudeResult:
        captured["prompt"] = prompt
        captured["container"] = container.name
        captured["workdir"] = workdir
        captured["model"] = model
        return ClaudeResult(
            text='{"action": "pr-opened", "pr_url": "https://x/pr/1", '
            '"summary": "added", "followups": ["next"]}',
            payload={},
        )

    outcome = run_task(cfg, decision, issue(), runner=runner)

    assert isinstance(outcome, TaskOutcome)
    assert outcome.kind is TaskKind.IMPLEMENTATION
    assert outcome.action == "pr-opened"
    assert outcome.url == "https://x/pr/1"
    assert outcome.followups == ["next"]
    assert "IMPL repo=acme/widgets num=12 title=Fix login" in captured["prompt"]  # type: ignore[operator]
    assert captured["container"] == "widgets-dev"
    assert captured["model"] == cfg.model


def test_run_task_investigation(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    decision = PriorityDecision(
        repo="acme/widgets", number=12, kind=TaskKind.INVESTIGATION, reason="unclear"
    )

    def runner(
        container: ContainerConfig,
        prompt: str,
        *,
        workdir: str,
        model: str,
    ) -> ClaudeResult:
        assert prompt.startswith("INV ")
        return ClaudeResult(
            text='{"action": "commented", "comment_url": "https://x/c/1", '
            '"summary": "ok", "followups": []}',
            payload={},
        )

    outcome = run_task(cfg, decision, issue(), runner=runner)
    assert outcome.kind is TaskKind.INVESTIGATION
    assert outcome.action == "commented"
    assert outcome.url == "https://x/c/1"
    assert outcome.followups == []


def test_run_task_repo_lookup_fails(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    decision = PriorityDecision(
        repo="other/missing", number=1, kind=TaskKind.IMPLEMENTATION, reason="x"
    )

    def runner(*_a: object, **_kw: object) -> ClaudeResult:  # pragma: no cover
        raise AssertionError("should not be called")

    with pytest.raises(TaskExecutionError):
        run_task(cfg, decision, issue(), runner=runner)


def test_run_task_garbled_output_raises(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    decision = PriorityDecision(
        repo="acme/widgets", number=12, kind=TaskKind.IMPLEMENTATION, reason="x"
    )

    def runner(*_a: object, **_kw: object) -> ClaudeResult:
        return ClaudeResult(text="no json", payload={})

    with pytest.raises(TaskExecutionError):
        run_task(cfg, decision, issue(), runner=runner)
