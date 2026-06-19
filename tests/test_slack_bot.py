"""knowl.slack_bot のテスト."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import pytest

from knowl.adhoc import AdhocResult, AdhocResultKind
from knowl.slack_bot import (
    SlackCommandError,
    dispatch_command,
    parse_command_text,
    resolve_repo,
)


def test_parse_command_text_basic() -> None:
    sub, repo, task = parse_command_text("run owner/repo タスクの内容")
    assert sub == "run"
    assert repo == "owner/repo"
    assert task == "タスクの内容"


def test_parse_command_text_owner_omitted() -> None:
    sub, repo, task = parse_command_text("run knowl テストを書いて")
    assert sub == "run"
    assert repo == "knowl"
    assert task == "テストを書いて"


def test_parse_command_text_quoted_task() -> None:
    sub, repo, task = parse_command_text('run knowl "複数 単語 を 含む 指示"')
    assert sub == "run"
    assert repo == "knowl"
    assert task == "複数 単語 を 含む 指示"


def test_parse_command_text_empty_raises() -> None:
    with pytest.raises(SlackCommandError):
        parse_command_text("")


def test_parse_command_text_missing_task_raises() -> None:
    with pytest.raises(SlackCommandError):
        parse_command_text("run owner/repo")


def test_parse_command_text_wrong_subcommand_raises() -> None:
    with pytest.raises(SlackCommandError):
        parse_command_text("stop owner/repo do something")


def test_resolve_repo_owner_slash_repo_passthrough() -> None:
    assert resolve_repo("owner/repo", login="alice") == "owner/repo"


def test_resolve_repo_name_only_uses_login() -> None:
    assert resolve_repo("knowl", login="alice") == "alice/knowl"


@dataclass
class _Captured:
    acks: int = 0
    responds: list[str] = field(default_factory=list)


def _record() -> tuple[
    _Captured,
    Callable[[], Awaitable[None]],
    Callable[[str], Awaitable[None]],
]:
    captured = _Captured()

    async def ack() -> None:
        captured.acks += 1

    async def respond(text: str) -> None:
        captured.responds.append(text)

    return captured, ack, respond


def test_dispatch_acks_immediately_and_acknowledges_task() -> None:
    captured, ack, respond = _record()

    called: dict[str, object] = {}

    async def adhoc(
        *, repo: str, task: str, user: str
    ) -> AdhocResult:
        called["repo"] = repo
        called["task"] = task
        called["user"] = user
        return AdhocResult(kind=AdhocResultKind.OK, reason="ok")

    asyncio.run(
        dispatch_command(
            ack=ack,
            respond=respond,
            text="run owner/repo issue を実装して",
            user_name="alice",
            login="bob",
            adhoc_runner=adhoc,
        )
    )

    assert captured.acks == 1
    # 1 回目の respond は「了解」
    assert captured.responds[0].startswith("了解")
    # gate OK の場合は追加 respond は無い (二重通知回避)
    assert len(captured.responds) == 1
    assert called["repo"] == "owner/repo"
    assert called["task"] == "issue を実装して"
    assert called["user"] == "alice"


def test_dispatch_resolves_owner_omitted_repo() -> None:
    _captured, ack, respond = _record()

    received_repo: dict[str, str] = {}

    async def adhoc(*, repo: str, task: str, user: str) -> AdhocResult:
        received_repo["repo"] = repo
        return AdhocResult(kind=AdhocResultKind.OK, reason="ok")

    asyncio.run(
        dispatch_command(
            ack=ack,
            respond=respond,
            text="run knowl テスト",
            user_name="alice",
            login="bob",
            adhoc_runner=adhoc,
        )
    )

    assert received_repo["repo"] == "bob/knowl"


def test_dispatch_responds_when_busy() -> None:
    captured, ack, respond = _record()

    async def adhoc(*, repo: str, task: str, user: str) -> AdhocResult:
        return AdhocResult(kind=AdhocResultKind.BUSY, reason="another cycle running")

    asyncio.run(
        dispatch_command(
            ack=ack,
            respond=respond,
            text="run owner/repo 何か",
            user_name="alice",
            login="bob",
            adhoc_runner=adhoc,
        )
    )

    assert captured.responds[0].startswith("了解")
    assert any("他のサイクル" in r for r in captured.responds)


def test_dispatch_responds_when_gate_blocks() -> None:
    captured, ack, respond = _record()

    async def adhoc(*, repo: str, task: str, user: str) -> AdhocResult:
        return AdhocResult(kind=AdhocResultKind.GATE_BLOCKED, reason="session below")

    asyncio.run(
        dispatch_command(
            ack=ack,
            respond=respond,
            text="run owner/repo 何か",
            user_name="alice",
            login="bob",
            adhoc_runner=adhoc,
        )
    )

    assert captured.responds[0].startswith("了解")
    assert any("rate limit" in r for r in captured.responds)
    assert any("無理だったすまん" in r for r in captured.responds)


def test_dispatch_responds_with_usage_on_parse_error() -> None:
    captured, ack, respond = _record()

    async def adhoc(*, repo: str, task: str, user: str) -> AdhocResult:
        raise AssertionError("must not be called on parse error")

    asyncio.run(
        dispatch_command(
            ack=ack,
            respond=respond,
            text="run knowl",
            user_name="alice",
            login="bob",
            adhoc_runner=adhoc,
        )
    )

    assert captured.acks == 1
    assert len(captured.responds) == 1
    assert "usage" in captured.responds[0].lower()


def test_dispatch_responds_on_adhoc_error() -> None:
    captured, ack, respond = _record()

    async def adhoc(*, repo: str, task: str, user: str) -> AdhocResult:
        return AdhocResult(
            kind=AdhocResultKind.ERROR, reason="repository 'foo/bar' is not registered"
        )

    asyncio.run(
        dispatch_command(
            ack=ack,
            respond=respond,
            text="run foo/bar do this",
            user_name="alice",
            login="bob",
            adhoc_runner=adhoc,
        )
    )

    assert captured.responds[0].startswith("了解")
    assert any("foo/bar" in r and "registered" in r for r in captured.responds)


def test_dispatch_responds_with_generic_message_on_unexpected_exception() -> None:
    captured, ack, respond = _record()

    async def adhoc(*, repo: str, task: str, user: str) -> AdhocResult:
        raise RuntimeError("internal: secret detail leaked here")

    asyncio.run(
        dispatch_command(
            ack=ack,
            respond=respond,
            text="run owner/repo task",
            user_name="alice",
            login="bob",
            adhoc_runner=adhoc,
        )
    )

    # 例外の生メッセージは Slack へ流さず固定文言にする (秘匿性 + ユーザフレンドリ)
    assert captured.responds[0].startswith("了解")
    last = captured.responds[-1]
    assert "secret detail" not in last
    assert "予期せぬエラー" in last
