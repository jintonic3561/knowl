"""knowl.cycle のテスト."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from knowl.claude_runner import ClaudeError
from knowl.config import AppConfig
from knowl.cycle import CycleResult, run_cycle
from knowl.github_client import IssueRef
from knowl.prioritize import PriorityDecision, TaskKind
from knowl.tasks import TaskOutcome
from knowl.usage import UsageSnapshot


def test_run_cycle_no_op_when_gate_blocks(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
) -> None:
    cfg = app_cfg()

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


def test_run_cycle_no_op_when_no_issues(
    app_cfg: Callable[..., AppConfig], ok_snapshot: UsageSnapshot
) -> None:
    cfg = app_cfg()
    posted: list[str] = []
    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
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
    assert result.idle is True
    assert "no open issues" in result.reason
    # 「進めるべき issue がない」旨を Slack に流す
    assert len(posted) == 1
    assert "issue" in posted[0].lower() or "進めるべき" in posted[0]


def test_run_cycle_suppresses_idle_notice_when_requested(
    app_cfg: Callable[..., AppConfig], ok_snapshot: UsageSnapshot
) -> None:
    """前回 idle だった場合の連続通知抑止: idle ケースで notify を呼ばない."""
    cfg = app_cfg()
    posted: list[str] = []
    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
        list_issues=lambda repos: [],
        prioritize=lambda issues, **_: pytest.fail(  # pragma: no cover
            "prioritize must not be invoked when no issues"
        ),
        run_task=lambda *a, **kw: pytest.fail(  # pragma: no cover
            "run_task must not be invoked"
        ),
        notify=posted.append,
        ensure_container=lambda _: None,
        suppress_idle_notice=True,
    )
    assert result.executed is False
    assert result.idle is True
    assert "no open issues" in result.reason
    assert posted == []


def test_run_cycle_idle_when_all_blocked(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    from knowl.filters import INVESTIGATED_LABEL

    cfg = app_cfg()
    posted: list[str] = []
    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
        list_issues=lambda repos: [make_issue(labels=(INVESTIGATED_LABEL,))],
        prioritize=lambda issues, **_: pytest.fail(  # pragma: no cover
            "prioritize must not be invoked"
        ),
        run_task=lambda *a, **kw: pytest.fail(  # pragma: no cover
            "run_task must not be invoked"
        ),
        notify=posted.append,
        ensure_container=lambda _: None,
        suppress_idle_notice=True,
    )
    assert result.executed is False
    assert result.idle is True
    assert posted == []


def test_run_cycle_idle_when_no_actionable_issue_suppressed(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    from knowl.prioritize import NoActionableIssue

    cfg = app_cfg()
    posted: list[str] = []

    def prioritize(
        issues: list[IssueRef], *, model: str
    ) -> tuple[PriorityDecision, IssueRef] | NoActionableIssue:
        return NoActionableIssue(reason="all waiting for human review")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
        list_issues=lambda repos: [make_issue()],
        prioritize=prioritize,
        run_task=lambda *a, **kw: pytest.fail(  # pragma: no cover
            "run_task must not be invoked"
        ),
        notify=posted.append,
        ensure_container=lambda _: pytest.fail(  # pragma: no cover
            "container must not be ensured"
        ),
        suppress_idle_notice=True,
    )
    assert result.executed is False
    assert result.idle is True
    assert posted == []


def test_run_cycle_does_not_suppress_error_notice(
    app_cfg: Callable[..., AppConfig], ok_snapshot: UsageSnapshot
) -> None:
    """エラー通知は suppress_idle_notice=True でも常に飛ぶ."""
    cfg = app_cfg()
    posted: list[str] = []

    def list_issues(_repos: object) -> list[IssueRef]:
        from knowl.github_client import GitHubError

        raise GitHubError("gh auth required")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
        list_issues=list_issues,
        prioritize=lambda issues, **_: pytest.fail(  # pragma: no cover
            "prioritize must not be invoked"
        ),
        run_task=lambda *a, **kw: pytest.fail(  # pragma: no cover
            "run_task must not be invoked"
        ),
        notify=posted.append,
        ensure_container=lambda _: None,
        suppress_idle_notice=True,
    )
    assert result.executed is False
    assert result.idle is False
    # idle ではないのでエラー通知は飛ぶ
    assert len(posted) == 1
    assert "issue collection" in posted[0]


def test_run_cycle_no_op_when_all_issues_blocked(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    from knowl.filters import INVESTIGATED_LABEL, NEEDS_REVIEW_LABEL

    cfg = app_cfg()
    posted: list[str] = []

    blocked_issues = [
        make_issue(number=1, labels=(NEEDS_REVIEW_LABEL,)),
        make_issue(number=2, labels=(INVESTIGATED_LABEL,)),
    ]

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
        list_issues=lambda repos: blocked_issues,
        prioritize=lambda issues, **_: pytest.fail(  # pragma: no cover
            "prioritize must not be invoked when all issues are blocked"
        ),
        run_task=lambda *a, **kw: pytest.fail(  # pragma: no cover
            "run_task must not be invoked when all issues are blocked"
        ),
        notify=posted.append,
        ensure_container=lambda _: pytest.fail(  # pragma: no cover
            "container must not be ensured when all issues are blocked"
        ),
    )
    assert result.executed is False
    assert "no actionable issue" in result.reason.lower()
    assert len(posted) == 1
    assert "💤" in posted[0]


def test_run_cycle_filters_blocked_before_prioritize(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    from knowl.filters import INVESTIGATED_LABEL, NEEDS_REVIEW_LABEL

    cfg = app_cfg()
    posted: list[str] = []

    actionable = make_issue(number=1)
    issues = [
        make_issue(number=2, labels=(NEEDS_REVIEW_LABEL,)),
        actionable,
        make_issue(number=3, labels=(INVESTIGATED_LABEL,)),
    ]

    def prioritize(
        issues: list[IssueRef], *, model: str
    ) -> tuple[PriorityDecision, IssueRef]:
        # blocked な issue は prioritize に渡らないこと
        assert [i.number for i in issues] == [1]
        return (
            PriorityDecision(
                repo="acme/widgets",
                number=1,
                kind=TaskKind.IMPLEMENTATION,
                reason="only candidate",
            ),
            issues[0],
        )

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        return TaskOutcome(
            kind=TaskKind.IMPLEMENTATION,
            action="pr-opened",
            summary="",
            url="https://pr/1",
            followups=[],
        )

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
        list_issues=lambda repos: issues,
        prioritize=prioritize,
        run_task=run_task,
        notify=posted.append,
        ensure_container=lambda _: None,
    )
    assert result.executed is True
    assert result.issue is actionable


def test_run_cycle_short_circuits_for_reviewed_label(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    """``knowl-reviewed`` 付き issue は prioritize をスキップし IMPLEMENTATION で直接実行."""
    from knowl.filters import REVIEWED_LABEL
    from knowl.prioritize import INVESTIGATION_LABEL

    cfg = app_cfg()
    posted: list[str] = []
    other = make_issue(number=1, title="other")
    # reviewed と investigation ラベルが併存しても reviewed の短絡 (IMPLEMENTATION) が
    # 後段の label kind 上書きで覆されないこと。
    reviewed = make_issue(
        number=2, title="ready", labels=(REVIEWED_LABEL, INVESTIGATION_LABEL)
    )

    def prioritize(
        issues: list[IssueRef], *, model: str
    ) -> tuple[PriorityDecision, IssueRef]:  # pragma: no cover
        pytest.fail("prioritize must not be invoked when knowl-reviewed exists")

    received: list[PriorityDecision] = []

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        received.append(decision)
        return TaskOutcome(
            kind=decision.kind,
            action="merged",
            summary="merged existing pr",
            url="https://pr/2",
            followups=[],
        )

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
        list_issues=lambda repos: [other, reviewed],
        prioritize=prioritize,
        run_task=run_task,
        notify=posted.append,
        ensure_container=lambda _: None,
    )

    assert result.executed is True
    assert result.issue is reviewed
    assert received and received[0].kind is TaskKind.IMPLEMENTATION
    assert received[0].number == 2


def test_run_cycle_no_op_when_no_actionable_issue(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    from knowl.prioritize import NoActionableIssue

    cfg = app_cfg()
    posted: list[str] = []

    def prioritize(
        issues: list[IssueRef], *, model: str
    ) -> tuple[PriorityDecision, IssueRef] | NoActionableIssue:
        return NoActionableIssue(reason="all waiting for human review")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
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


def test_run_cycle_happy_path(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    cfg = app_cfg()
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
        fetch_usage=lambda: ok_snapshot,
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
    # サマリ通知に issue URL と PR URL が含まれる
    assert fixture_issue.url in posted[1]
    assert "https://pr/1" in posted[1]


def test_run_cycle_overrides_kind_from_label(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    """ラベルが付いた issue では Claude 判定より優先してラベルから kind を決める."""
    from knowl.prioritize import IMPLEMENTATION_LABEL

    cfg = app_cfg()
    posted: list[str] = []

    fixture_issue = make_issue(number=1, labels=(IMPLEMENTATION_LABEL,))
    # Claude が誤って investigation を返してきたケース。
    fixture_decision = PriorityDecision(
        repo="acme/widgets",
        number=1,
        kind=TaskKind.INVESTIGATION,
        reason="claude said so",
    )
    received: list[PriorityDecision] = []

    def prioritize(
        issues: list[IssueRef], *, model: str
    ) -> tuple[PriorityDecision, IssueRef]:
        return fixture_decision, fixture_issue

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        received.append(decision)
        return TaskOutcome(
            kind=decision.kind, action="ok", summary="", url=None, followups=[]
        )

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
        list_issues=lambda repos: [fixture_issue],
        prioritize=prioritize,
        run_task=run_task,
        notify=posted.append,
        ensure_container=lambda _: None,
    )

    assert result.executed is True
    assert received and received[0].kind is TaskKind.IMPLEMENTATION
    assert result.decision is not None
    assert result.decision.kind is TaskKind.IMPLEMENTATION


def test_run_cycle_uses_claude_kind_when_no_label(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    """ラベル無しのときは Claude が返した kind をそのまま使う (フォールバック)."""
    cfg = app_cfg()

    fixture_issue = make_issue(number=1, labels=())
    fixture_decision = PriorityDecision(
        repo="acme/widgets",
        number=1,
        kind=TaskKind.INVESTIGATION,
        reason="x",
    )
    received: list[PriorityDecision] = []

    def prioritize(
        issues: list[IssueRef], *, model: str
    ) -> tuple[PriorityDecision, IssueRef]:
        return fixture_decision, fixture_issue

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        received.append(decision)
        return TaskOutcome(
            kind=decision.kind, action="ok", summary="", url=None, followups=[]
        )

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
        list_issues=lambda repos: [fixture_issue],
        prioritize=prioritize,
        run_task=run_task,
        notify=lambda _: None,
        ensure_container=lambda _: None,
    )

    assert result.executed is True
    assert received and received[0].kind is TaskKind.INVESTIGATION


def test_run_cycle_falls_back_when_both_labels_present(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    """両ラベルが付いていて矛盾しているときは Claude 判定にフォールバック."""
    from knowl.prioritize import IMPLEMENTATION_LABEL, INVESTIGATION_LABEL

    cfg = app_cfg()

    fixture_issue = make_issue(
        number=1, labels=(IMPLEMENTATION_LABEL, INVESTIGATION_LABEL)
    )
    fixture_decision = PriorityDecision(
        repo="acme/widgets",
        number=1,
        kind=TaskKind.INVESTIGATION,
        reason="claude tiebreaker",
    )
    received: list[PriorityDecision] = []

    def prioritize(
        issues: list[IssueRef], *, model: str
    ) -> tuple[PriorityDecision, IssueRef]:
        return fixture_decision, fixture_issue

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        received.append(decision)
        return TaskOutcome(
            kind=decision.kind, action="ok", summary="", url=None, followups=[]
        )

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
        list_issues=lambda repos: [fixture_issue],
        prioritize=prioritize,
        run_task=run_task,
        notify=lambda _: None,
        ensure_container=lambda _: None,
    )

    assert result.executed is True
    # 両ラベル付きは矛盾。 Claude の判定をそのまま使う。
    assert received and received[0].kind is TaskKind.INVESTIGATION


def test_run_cycle_container_start_failure_notifies(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    cfg = app_cfg()
    posted: list[str] = []

    def ensure_container(_c: object) -> None:
        from knowl.container import ContainerError

        raise ContainerError("docker daemon unavailable")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
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


def test_run_cycle_handles_limit_alert(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    cfg = app_cfg()
    posted: list[str] = []

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        raise ClaudeError("weekly limit reached", limit_reached=True)

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
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


def test_run_cycle_escalates_to_limit_when_claude_error_carries_drained_usage(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    """stderr ヒントが無くても、 ClaudeError に添えた snapshot が閾値割れなら limit alert に昇格.

    gate 通過後に Claude 実行中で 5h ウィンドウを使い切るレースを想定。 run_task 側で
    枯渇 snapshot を ClaudeError に添えて投げると、 cycle.py の except 分岐で
    ``escalate_limit_reached`` が ``limit_reached=True`` に昇格させる。
    """
    cfg = app_cfg()
    posted: list[str] = []
    drained = UsageSnapshot(session_remaining_pct=2, weekly_remaining_pct=80)

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        raise ClaudeError("claude -p exited 1: internal error", usage=drained)

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
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


def test_run_cycle_keeps_generic_error_when_usage_within_threshold(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    """stderr ヒント無し + 通常レンジ snapshot は通常エラー扱いのまま (誤判定しない)."""
    cfg = app_cfg()
    posted: list[str] = []

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        # usage を添えても残量十分なら escalation は発火しない。
        raise ClaudeError(
            "claude -p exited 1: internal error",
            usage=UsageSnapshot(session_remaining_pct=80, weekly_remaining_pct=80),
        )

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
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
    # 通常の claude error 扱い (limit ではない)
    assert "claude error" in result.reason
    assert "limit reached" not in result.reason
    # 通知も限度エラーではなく一般エラー alert
    assert any("❌" in p for p in posted)
    assert not any("limit" in p.lower() for p in posted)


def test_run_cycle_usage_error_notifies(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
) -> None:
    cfg = app_cfg()
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


def test_run_cycle_github_error_notifies(
    app_cfg: Callable[..., AppConfig], ok_snapshot: UsageSnapshot
) -> None:
    cfg = app_cfg()
    posted: list[str] = []

    def list_issues(_repos: object) -> list[IssueRef]:
        from knowl.github_client import GitHubError

        raise GitHubError("gh auth required")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
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


def test_run_cycle_prioritization_error_notifies(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    cfg = app_cfg()
    posted: list[str] = []

    def prioritize(
        issues: list[IssueRef], *, model: str
    ) -> tuple[PriorityDecision, IssueRef]:
        from knowl.prioritize import PrioritizationError

        raise PrioritizationError("bad response")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
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


def test_run_cycle_token_expired_uses_dedicated_message(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
) -> None:
    cfg = app_cfg()
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


def test_run_cycle_container_error_notifies(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    cfg = app_cfg()
    posted: list[str] = []

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        from knowl.container import ContainerError

        raise ContainerError("docker daemon unavailable")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
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


def test_run_cycle_task_execution_error_notifies(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    cfg = app_cfg()
    posted: list[str] = []

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        from knowl.tasks import TaskExecutionError

        raise TaskExecutionError("bad output")

    result = run_cycle(
        cfg,
        fetch_usage=lambda: ok_snapshot,
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


def test_run_cycle_propagates_unknown_error(
    app_cfg: Callable[..., AppConfig],
    make_issue: Callable[..., IssueRef],
    ok_snapshot: UsageSnapshot,
) -> None:
    cfg = app_cfg()

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_cycle(
            cfg,
            fetch_usage=lambda: ok_snapshot,
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
