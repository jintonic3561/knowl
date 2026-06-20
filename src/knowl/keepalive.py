"""OAuth トークンの自動 keepalive.

`~/.claude/.credentials.json` の expiresAt が閾値を下回ったら、`claude -p` を
極短プロンプトで叩く。これにより Claude Code CLI が refreshToken を使って
access token を更新し、ファイルが書き換わる。

cron から ``knowl keepalive`` で呼ぶ想定。判定が False のサイクルでは何もしない。
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from knowl._proc import run_checked
from knowl.usage import (
    DEFAULT_CREDENTIALS_PATH,
    OAuthToken,
    UsageError,
    load_oauth_credentials,
)

_LOG = logging.getLogger(__name__)

DEFAULT_THRESHOLD_MS = 2 * 3600 * 1000
DEFAULT_CLAUDE_ARGS: tuple[str, ...] = ("claude", "-p", "ok")
# `claude -p ok` は実測で 3〜10 秒で返るが、モデル混雑時の余裕を見て大きめ。
DEFAULT_TIMEOUT_S = 300.0

Runner = Callable[[Sequence[str]], int]


@dataclass(frozen=True, slots=True)
class KeepaliveResult:
    refreshed: bool
    reason: str
    remaining_ms: int | None


def should_refresh(token: OAuthToken, *, now_ms: int, threshold_ms: int) -> bool:
    """期限まで threshold_ms 未満なら True。期限不明なら触らない (False)."""
    if token.expires_at_ms is None:
        return False
    return (token.expires_at_ms - now_ms) < threshold_ms


def _default_runner(argv: Sequence[str], *, timeout_s: float) -> int:
    proc = run_checked(
        list(argv),
        error_cls=UsageError,
        label=f"claude keepalive {list(argv)}",
        timeout=timeout_s,
        check=False,
    )
    if proc.stdout:
        _LOG.debug("claude stdout: %s", proc.stdout.strip()[:200])
    if proc.stderr:
        _LOG.debug("claude stderr: %s", proc.stderr.strip()[:200])
    return proc.returncode


def keepalive_once(
    *,
    credentials_path: Path = DEFAULT_CREDENTIALS_PATH,
    threshold_ms: int = DEFAULT_THRESHOLD_MS,
    now_ms: int | None = None,
    dry_run: bool = False,
    claude_argv: Sequence[str] = DEFAULT_CLAUDE_ARGS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    runner: Runner | None = None,
) -> KeepaliveResult:
    """1 サイクル分の判定 + 必要なら refresh を行う."""
    token = load_oauth_credentials(credentials_path)
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    remaining = (
        token.expires_at_ms - current_ms if token.expires_at_ms is not None else None
    )

    if not should_refresh(token, now_ms=current_ms, threshold_ms=threshold_ms):
        reason = (
            f"skip: remaining={remaining}ms threshold={threshold_ms}ms"
            if remaining is not None
            else "skip: expiresAt unknown"
        )
        _LOG.info(reason)
        return KeepaliveResult(refreshed=False, reason=reason, remaining_ms=remaining)

    if dry_run:
        reason = f"dry-run: would refresh (remaining={remaining}ms)"
        _LOG.info(reason)
        return KeepaliveResult(refreshed=False, reason=reason, remaining_ms=remaining)

    _LOG.info(
        "refresh triggered: remaining=%sms threshold=%sms argv=%s",
        remaining,
        threshold_ms,
        list(claude_argv),
    )

    run: Runner = runner if runner is not None else (
        lambda argv: _default_runner(argv, timeout_s=timeout_s)
    )
    try:
        rc = run(claude_argv)
    except subprocess.TimeoutExpired as exc:
        raise UsageError(
            f"claude keepalive command timed out after {exc.timeout}s: {list(claude_argv)}"
        ) from exc
    except FileNotFoundError as exc:
        raise UsageError(
            f"claude binary not found while invoking {list(claude_argv)}: {exc}"
        ) from exc
    if rc != 0:
        raise UsageError(
            f"claude keepalive command failed with exit {rc}: {list(claude_argv)}"
        )

    reason = f"refreshed: invoked {list(claude_argv)}"
    _LOG.info(reason)
    return KeepaliveResult(refreshed=True, reason=reason, remaining_ms=remaining)


__all__ = [
    "DEFAULT_CLAUDE_ARGS",
    "DEFAULT_THRESHOLD_MS",
    "DEFAULT_TIMEOUT_S",
    "KeepaliveResult",
    "Runner",
    "keepalive_once",
    "should_refresh",
]
