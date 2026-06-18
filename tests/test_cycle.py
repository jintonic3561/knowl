"""knowl.cycle のテスト."""

from __future__ import annotations

from pathlib import Path

import pytest

from knowl.claude_runner import ClaudeError
from knowl.config import AppConfig, RepoConfig, TemplatesConfig
from knowl.cycle import CycleResult, run_cycle
from knowl.github_client import IssueRef
from knowl.prioritize import PriorityDecision, TaskKind
from knowl.tasks import TaskOutcome
from knowl.usage import UsageSnapshot


def repo() -> RepoConfig:
    return RepoConfig.model_validate(
        {"name": "acme/widgets", "container": {"kind": "docker", "name": "c"}}
    )


def app_cfg(tmp_path: Path) -> AppConfig:
    impl = tmp_path / "impl.md"
    impl.write_text("p", encoding="utf-8")
    inv = tmp_path / "inv.md"
    inv.write_text("p", encoding="utf-8")
    return AppConfig(
        templates=TemplatesConfig(implementation=impl, investigation=inv),
        repositories=[repo()],
    )


def make_issue(repo_name: str = "acme/widgets", number: int = 1) -> IssueRef:
    return IssueRef(
        repo=repo_name,
        number=number,
        title="t",
        body="b",
        labels=(),
        url=f"https://github.com/{repo_name}/issues/{number}",
        updated_at="2026-06-01T00:00:00Z",
    )


def test_run_cycle_no_op_when_gate_blocks(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)

    notifications: list[str] = []

    result = run_cycle(
        cfg,
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=10, weekly_remaining_pct=50
        ),
        list_issues=lambda repos: [make_issue()],
        prioritize=lambda issues, **_: (
            PriorityDecision(
                repo="acme/widgets", number=1, kind=TaskKind.IMPLEMENTATION, reason="x"
            ),
            issues[0],
        ),
        run_task=lambda *a, **kw: TaskOutcome(
            kind=TaskKind.IMPLEMENTATION, action="x", summary="", url=None, followups=[]
        ),
        notify=notifications.append,
        ensure_container=lambda _: None,
    )

    assert isinstance(result, CycleResult)
    assert result.executed is False
    assert "session below" in result.reason
    assert notifications == []  # ゲートブロックは通知しない


def test_run_cycle_no_op_when_no_issues(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    posted: list[str] = []
    result = run_cycle(
        cfg,
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=80, weekly_remaining_pct=80
        ),
        list_issues=lambda repos: [],
        prioritize=lambda issues, **_: (  # pragma: no cover
            PriorityDecision(
                repo="x", number=1, kind=TaskKind.IMPLEMENTATION, reason=""
            ),
            issues[0],
        ),
        run_task=lambda *a, **kw: TaskOutcome(
            kind=TaskKind.IMPLEMENTATION, action="x", summary="", url=None, followups=[]
        ),
        notify=posted.append,
        ensure_container=lambda _: None,
    )
    assert result.executed is False
    assert "no open issues" in result.reason
    # 「進めるべき issue がない」旨を Slack に流す
    assert len(posted) == 1
    assert "issue" in posted[0].lower() or "進めるべき" in posted[0]


def test_run_cycle_no_op_when_no_actionable_issue(tmp_path: Path) -> None:
    from knowl.prioritize import NoActionableIssue

    cfg = app_cfg(tmp_path)
    posted: list[str] = []

    def prioritize(
        issues: list[IssueRef], *, model: str
    ) -> tuple[PriorityDecision, IssueRef] | NoActionableIssue:
        return NoActionableIssue(reason="all waiting for human review")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=80, weekly_remaining_pct=80
        ),
        list_issues=lambda repos: [make_issue()],
        prioritize=prioritize,
        run_task=lambda *a, **kw: pytest.fail(  # pragma: no cover
            "run_task must not be invoked when no actionable issue"
        ),
        notify=posted.append,
        ensure_container=lambda _: pytest.fail(  # pragma: no cover
            "container must not be ensured when no actionable issue"
        ),
    )
    assert result.executed is False
    assert "no actionable issue" in result.reason.lower()
    assert "all waiting for human review" in result.reason
    assert len(posted) == 1
    assert "waiting" in posted[0].lower() or "review" in posted[0].lower()


def test_run_cycle_happy_path(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    posted: list[str] = []
    ensured: list[str] = []

    fixture_issue = make_issue(number=42)
    fixture_decision = PriorityDecision(
        repo="acme/widgets", number=42, kind=TaskKind.IMPLEMENTATION, reason="bug"
    )

    def prioritize(
        issues: list[IssueRef], *, model: str
    ) -> tuple[PriorityDecision, IssueRef]:
        assert issues[0].number == 42
        assert model == cfg.model
        return fixture_decision, fixture_issue

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        assert decision is fixture_decision
        assert issue is fixture_issue
        # 開始通知は run_task より前に 1 件流れているはず (文言には依存しない)
        assert len(posted) == 1
        return TaskOutcome(
            kind=TaskKind.IMPLEMENTATION,
            action="pr-opened",
            summary="opened pr",
            url="https://pr/1",
            followups=["next"],
        )

    def ensure_container(c: object) -> None:
        from knowl.config import ContainerConfig

        assert isinstance(c, ContainerConfig)
        ensured.append(c.name)

    result = run_cycle(
        cfg,
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=80, weekly_remaining_pct=80
        ),
        list_issues=lambda repos: [fixture_issue],
        prioritize=prioritize,
        run_task=run_task,
        notify=posted.append,
        ensure_container=ensure_container,
    )

    assert result.executed is True
    assert result.outcome is not None
    assert result.outcome.action == "pr-opened"
    assert ensured == ["c"], "container must be ensured exactly once"
    # 通知は [開始通知, サマリ] の順
    assert len(posted) == 2
    assert "acme/widgets" in posted[0]
    assert "#42" in posted[0]
    assert "開始" in posted[0]
    assert "pr-opened" in posted[1]
    assert "next" in posted[1]


def test_run_cycle_container_start_failure_notifies(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    posted: list[str] = []

    def ensure_container(_c: object) -> None:
        from knowl.container import ContainerError

        raise ContainerError("docker daemon unavailable")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=80, weekly_remaining_pct=80
        ),
        list_issues=lambda repos: [make_issue()],
        prioritize=lambda issues, **_: (
            PriorityDecision(
                repo="acme/widgets", number=1, kind=TaskKind.IMPLEMENTATION, reason=""
            ),
            issues[0],
        ),
        run_task=lambda *a, **kw: pytest.fail(
            "run_task must not be invoked when container start fails"
        ),
        notify=posted.append,
        ensure_container=ensure_container,
    )

    assert result.executed is False
    assert "container start failed" in result.reason
    assert any("container start" in p for p in posted)
    # 開始通知は流さない
    assert not any("開始" in p for p in posted)


def test_run_cycle_handles_limit_alert(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    posted: list[str] = []

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        raise ClaudeError("weekly limit reached", limit_reached=True)

    result = run_cycle(
        cfg,
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=80, weekly_remaining_pct=80
        ),
        list_issues=lambda repos: [make_issue()],
        prioritize=lambda issues, **_: (
            PriorityDecision(
                repo="acme/widgets", number=1, kind=TaskKind.IMPLEMENTATION, reason=""
            ),
            issues[0],
        ),
        run_task=run_task,
        notify=posted.append,
        ensure_container=lambda _: None,
    )

    assert result.executed is False
    assert "limit" in result.reason.lower()
    assert any("limit" in p.lower() for p in posted)


def test_run_cycle_usage_error_notifies(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    posted: list[str] = []

    def fetch_usage() -> UsageSnapshot:
        from knowl.usage import UsageError

        raise UsageError("401 unauthorized")

    result = run_cycle(
        cfg,
        fetch_usage=fetch_usage,
        list_issues=lambda repos: [make_issue()],
        prioritize=lambda issues, **_: (
            PriorityDecision(
                repo="acme/widgets", number=1, kind=TaskKind.IMPLEMENTATION, reason=""
            ),
            issues[0],
        ),
        run_task=lambda *a, **kw: TaskOutcome(
            kind=TaskKind.IMPLEMENTATION, action="x", summary="", url=None, followups=[]
        ),
        notify=posted.append,
        ensure_container=lambda _: None,
    )
    assert result.executed is False
    assert "usage fetch failed" in result.reason
    assert any("usage fetch" in p for p in posted)


def test_run_cycle_github_error_notifies(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    posted: list[str] = []

    def list_issues(_repos: object) -> list[IssueRef]:
        from knowl.github_client import GitHubError

        raise GitHubError("gh auth required")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=80, weekly_remaining_pct=80
        ),
        list_issues=list_issues,
        prioritize=lambda issues, **_: (
            PriorityDecision(
                repo="acme/widgets", number=1, kind=TaskKind.IMPLEMENTATION, reason=""
            ),
            issues[0],
        ),
        run_task=lambda *a, **kw: TaskOutcome(
            kind=TaskKind.IMPLEMENTATION, action="x", summary="", url=None, followups=[]
        ),
        notify=posted.append,
        ensure_container=lambda _: None,
    )
    assert result.executed is False
    assert "issue collection failed" in result.reason
    assert any("issue collection" in p for p in posted)


def test_run_cycle_prioritization_error_notifies(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    posted: list[str] = []

    def prioritize(
        issues: list[IssueRef], *, model: str
    ) -> tuple[PriorityDecision, IssueRef]:
        from knowl.prioritize import PrioritizationError

        raise PrioritizationError("bad response")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=80, weekly_remaining_pct=80
        ),
        list_issues=lambda repos: [make_issue()],
        prioritize=prioritize,
        run_task=lambda *a, **kw: TaskOutcome(
            kind=TaskKind.IMPLEMENTATION, action="x", summary="", url=None, followups=[]
        ),
        notify=posted.append,
        ensure_container=lambda _: None,
    )
    assert result.executed is False
    assert "prioritization failed" in result.reason
    assert any("prioritization" in p for p in posted)


def test_run_cycle_token_expired_uses_dedicated_message(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    posted: list[str] = []

    def fetch_usage() -> UsageSnapshot:
        from knowl.usage import TokenExpiredError

        raise TokenExpiredError("token expired")

    result = run_cycle(
        cfg,
        fetch_usage=fetch_usage,
        list_issues=lambda repos: [make_issue()],
        prioritize=lambda issues, **_: (
            PriorityDecision(
                repo="acme/widgets", number=1, kind=TaskKind.IMPLEMENTATION, reason=""
            ),
            issues[0],
        ),
        run_task=lambda *a, **kw: TaskOutcome(
            kind=TaskKind.IMPLEMENTATION, action="x", summary="", url=None, followups=[]
        ),
        notify=posted.append,
        ensure_container=lambda _: None,
    )
    assert result.executed is False
    assert "oauth token expired" in result.reason.lower()
    assert any("🔐" in p and "expired" in p.lower() for p in posted)


def test_run_cycle_container_error_notifies(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    posted: list[str] = []

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        from knowl.container import ContainerError

        raise ContainerError("docker daemon unavailable")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=80, weekly_remaining_pct=80
        ),
        list_issues=lambda repos: [make_issue()],
        prioritize=lambda issues, **_: (
            PriorityDecision(
                repo="acme/widgets", number=1, kind=TaskKind.IMPLEMENTATION, reason=""
            ),
            issues[0],
        ),
        run_task=run_task,
        notify=posted.append,
        ensure_container=lambda _: None,
    )
    assert result.executed is False
    assert "container operation failed" in result.reason
    assert any("container" in p for p in posted)


def test_run_cycle_task_execution_error_notifies(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)
    posted: list[str] = []

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        from knowl.tasks import TaskExecutionError

        raise TaskExecutionError("bad output")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=80, weekly_remaining_pct=80
        ),
        list_issues=lambda repos: [make_issue()],
        prioritize=lambda issues, **_: (
            PriorityDecision(
                repo="acme/widgets", number=1, kind=TaskKind.IMPLEMENTATION, reason=""
            ),
            issues[0],
        ),
        run_task=run_task,
        notify=posted.append,
        ensure_container=lambda _: None,
    )
    assert result.executed is False
    assert "task execution failed" in result.reason
    assert any("task execution" in p for p in posted)


def test_run_cycle_propagates_unknown_error(tmp_path: Path) -> None:
    cfg = app_cfg(tmp_path)

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_cycle(
            cfg,
            fetch_usage=lambda: UsageSnapshot(
                session_remaining_pct=80, weekly_remaining_pct=80
            ),
            list_issues=lambda repos: [make_issue()],
            prioritize=lambda issues, **_: (
                PriorityDecision(
                    repo="acme/widgets", number=1, kind=TaskKind.IMPLEMENTATION, reason=""
                ),
                issues[0],
            ),
            run_task=run_task,
            notify=lambda _: None,
            ensure_container=lambda _: None,
        )
