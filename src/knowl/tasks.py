"""実装 / 調査タスクの実行ディスパッチ.

テンプレートにissue情報を埋め込み、対象リポジトリのコンテナで ``claude -p`` を
起動する。最終出力 JSON から ``action`` / ``url`` / ``summary`` / ``followups``
を抽出する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from knowl._jsonutil import extract_last_json_object
from knowl.claude_runner import ClaudeResult, run_claude_in_container
from knowl.config import AppConfig, ContainerConfig, RepoConfig
from knowl.github_client import IssueRef
from knowl.prioritize import PriorityDecision, TaskKind


class TaskExecutionError(RuntimeError):
    """タスク実行失敗."""


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    kind: TaskKind
    action: str
    summary: str
    url: str | None
    followups: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class ContainerRunner(Protocol):
    def __call__(
        self,
        container: ContainerConfig,
        prompt: str,
        *,
        workdir: str,
        model: str,
    ) -> ClaudeResult: ...


def render_template(template_path: Path, issue: IssueRef) -> str:
    """テンプレートに issue 情報を埋め込んだプロンプトを返す."""
    if not template_path.is_file():
        raise TaskExecutionError(f"template file not found: {template_path}")
    body = template_path.read_text(encoding="utf-8")
    try:
        return body.format(
            repo=issue.repo,
            issue_number=issue.number,
            issue_title=issue.title,
            issue_body=issue.body,
            issue_url=issue.url,
            issue_labels=",".join(issue.labels) or "-",
        )
    except KeyError as exc:
        raise TaskExecutionError(
            f"template references unknown placeholder: {exc}"
        ) from exc


def extract_final_json(text: str) -> dict[str, Any]:
    """テキスト末尾付近の JSON オブジェクトを抽出する."""
    obj = extract_last_json_object(text)
    if obj is None:
        raise TaskExecutionError("no JSON object found in task output")
    return obj


DEFAULT_RUN_EXTRA_ARGS: tuple[str, ...] = ("--dangerously-skip-permissions",)


def _runner_default(
    container: ContainerConfig,
    prompt: str,
    *,
    workdir: str,
    model: str,
) -> ClaudeResult:
    """Knowl はサンドボックス済みの作業 container 内で claude -p を起動するため、
    対話不能環境で permission prompt に詰まらないよう既定で
    ``--dangerously-skip-permissions`` を付与する。
    """
    return run_claude_in_container(
        container,
        prompt,
        workdir=workdir,
        model=model,
        extra_args=DEFAULT_RUN_EXTRA_ARGS,
    )


def _find_repo(cfg: AppConfig, name: str) -> RepoConfig:
    for repo in cfg.repositories:
        if repo.name == name:
            return repo
    raise TaskExecutionError(f"repository '{name}' is not registered")


def _template_for(cfg: AppConfig, kind: TaskKind) -> Path:
    return (
        cfg.templates.implementation
        if kind is TaskKind.IMPLEMENTATION
        else cfg.templates.investigation
    )


def _url_for(action_payload: dict[str, Any], kind: TaskKind) -> str | None:
    if kind is TaskKind.IMPLEMENTATION:
        url = action_payload.get("pr_url")
    else:
        url = action_payload.get("comment_url")
    if isinstance(url, str) and url:
        return url
    return None


def run_task(
    cfg: AppConfig,
    decision: PriorityDecision,
    issue: IssueRef,
    *,
    runner: ContainerRunner | None = None,
) -> TaskOutcome:
    """1 件の issue について claude を起動し、結果サマリを返す."""
    repo = _find_repo(cfg, decision.repo)
    template_path = _template_for(cfg, decision.kind)
    prompt = render_template(template_path, issue)
    runner_fn: ContainerRunner = runner or _runner_default
    result = runner_fn(
        repo.container,
        prompt,
        workdir=str(repo.workdir or repo.container.workdir),
        model=cfg.model,
    )
    payload = extract_final_json(result.text)
    action = str(payload.get("action") or "")
    if not action:
        raise TaskExecutionError("task output JSON missing 'action'")
    summary = str(payload.get("summary") or "").strip()
    followups_raw = payload.get("followups") or []
    if not isinstance(followups_raw, list):
        raise TaskExecutionError("task output 'followups' must be a list")
    followups = [str(f) for f in followups_raw if str(f).strip()]
    return TaskOutcome(
        kind=decision.kind,
        action=action,
        summary=summary,
        url=_url_for(payload, decision.kind),
        followups=followups,
        raw=payload,
    )
