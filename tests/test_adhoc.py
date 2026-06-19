"""knowl.adhoc のテスト."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from knowl.adhoc import (
    AdhocResult,
    AdhocResultKind,
    build_seed_issue_title,
    run_adhoc,
)
from knowl.config import AppConfig, RepoConfig, TemplatesConfig
from knowl.github_client import GitHubError, IssueRef
from knowl.prioritize import PriorityDecision, TaskKind
from knowl.tasks import TaskOutcome
from knowl.usage import UsageSnapshot


@contextmanager
def _no_lock() -> Iterator[bool]:
    yield True


@contextmanager
def _busy_lock() -> Iterator[bool]:
    yield False


def _repo() -> RepoConfig:
    return RepoConfig.model_validate(
        {"name": "acme/widgets", "container": {"kind": "docker", "name": "c"}}
    )


def _cfg(tmp_path: Path) -> AppConfig:
    impl = tmp_path / "impl.md"
    impl.write_text("p", encoding="utf-8")
    inv = tmp_path / "inv.md"
    inv.write_text("p", encoding="utf-8")
    return AppConfig(
        templates=TemplatesConfig(implementation=impl, investigation=inv),
        repositories=[_repo()],
    )


def _issue(number: int = 99) -> IssueRef:
    return IssueRef(
        repo="acme/widgets",
        number=number,
        title="seed",
        body="body",
        labels=(),
        url=f"https://github.com/acme/widgets/issues/{number}",
        updated_at="",
    )


def test_build_seed_issue_title_truncates_long_input() -> None:
    long = "a" * 200
    title = build_seed_issue_title(long)
    # 概ね 60 文字以内 + "..."
    assert len(title) <= 70
    assert title.endswith("...")


def test_build_seed_issue_title_keeps_short_input() -> None:
    title = build_seed_issue_title("short task")
    assert title == "short task"


def test_build_seed_issue_title_first_line_only() -> None:
    title = build_seed_issue_title("first line\nsecond line\nthird line")
    assert "second" not in title
    assert title.startswith("first line")


def test_run_adhoc_gate_blocked(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    posted: list[str] = []
    created: list[tuple[str, str, str]] = []

    def create_issue(repo_name: str, *, title: str, body: str) -> IssueRef:
        created.append((repo_name, title, body))
        return _issue()

    result = run_adhoc(
        cfg,
        repo_name="acme/widgets",
        task_description="do something",
        user="alice",
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=5, weekly_remaining_pct=50
        ),
        create_issue=create_issue,
        ensure_container=lambda _: pytest.fail("must not be called when gate blocks"),
        run_task=lambda *a, **kw: pytest.fail("must not be called when gate blocks"),
        notify=posted.append,
        acquire_lock=_no_lock,
    )

    assert result.kind is AdhocResultKind.GATE_BLOCKED
    assert "session below" in result.reason
    assert created == []
    assert posted == []  # ゲートブロックは Slack 通知に流さない


def test_run_adhoc_unknown_repo(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    posted: list[str] = []

    result = run_adhoc(
        cfg,
        repo_name="foo/bar",
        task_description="task",
        user="alice",
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=80, weekly_remaining_pct=80
        ),
        create_issue=lambda *a, **kw: pytest.fail(
            "must not call create_issue for unknown repo"
        ),
        ensure_container=lambda _: pytest.fail("must not be called for unknown repo"),
        run_task=lambda *a, **kw: pytest.fail("must not be called for unknown repo"),
        notify=posted.append,
        acquire_lock=_no_lock,
    )

    assert result.kind is AdhocResultKind.ERROR
    assert "foo/bar" in result.reason
    # 登録外 repo は Slack に流す必要なし (handler が usage で返す)
    assert posted == []


def test_run_adhoc_happy_path(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    posted: list[str] = []
    ensured: list[str] = []

    seed = _issue(number=99)
    create_calls: list[tuple[str, str, str]] = []

    def create_issue(repo_name: str, *, title: str, body: str) -> IssueRef:
        create_calls.append((repo_name, title, body))
        return seed

    decisions: list[PriorityDecision] = []

    def run_task(
        cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
    ) -> TaskOutcome:
        decisions.append(decision)
        assert issue is seed
        return TaskOutcome(
            kind=TaskKind.IMPLEMENTATION,
            action="pr-opened",
            summary="opened pr",
            url="https://pr/1",
            followups=[],
        )

    def ensure_container(c: object) -> None:
        from knowl.config import ContainerConfig

        assert isinstance(c, ContainerConfig)
        ensured.append(c.name)

    result = run_adhoc(
        cfg,
        repo_name="acme/widgets",
        task_description="do something useful",
        user="alice",
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=80, weekly_remaining_pct=80
        ),
        create_issue=create_issue,
        ensure_container=ensure_container,
        run_task=run_task,
        notify=posted.append,
        acquire_lock=_no_lock,
    )

    assert result.kind is AdhocResultKind.OK
    assert create_calls and create_calls[0][0] == "acme/widgets"
    # body は元の指示全文 + Slack triggered フッタ
    body = create_calls[0][2]
    assert "do something useful" in body
    assert "@alice" in body
    # ensure_container は 1 回
    assert ensured == ["c"]
    # 開始通知 + サマリ
    assert len(posted) == 2
    assert "開始" in posted[0]
    assert "pr-opened" in posted[1]
    # PriorityDecision は implementation 固定
    assert decisions and decisions[0].kind is TaskKind.IMPLEMENTATION
    assert decisions[0].repo == "acme/widgets"
    assert decisions[0].number == 99


def test_run_adhoc_create_issue_failure(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    posted: list[str] = []

    def create_issue(repo_name: str, *, title: str, body: str) -> IssueRef:
        raise GitHubError("permission denied")

    result = run_adhoc(
        cfg,
        repo_name="acme/widgets",
        task_description="task",
        user="alice",
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=80, weekly_remaining_pct=80
        ),
        create_issue=create_issue,
        ensure_container=lambda _: pytest.fail("must not be called"),
        run_task=lambda *a, **kw: pytest.fail("must not be called"),
        notify=posted.append,
        acquire_lock=_no_lock,
    )

    assert result.kind is AdhocResultKind.ERROR
    assert "permission denied" in result.reason
    # エラーは notify 経由でアラート
    assert posted and any("permission denied" in p for p in posted)
    assert isinstance(result, AdhocResult)


def test_run_adhoc_busy_when_lock_unavailable(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    result = run_adhoc(
        cfg,
        repo_name="acme/widgets",
        task_description="task",
        user="alice",
        fetch_usage=lambda: pytest.fail("must not be called when busy"),
        create_issue=lambda *a, **kw: pytest.fail("must not be called when busy"),
        ensure_container=lambda _: pytest.fail("must not be called when busy"),
        run_task=lambda *a, **kw: pytest.fail("must not be called when busy"),
        notify=lambda _: pytest.fail("must not notify when busy"),
        acquire_lock=_busy_lock,
    )

    assert result.kind is AdhocResultKind.BUSY


def test_run_adhoc_task_limit_reached(tmp_path: Path) -> None:
    from knowl.claude_runner import ClaudeError

    cfg = _cfg(tmp_path)
    posted: list[str] = []

    def run_task(*a: object, **kw: object) -> TaskOutcome:
        raise ClaudeError("weekly limit reached", limit_reached=True)

    result = run_adhoc(
        cfg,
        repo_name="acme/widgets",
        task_description="task",
        user="alice",
        fetch_usage=lambda: UsageSnapshot(
            session_remaining_pct=80, weekly_remaining_pct=80
        ),
        create_issue=lambda *a, **kw: _issue(),
        ensure_container=lambda _: None,
        run_task=run_task,
        notify=posted.append,
        acquire_lock=_no_lock,
    )

    assert result.kind is AdhocResultKind.ERROR
    assert "limit" in result.reason.lower()
    assert any("limit" in p.lower() for p in posted)


def test_run_adhoc_token_expired_is_error(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    posted: list[str] = []

    def fetch_usage() -> UsageSnapshot:
        from knowl.usage import TokenExpiredError

        raise TokenExpiredError("token expired")

    result = run_adhoc(
        cfg,
        repo_name="acme/widgets",
        task_description="task",
        user="alice",
        fetch_usage=fetch_usage,
        create_issue=lambda *a, **kw: pytest.fail("must not be called on token expired"),
        ensure_container=lambda _: pytest.fail("must not be called on token expired"),
        run_task=lambda *a, **kw: pytest.fail("must not be called on token expired"),
        notify=posted.append,
        acquire_lock=_no_lock,
    )

    # OAuth 失効は GATE_BLOCKED ではなく ERROR (運用者が即介入する必要があるため)。
    assert result.kind is AdhocResultKind.ERROR
    assert "oauth token expired" in result.reason
    assert posted and any("🔐" in p for p in posted)
