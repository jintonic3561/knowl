"""1 サイクルのオーケストレーション.

R2 → R3 → R4 → R5/R6 → R7 → R8 を順に実行する純粋ロジック。
I/O 依存は関数引数で差し替え可能にしてテストを容易にする。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from knowl.claude_runner import ClaudeError
from knowl.config import AppConfig, ContainerConfig, RepoConfig
from knowl.container import ContainerError
from knowl.gate import evaluate_gate
from knowl.github_client import GitHubError, IssueRef
from knowl.prioritize import NoActionableIssue, PrioritizationError, PriorityDecision
from knowl.slack import (
    build_cycle_start_notice,
    build_cycle_summary,
    build_idle_notice,
    build_limit_alert,
)
from knowl.tasks import TaskExecutionError, TaskOutcome
from knowl.usage import TokenExpiredError, UsageError, UsageSnapshot

_LOG = logging.getLogger(__name__)

FetchUsage = Callable[[], UsageSnapshot]
ListIssues = Callable[[Sequence[RepoConfig]], list[IssueRef]]
Notify = Callable[[str], None]
EnsureContainer = Callable[[ContainerConfig], None]


class Prioritize(Protocol):
    def __call__(
        self,
        issues: list[IssueRef],
        *,
        model: str,
    ) -> tuple[PriorityDecision, IssueRef] | NoActionableIssue: ...


class RunTask(Protocol):
    def __call__(
        self,
        cfg: AppConfig,
        decision: PriorityDecision,
        issue: IssueRef,
    ) -> TaskOutcome: ...


@dataclass(frozen=True, slots=True)
class CycleResult:
    executed: bool
    reason: str
    usage: UsageSnapshot | None = None
    outcome: TaskOutcome | None = None
    issue: IssueRef | None = None
    decision: PriorityDecision | None = None


def _build_error_alert(prefix: str, exc: BaseException) -> str:
    """サイクル失敗時の Slack 通知文."""
    return f"❌ knowl cycle failed during {prefix}: {exc}"


def run_cycle(
    cfg: AppConfig,
    *,
    fetch_usage: FetchUsage,
    list_issues: ListIssues,
    prioritize: Prioritize,
    run_task: RunTask,
    notify: Notify,
    ensure_container: EnsureContainer,
) -> CycleResult:
    """1 サイクル分の処理を行い結果を返す."""
    try:
        usage = fetch_usage()
    except TokenExpiredError as exc:
        _LOG.warning("OAuth token expired or invalid: %s", exc)
        notify(f"🔐 knowl: OAuth token expired/invalid — {exc}")
        return CycleResult(executed=False, reason=f"oauth token expired: {exc}")
    except UsageError as exc:
        _LOG.warning("usage fetch failed: %s", exc)
        notify(_build_error_alert("usage fetch", exc))
        return CycleResult(executed=False, reason=f"usage fetch failed: {exc}")

    decision_gate = evaluate_gate(
        usage,
        session_threshold=cfg.thresholds.session_remaining_pct,
        weekly_threshold=cfg.thresholds.weekly_remaining_pct,
    )
    if not decision_gate.allowed:
        _LOG.info("gate blocked: %s", decision_gate.reason)
        return CycleResult(executed=False, reason=decision_gate.reason, usage=usage)

    try:
        issues = list_issues(cfg.repositories)
    except GitHubError as exc:
        _LOG.warning("issue collection failed: %s", exc)
        notify(_build_error_alert("issue collection", exc))
        return CycleResult(
            executed=False, reason=f"issue collection failed: {exc}", usage=usage
        )
    if not issues:
        _LOG.info("no open issues across registered repositories")
        notify(build_idle_notice("open issue が見つからない"))
        return CycleResult(executed=False, reason="no open issues", usage=usage)

    try:
        prioritized = prioritize(issues, model=cfg.model)
    except PrioritizationError as exc:
        _LOG.warning("prioritization failed: %s", exc)
        notify(_build_error_alert("prioritization", exc))
        return CycleResult(
            executed=False, reason=f"prioritization failed: {exc}", usage=usage
        )
    except ClaudeError as exc:
        if exc.limit_reached:
            _LOG.warning("claude limit reached during prioritization: %s", exc)
            notify(build_limit_alert(str(exc)))
            return CycleResult(
                executed=False,
                reason=f"claude limit reached: {exc}",
                usage=usage,
            )
        _LOG.warning("claude error during prioritization: %s", exc)
        notify(_build_error_alert("prioritization", exc))
        return CycleResult(
            executed=False, reason=f"prioritization claude error: {exc}", usage=usage
        )

    if isinstance(prioritized, NoActionableIssue):
        _LOG.info("no actionable issue: %s", prioritized.reason)
        notify(build_idle_notice(prioritized.reason))
        return CycleResult(
            executed=False,
            reason=f"no actionable issue: {prioritized.reason}",
            usage=usage,
        )
    decision, picked = prioritized
    _LOG.info(
        "prioritized %s#%d as %s",
        decision.repo,
        decision.number,
        decision.kind.value,
    )

    # prioritize は cfg.repositories の issue から選ぶので必ず一致するはず。
    # 一致しない = invariant 違反として上位に伝播させる。
    repo = next(r for r in cfg.repositories if r.name == decision.repo)

    try:
        ensure_container(repo.container)
    except ContainerError as exc:
        _LOG.warning("container start failed: %s", exc)
        notify(_build_error_alert("container start", exc))
        return CycleResult(
            executed=False,
            reason=f"container start failed: {exc}",
            usage=usage,
            issue=picked,
            decision=decision,
        )

    notify(
        build_cycle_start_notice(
            repo=decision.repo,
            issue_number=decision.number,
            issue_title=picked.title,
        )
    )

    try:
        outcome = run_task(cfg, decision, picked)
    except ClaudeError as exc:
        if exc.limit_reached:
            _LOG.warning("claude limit reached: %s", exc)
            notify(build_limit_alert(str(exc)))
            return CycleResult(
                executed=False,
                reason=f"claude limit reached: {exc}",
                usage=usage,
                issue=picked,
                decision=decision,
            )
        _LOG.warning("claude error during task execution: %s", exc)
        notify(_build_error_alert("task execution", exc))
        return CycleResult(
            executed=False,
            reason=f"task claude error: {exc}",
            usage=usage,
            issue=picked,
            decision=decision,
        )
    except TaskExecutionError as exc:
        _LOG.warning("task execution failed: %s", exc)
        notify(_build_error_alert("task execution", exc))
        return CycleResult(
            executed=False,
            reason=f"task execution failed: {exc}",
            usage=usage,
            issue=picked,
            decision=decision,
        )
    except ContainerError as exc:
        _LOG.warning("container operation failed: %s", exc)
        notify(_build_error_alert("container", exc))
        return CycleResult(
            executed=False,
            reason=f"container operation failed: {exc}",
            usage=usage,
            issue=picked,
            decision=decision,
        )

    summary = build_cycle_summary(
        repo=decision.repo,
        issue_number=decision.number,
        issue_title=picked.title,
        outcome=f"{outcome.action}: {outcome.summary}".strip(": ").strip(),
        followups=outcome.followups,
    )
    notify(summary)

    return CycleResult(
        executed=True,
        reason="ok",
        usage=usage,
        outcome=outcome,
        issue=picked,
        decision=decision,
    )
