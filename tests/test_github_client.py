"""knowl.github_client のテスト."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

import pytest

from knowl.config import RepoConfig
from knowl.github_client import (
    GitHubError,
    IssueRef,
    create_issue,
    list_open_issues,
    resolve_gh_login,
)


def _repo(name: str = "acme/widgets") -> RepoConfig:
    return RepoConfig.model_validate(
        {"name": name, "container": {"kind": "docker", "name": "c"}}
    )


class FakeRun:
    """subprocess.run の差し替え用フェイク."""

    def __init__(self, payloads: dict[str, list[dict[str, object]]]) -> None:
        self.payloads = payloads
        self.calls: list[Sequence[str]] = []

    def __call__(
        self,
        cmd: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        idx = cmd.index("--repo") + 1
        repo_name = cmd[idx]
        body = json.dumps(self.payloads.get(repo_name, []))
        return subprocess.CompletedProcess(args=list(cmd), returncode=0, stdout=body, stderr="")


def test_list_open_issues_aggregates_multiple_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = {
        "acme/widgets": [
            {
                "number": 12,
                "title": "Fix login",
                "body": "Steps...",
                "labels": [{"name": "bug"}],
                "url": "https://github.com/acme/widgets/issues/12",
                "updatedAt": "2026-06-01T00:00:00Z",
            }
        ],
        "acme/gizmos": [
            {
                "number": 3,
                "title": "Investigate flake",
                "body": "...",
                "labels": [],
                "url": "https://github.com/acme/gizmos/issues/3",
                "updatedAt": "2026-06-10T00:00:00Z",
            }
        ],
    }
    fake = FakeRun(payloads)
    monkeypatch.setattr(subprocess, "run", fake)

    issues = list_open_issues([_repo("acme/widgets"), _repo("acme/gizmos")])

    assert [i.repo for i in issues] == ["acme/widgets", "acme/gizmos"]
    assert issues[0] == IssueRef(
        repo="acme/widgets",
        number=12,
        title="Fix login",
        body="Steps...",
        labels=("bug",),
        url="https://github.com/acme/widgets/issues/12",
        updated_at="2026-06-01T00:00:00Z",
    )
    # gh CLI に正しい引数で投げているか
    assert fake.calls[0][:3] == ["gh", "issue", "list"]
    assert "--state" in fake.calls[0] and "open" in fake.calls[0]
    assert "--json" in fake.calls[0]


def test_list_open_issues_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun({"acme/widgets": []})
    monkeypatch.setattr(subprocess, "run", fake)
    assert list_open_issues([_repo("acme/widgets")]) == []


def test_list_open_issues_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(
        cmd: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            returncode=1, cmd=list(cmd), output="", stderr="auth required"
        )

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(GitHubError) as exc:
        list_open_issues([_repo()])
    assert "auth required" in str(exc.value)


def test_list_open_issues_rejects_missing_number(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads: dict[str, list[dict[str, object]]] = {
        "acme/widgets": [
            {
                "title": "no number",
                "body": "",
                "labels": [],
                "url": "https://github.com/acme/widgets/issues/?",
                "updatedAt": "2026-06-01T00:00:00Z",
            }
        ]
    }
    monkeypatch.setattr(subprocess, "run", FakeRun(payloads))
    with pytest.raises(GitHubError):
        list_open_issues([_repo("acme/widgets")])


def test_list_open_issues_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad_json(
        cmd: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=0, stdout="not json", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", bad_json)
    with pytest.raises(GitHubError):
        list_open_issues([_repo()])


def test_create_issue_parses_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Sequence[str]] = []

    def run(
        cmd: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(
            args=list(cmd),
            returncode=0,
            stdout="https://github.com/acme/widgets/issues/42\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", run)
    issue = create_issue(
        "acme/widgets", title="Test", body="body content"
    )
    assert isinstance(issue, IssueRef)
    assert issue.repo == "acme/widgets"
    assert issue.number == 42
    assert issue.title == "Test"
    assert issue.body == "body content"
    assert issue.url == "https://github.com/acme/widgets/issues/42"
    # gh issue create に正しい引数
    assert calls[0][:3] == ["gh", "issue", "create"]
    assert "--repo" in calls[0]
    assert "acme/widgets" in calls[0]
    assert "--title" in calls[0]
    assert "Test" in calls[0]
    assert "--body" in calls[0]
    assert "body content" in calls[0]


def test_create_issue_handles_unexpected_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(
        cmd: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=0, stdout="garbage\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(GitHubError):
        create_issue("acme/widgets", title="t", body="b")


def test_create_issue_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(
        cmd: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            returncode=1, cmd=list(cmd), output="", stderr="rate limited"
        )

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(GitHubError) as exc:
        create_issue("acme/widgets", title="t", body="b")
    assert "rate limited" in str(exc.value)


def test_resolve_gh_login_returns_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(
        cmd: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=0, stdout="alice\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert resolve_gh_login() == "alice"


def test_resolve_gh_login_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(
        cmd: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            returncode=1, cmd=list(cmd), output="", stderr="auth required"
        )

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(GitHubError):
        resolve_gh_login()
