"""GitHub クライアント (gh CLI ラッパ).

登録済リポジトリの open issue を ``gh issue list`` で収集する。
gh は devcontainer feature でインストール済の前提。
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass

from knowl.config import RepoConfig

_JSON_FIELDS = "number,title,body,labels,url,updatedAt"
_DEFAULT_TIMEOUT = 30.0


class GitHubError(RuntimeError):
    """gh CLI 呼び出し失敗."""


@dataclass(frozen=True, slots=True)
class IssueRef:
    repo: str
    number: int
    title: str
    body: str
    labels: tuple[str, ...]
    url: str
    updated_at: str


def _parse_issue(repo: str, raw: dict[str, object]) -> IssueRef:
    labels_raw = raw.get("labels") or []
    if not isinstance(labels_raw, list):
        raise GitHubError(f"unexpected labels shape for {repo}#{raw.get('number')}")
    labels = tuple(
        str(label.get("name", "")) for label in labels_raw if isinstance(label, dict)
    )
    number_raw = raw.get("number")
    if not isinstance(number_raw, int | str):
        raise GitHubError(f"unexpected issue number type for {repo}: {number_raw!r}")
    try:
        number = int(number_raw)
    except ValueError as exc:
        raise GitHubError(f"non-numeric issue number for {repo}: {number_raw!r}") from exc
    return IssueRef(
        repo=repo,
        number=number,
        title=str(raw.get("title") or ""),
        body=str(raw.get("body") or ""),
        labels=labels,
        url=str(raw.get("url") or ""),
        updated_at=str(raw.get("updatedAt") or ""),
    )


def list_open_issues(
    repos: Iterable[RepoConfig],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    limit: int = 100,
) -> list[IssueRef]:
    """登録リポジトリの open issue を集約して返す."""
    issues: list[IssueRef] = []
    for repo in repos:
        cmd = [
            "gh",
            "issue",
            "list",
            "--repo",
            repo.name,
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            _JSON_FIELDS,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
            )
        except subprocess.CalledProcessError as exc:
            raise GitHubError(
                f"gh issue list failed for {repo.name}: {exc.stderr or exc.stdout}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise GitHubError(f"gh issue list timed out for {repo.name}") from exc
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise GitHubError(
                f"gh issue list returned non-JSON for {repo.name}: {exc}"
            ) from exc
        if not isinstance(payload, list):
            raise GitHubError(f"gh issue list returned non-list for {repo.name}")
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            issues.append(_parse_issue(repo.name, raw))
    return issues
