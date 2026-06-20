"""Slack slash command 経由の ad-hoc 起動.

`/knowl run <repo> <task>` が来たら呼ばれる 1 サイクル。 cron の通常サイクルと
違い prioritize/list_issues を経由せず、対象 repo に seed issue を起票してそのまま
通常の実装パイプラインに流す。
"""

from __future__ import annotations

import logging
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from knowl._lock import cycle_lock
from knowl.claude_runner import ClaudeError, escalate_limit_reached
from knowl.config import AppConfig, RepoConfig
from knowl.container import ContainerError
from knowl.cycle import EnsureContainer, FetchUsage, Notify, RunTask
from knowl.gate import evaluate_gate
from knowl.github_client import GitHubError, IssueRef
from knowl.prioritize import PriorityDecision, TaskKind
from knowl.slack import (
    build_cycle_start_notice,
    build_cycle_summary,
    classify_claude_error,
    format_error_alert,
)
from knowl.tasks import TaskExecutionError
from knowl.usage import TokenExpiredError, UsageError

_LOG = logging.getLogger(__name__)

_SEED_TITLE_MAX = 60


class CreateIssue(Protocol):
    def __call__(
        self,
        repo_name: str,
        *,
        title: str,
        body: str,
    ) -> IssueRef: ...


class AcquireLock(Protocol):
    def __call__(self) -> AbstractContextManager[bool]: ...


class AdhocResultKind(StrEnum):
    """ad-hoc 起動 1 件分の結果分類.

    Slack handler 側は kind を見て返事の文面を決める。
    - ``OK``: 通常完了。 handler は何も追加 respond しない (通知は notify 経由)。
    - ``GATE_BLOCKED``: usage gate 不通過。 handler は「rate limit きつい」を返す。
    - ``BUSY``: cron tick 等で別サイクルが実行中。 handler は再投入を案内。
    - ``ERROR``: それ以外 (OAuth 失効、 repo 未登録、 issue 起票失敗、 task 実行失敗等)。
      handler は reason を含めて即時返事し、運用者がすぐ復旧手段を取れるようにする。
    """

    OK = "ok"
    GATE_BLOCKED = "gate_blocked"
    BUSY = "busy"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AdhocResult:
    """ad-hoc 起動 1 件分の結果."""

    kind: AdhocResultKind
    reason: str


def build_seed_issue_title(task_description: str) -> str:
    """指示の先頭行を 60 文字程度で切り出した issue タイトル."""
    first_line = task_description.strip().splitlines()[0] if task_description.strip() else ""
    if len(first_line) <= _SEED_TITLE_MAX:
        return first_line or "ad-hoc task"
    return first_line[:_SEED_TITLE_MAX].rstrip() + "..."


def _build_seed_issue_body(task_description: str, *, user: str) -> str:
    return (
        f"{task_description.rstrip()}\n\n"
        f"---\nTriggered via Slack by @{user}."
    )


def _find_repo(cfg: AppConfig, name: str) -> RepoConfig | None:
    for repo in cfg.repositories:
        if repo.name == name:
            return repo
    return None


def run_adhoc(
    cfg: AppConfig,
    *,
    repo_name: str,
    task_description: str,
    user: str,
    fetch_usage: FetchUsage,
    create_issue: CreateIssue,
    ensure_container: EnsureContainer,
    run_task: RunTask,
    notify: Notify,
    acquire_lock: AcquireLock | None = None,
) -> AdhocResult:
    """ad-hoc 起動の 1 サイクル.

    通常の ``run_cycle`` と同じく gate 判定 → run_task のフローだが、
    対象 issue は Slack 指示から生成して即起票する。

    ``acquire_lock`` は cron tick との並走を防ぐためのプロセス間排他。 default で
    ``/var/run/knowl-cycle.lock`` を flock する。テストでは ``yield True`` を返す
    ダミー context manager を渡して排他をバイパスする。
    """
    lock_cm = acquire_lock() if acquire_lock is not None else cycle_lock()
    with lock_cm as acquired:
        if not acquired:
            msg = "another cycle is running; try again shortly"
            _LOG.info("ad-hoc busy: %s", msg)
            return AdhocResult(kind=AdhocResultKind.BUSY, reason=msg)
        return _run_locked(
            cfg,
            repo_name=repo_name,
            task_description=task_description,
            user=user,
            fetch_usage=fetch_usage,
            create_issue=create_issue,
            ensure_container=ensure_container,
            run_task=run_task,
            notify=notify,
        )


def _run_locked(
    cfg: AppConfig,
    *,
    repo_name: str,
    task_description: str,
    user: str,
    fetch_usage: FetchUsage,
    create_issue: CreateIssue,
    ensure_container: EnsureContainer,
    run_task: RunTask,
    notify: Notify,
) -> AdhocResult:
    try:
        usage = fetch_usage()
    except TokenExpiredError as exc:
        notify(f"🔐 knowl: OAuth token expired/invalid — {exc}")
        return AdhocResult(
            kind=AdhocResultKind.ERROR, reason=f"oauth token expired: {exc}"
        )
    except UsageError as exc:
        notify(format_error_alert("ad-hoc failed during usage fetch", exc))
        return AdhocResult(
            kind=AdhocResultKind.ERROR, reason=f"usage fetch failed: {exc}"
        )

    decision_gate = evaluate_gate(
        usage,
        session_threshold=cfg.thresholds.session_remaining_pct,
        weekly_threshold=cfg.thresholds.weekly_remaining_pct,
    )
    if not decision_gate.allowed:
        _LOG.info("ad-hoc gate blocked: %s", decision_gate.reason)
        return AdhocResult(
            kind=AdhocResultKind.GATE_BLOCKED, reason=decision_gate.reason
        )

    repo = _find_repo(cfg, repo_name)
    if repo is None:
        msg = f"repository '{repo_name}' is not registered in knowl.yaml"
        _LOG.info("ad-hoc unknown repo: %s", msg)
        return AdhocResult(kind=AdhocResultKind.ERROR, reason=msg)

    title = build_seed_issue_title(task_description)
    body = _build_seed_issue_body(task_description, user=user)
    try:
        issue = create_issue(repo_name, title=title, body=body)
    except GitHubError as exc:
        notify(format_error_alert("ad-hoc failed during issue creation", exc))
        return AdhocResult(
            kind=AdhocResultKind.ERROR, reason=f"issue creation failed: {exc}"
        )

    try:
        ensure_container(repo.container)
    except ContainerError as exc:
        notify(format_error_alert("ad-hoc failed during container start", exc))
        return AdhocResult(
            kind=AdhocResultKind.ERROR, reason=f"container start failed: {exc}"
        )

    decision = PriorityDecision(
        repo=repo_name,
        number=issue.number,
        kind=TaskKind.IMPLEMENTATION,
        reason=f"ad-hoc via Slack by @{user}",
    )

    notify(
        build_cycle_start_notice(
            repo=repo_name,
            issue_number=issue.number,
            issue_title=issue.title,
        )
    )

    try:
        outcome = run_task(cfg, decision, issue)
    except ClaudeError as exc:
        escalate_limit_reached(
            exc,
            usage,
            session_threshold=cfg.thresholds.session_remaining_pct,
            weekly_threshold=cfg.thresholds.weekly_remaining_pct,
        )
        alert = classify_claude_error(
            exc,
            notice_prefix="ad-hoc failed during task execution",
            reason_label="task",
        )
        notify(alert.notice)
        return AdhocResult(kind=AdhocResultKind.ERROR, reason=alert.reason)
    except TaskExecutionError as exc:
        notify(format_error_alert("ad-hoc failed during task execution", exc))
        return AdhocResult(
            kind=AdhocResultKind.ERROR, reason=f"task execution failed: {exc}"
        )
    except ContainerError as exc:
        notify(format_error_alert("ad-hoc failed during container", exc))
        return AdhocResult(
            kind=AdhocResultKind.ERROR, reason=f"container operation failed: {exc}"
        )

    notify(
        build_cycle_summary(
            repo=repo_name,
            issue_number=issue.number,
            issue_title=issue.title,
            issue_url=issue.url,
            outcome=f"{outcome.action}: {outcome.summary}".strip(": ").strip(),
            outcome_url=outcome.url,
            outcome_url_label=(
                "PR" if outcome.kind is TaskKind.IMPLEMENTATION else "コメント"
            ),
            followups=outcome.followups,
        )
    )

    return AdhocResult(kind=AdhocResultKind.OK, reason="ok")


__all__ = [
    "AcquireLock",
    "AdhocResult",
    "AdhocResultKind",
    "CreateIssue",
    "build_seed_issue_title",
    "run_adhoc",
]
