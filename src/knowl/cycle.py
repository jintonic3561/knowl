"""1 サイクルのオーケストレーション.

R2 → R3 → R4 → R5/R6 → R7 → R8 を順に実行する純粋ロジック。
I/O 依存は関数引数で差し替え可能にしてテストを容易にする。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from knowl.claude_runner import ClaudeError, escalate_limit_reached
from knowl.config import AppConfig, ContainerConfig, RepoConfig
from knowl.container import ContainerError
from knowl.filters import REVIEWED_LABEL, exclude_blocked_issues
from knowl.gate import evaluate_gate
from knowl.github_client import GitHubError, IssueRef
from knowl.prioritize import (
    IMPLEMENTATION_LABEL,
    INVESTIGATION_LABEL,
    NoActionableIssue,
    PrioritizationError,
    PriorityDecision,
    TaskKind,
    task_kind_from_labels,
)
from knowl.slack import (
    build_cycle_start_notice,
    build_cycle_summary,
    build_idle_notice,
    classify_claude_error,
    format_error_alert,
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
    idle: bool = False
    usage: UsageSnapshot | None = None
    outcome: TaskOutcome | None = None
    issue: IssueRef | None = None
    decision: PriorityDecision | None = None


def run_cycle(
    cfg: AppConfig,
    *,
    fetch_usage: FetchUsage,
    list_issues: ListIssues,
    prioritize: Prioritize,
    run_task: RunTask,
    notify: Notify,
    ensure_container: EnsureContainer,
    suppress_idle_notice: bool = False,
) -> CycleResult:
    """1 サイクル分の処理を行い結果を返す.

    ``suppress_idle_notice=True`` のとき、 idle (進めるべき issue 無し) 時の
    Slack 通知を省略する。 cron で 1 時間毎に走るので、連続して idle が続くと
    同じ文面が延々と流れる。 CLI 側で前回 ``CycleResult.idle`` を覚えておき、
    次サイクルで True を渡すことで連続通知を抑える。
    """
    def notify_idle(reason: str) -> None:
        if not suppress_idle_notice:
            notify(build_idle_notice(reason))

    try:
        usage = fetch_usage()
    except TokenExpiredError as exc:
        _LOG.warning("OAuth token expired or invalid: %s", exc)
        notify(f"🔐 knowl: OAuth token expired/invalid — {exc}")
        return CycleResult(executed=False, reason=f"oauth token expired: {exc}")
    except UsageError as exc:
        _LOG.warning("usage fetch failed: %s", exc)
        notify(format_error_alert("cycle failed during usage fetch", exc))
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
        notify(format_error_alert("cycle failed during issue collection", exc))
        return CycleResult(
            executed=False, reason=f"issue collection failed: {exc}", usage=usage
        )
    if not issues:
        _LOG.info("no open issues across registered repositories")
        notify_idle("open issue が見つからない")
        return CycleResult(
            executed=False, reason="no open issues", idle=True, usage=usage
        )

    candidates = exclude_blocked_issues(issues)
    if not candidates:
        reason = "open issue は全てレビュー中 / 調査完了済み"
        _LOG.info("all open issues blocked: %s", reason)
        notify_idle(reason)
        return CycleResult(
            executed=False,
            reason=f"no actionable issue: {reason}",
            idle=True,
            usage=usage,
        )

    # `knowl-reviewed` 付き issue は人間レビュー通過後にユーザが付与したもの。
    # 既存 PR をマージするだけで完了するため、 Claude prioritize をスキップし
    # IMPLEMENTATION として直接実行する (テンプレ側がマージ処理を担当)。
    reviewed = next((i for i in candidates if REVIEWED_LABEL in i.labels), None)
    if reviewed is not None:
        prioritized: tuple[PriorityDecision, IssueRef] | NoActionableIssue = (
            PriorityDecision(
                repo=reviewed.repo,
                number=reviewed.number,
                kind=TaskKind.IMPLEMENTATION,
                reason=f"{REVIEWED_LABEL} ラベル付き — 既存 PR のマージ処理を優先",
            ),
            reviewed,
        )
        _LOG.info(
            "short-circuiting prioritize for reviewed issue %s#%d",
            reviewed.repo,
            reviewed.number,
        )
    else:
        try:
            prioritized = prioritize(candidates, model=cfg.model)
        except PrioritizationError as exc:
            _LOG.warning("prioritization failed: %s", exc)
            notify(format_error_alert("cycle failed during prioritization", exc))
            return CycleResult(
                executed=False, reason=f"prioritization failed: {exc}", usage=usage
            )
        except ClaudeError as exc:
            escalate_limit_reached(
                exc,
                usage,
                session_threshold=cfg.thresholds.session_remaining_pct,
                weekly_threshold=cfg.thresholds.weekly_remaining_pct,
            )
            alert = classify_claude_error(
                exc,
                notice_prefix="cycle failed during prioritization",
                reason_label="prioritization",
            )
            if alert.limit_reached:
                _LOG.warning("claude limit reached during prioritization: %s", exc)
            else:
                _LOG.warning("claude error during prioritization: %s", exc)
            notify(alert.notice)
            return CycleResult(executed=False, reason=alert.reason, usage=usage)

    if isinstance(prioritized, NoActionableIssue):
        _LOG.info("no actionable issue: %s", prioritized.reason)
        notify_idle(prioritized.reason)
        return CycleResult(
            executed=False,
            reason=f"no actionable issue: {prioritized.reason}",
            idle=True,
            usage=usage,
        )
    decision, picked = prioritized
    # short-circuit 経路 (reviewed) では IMPLEMENTATION 固定 = マージ処理が不変条件。
    # 他のラベル (implementation/investigation) が併存していても override しない。
    if reviewed is None:
        label_kind = task_kind_from_labels(picked.labels)
        if label_kind is not None and label_kind is not decision.kind:
            _LOG.info(
                "overriding kind from label: %s -> %s for %s#%d",
                decision.kind.value,
                label_kind.value,
                decision.repo,
                decision.number,
            )
            decision = decision.model_copy(update={"kind": label_kind})
        elif (
            label_kind is None
            and IMPLEMENTATION_LABEL in picked.labels
            and INVESTIGATION_LABEL in picked.labels
        ):
            _LOG.warning(
                "conflicting kind labels on %s#%d, falling back to claude kind=%s",
                decision.repo,
                decision.number,
                decision.kind.value,
            )
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
        notify(format_error_alert("cycle failed during container start", exc))
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
        escalate_limit_reached(
            exc,
            usage,
            session_threshold=cfg.thresholds.session_remaining_pct,
            weekly_threshold=cfg.thresholds.weekly_remaining_pct,
        )
        alert = classify_claude_error(
            exc,
            notice_prefix="cycle failed during task execution",
            reason_label="task",
        )
        if alert.limit_reached:
            _LOG.warning("claude limit reached: %s", exc)
        else:
            _LOG.warning("claude error during task execution: %s", exc)
        notify(alert.notice)
        return CycleResult(
            executed=False,
            reason=alert.reason,
            usage=usage,
            issue=picked,
            decision=decision,
        )
    except TaskExecutionError as exc:
        _LOG.warning("task execution failed: %s", exc)
        notify(format_error_alert("cycle failed during task execution", exc))
        return CycleResult(
            executed=False,
            reason=f"task execution failed: {exc}",
            usage=usage,
            issue=picked,
            decision=decision,
        )
    except ContainerError as exc:
        _LOG.warning("container operation failed: %s", exc)
        notify(format_error_alert("cycle failed during container", exc))
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
        issue_url=picked.url,
        outcome=f"{outcome.action}: {outcome.summary}".strip(": ").strip(),
        outcome_url=outcome.url,
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
