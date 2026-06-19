"""Slack slash command (`/knowl`) ハンドラ.

knowl 常駐コンテナに asyncio task として相乗りさせる Slack bot 実装。
ハンドリングはテスト容易化のため、 ``dispatch_command`` を独立した async 関数として
切り出し、 ``slack_bolt`` の ``AsyncApp.command`` デコレータは ``build_app`` 内で
それをラップする薄い接続層として扱う。
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from collections.abc import Awaitable, Callable

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from knowl.adhoc import AdhocResult, AdhocResultKind

_LOG = logging.getLogger(__name__)

_USAGE = "usage: /knowl run <repo> <task description>"
_ACK_MESSAGE = "了解。バックグラウンドで判定するので少し待って。"
_GATE_BLOCKED_MESSAGE = "rate limit きつくて無理だったすまん。落ち着いたら再投入して。"
_BUSY_MESSAGE = "他のサイクル実行中。少し待って再投入して。"
_UNEXPECTED_ERROR_MESSAGE = "予期せぬエラー。コンテナのログを確認して。"


class SlackCommandError(ValueError):
    """slash command のパース失敗."""


AdhocRunner = Callable[..., Awaitable[AdhocResult]]


def parse_command_text(text: str) -> tuple[str, str, str]:
    """slash command の text を ``(sub, repo, task)`` に分解する.

    ``run <repo> <task...>`` 以外は ``SlackCommandError``。
    """
    try:
        parts = shlex.split(text)
    except ValueError as exc:
        raise SlackCommandError(_USAGE) from exc
    if len(parts) < 3:
        raise SlackCommandError(_USAGE)
    sub, repo, *rest = parts
    if sub != "run":
        raise SlackCommandError(_USAGE)
    task = " ".join(rest).strip()
    if not task:
        raise SlackCommandError(_USAGE)
    return sub, repo, task


def resolve_repo(repo_arg: str, *, login: str) -> str:
    """`<repo>` が `owner/name` ならそのまま、 `name` のみなら ``login/name``."""
    if "/" in repo_arg:
        return repo_arg
    return f"{login}/{repo_arg}"


async def dispatch_command(
    *,
    ack: Callable[[], Awaitable[None]],
    respond: Callable[[str], Awaitable[None]],
    text: str,
    user_name: str,
    login: str,
    adhoc_runner: AdhocRunner,
) -> None:
    """`/knowl` 受信時の中核ハンドラ.

    1. ack() 即時 (slash command の 3 秒制限)
    2. respond("了解...") 即時
    3. adhoc_runner を呼んで結果を返す
    4. gate ブロック / エラー時のみ追加 respond
    """
    await ack()
    try:
        _, repo_arg, task = parse_command_text(text)
    except SlackCommandError as exc:
        await respond(str(exc))
        return

    repo = resolve_repo(repo_arg, login=login)
    await respond(_ACK_MESSAGE)

    try:
        outcome = await adhoc_runner(repo=repo, task=task, user=user_name)
    except Exception:
        _LOG.exception("ad-hoc runner failed unexpectedly")
        await respond(_UNEXPECTED_ERROR_MESSAGE)
        return

    if outcome.kind is AdhocResultKind.GATE_BLOCKED:
        await respond(_GATE_BLOCKED_MESSAGE)
    elif outcome.kind is AdhocResultKind.BUSY:
        await respond(_BUSY_MESSAGE)
    elif outcome.kind is AdhocResultKind.ERROR:
        await respond(f"エラー: {outcome.reason}")
    # OK のときは既存 Slack 通知パスに任せる (二重通知回避)


def build_app(
    *,
    bot_token: str,
    login: str,
    adhoc_runner: AdhocRunner,
) -> AsyncApp:
    """`/knowl` を受け付ける ``AsyncApp`` を組み立てる."""
    app = AsyncApp(token=bot_token)

    @app.command("/knowl")
    async def _on_knowl(ack, command, respond):  # type: ignore[no-untyped-def]
        text_raw = command.get("text") or ""
        user = command.get("user_name") or "unknown"
        await dispatch_command(
            ack=ack,
            respond=respond,
            text=text_raw,
            user_name=user,
            login=login,
            adhoc_runner=adhoc_runner,
        )

    return app


async def start_bot(
    *,
    bot_token: str,
    app_token: str,
    login: str,
    adhoc_runner: AdhocRunner,
) -> None:
    """Socket Mode で `/knowl` を listen し続ける (常駐タスク)."""
    app = build_app(bot_token=bot_token, login=login, adhoc_runner=adhoc_runner)
    handler = AsyncSocketModeHandler(app, app_token)
    _LOG.info("starting Slack bot in Socket Mode (login=%s)", login)
    await handler.start_async()  # type: ignore[no-untyped-call]


def run_bot_forever(
    *,
    bot_token: str,
    app_token: str,
    login: str,
    adhoc_runner: AdhocRunner,
) -> None:
    """`start_bot` を asyncio.run で起動する同期エントリ."""
    asyncio.run(
        start_bot(
            bot_token=bot_token,
            app_token=app_token,
            login=login,
            adhoc_runner=adhoc_runner,
        )
    )


__all__ = [
    "AdhocRunner",
    "SlackCommandError",
    "build_app",
    "dispatch_command",
    "parse_command_text",
    "resolve_repo",
    "run_bot_forever",
    "start_bot",
]
