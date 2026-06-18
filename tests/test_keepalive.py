"""knowl.keepalive のテスト."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from knowl.keepalive import (
    DEFAULT_THRESHOLD_MS,
    KeepaliveResult,
    keepalive_once,
    should_refresh,
)
from knowl.usage import OAuthToken, UsageError

NOW_MS = 1_000_000_000_000  # 適当な現在時刻 (ms)


def _token(expires_at_ms: int | None) -> OAuthToken:
    return OAuthToken(access_token="tok", expires_at_ms=expires_at_ms)


# ---- should_refresh ---------------------------------------------------------


def test_should_refresh_true_when_remaining_below_threshold() -> None:
    # 残り 30 分 < 閾値 2h
    token = _token(NOW_MS + 30 * 60 * 1000)
    assert should_refresh(token, now_ms=NOW_MS, threshold_ms=2 * 3600 * 1000) is True


def test_should_refresh_false_when_remaining_above_threshold() -> None:
    # 残り 5h > 閾値 2h
    token = _token(NOW_MS + 5 * 3600 * 1000)
    assert should_refresh(token, now_ms=NOW_MS, threshold_ms=2 * 3600 * 1000) is False


def test_should_refresh_true_when_already_expired() -> None:
    token = _token(NOW_MS - 1)
    assert should_refresh(token, now_ms=NOW_MS, threshold_ms=2 * 3600 * 1000) is True


def test_should_refresh_false_when_expires_unknown() -> None:
    # 期限不明は触らない (安全側)
    assert should_refresh(_token(None), now_ms=NOW_MS, threshold_ms=2 * 3600 * 1000) is False


def test_should_refresh_boundary_exactly_at_threshold() -> None:
    # 残り = 閾値 ちょうど。境界は「未満で refresh」なので False。
    threshold_ms = 2 * 3600 * 1000
    token = _token(NOW_MS + threshold_ms)
    assert should_refresh(token, now_ms=NOW_MS, threshold_ms=threshold_ms) is False


# ---- keepalive_once ---------------------------------------------------------


def _write_creds(path: Path, expires_at_ms: int) -> None:
    path.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "tok",
                    "expiresAt": expires_at_ms,
                }
            }
        ),
        encoding="utf-8",
    )


def test_keepalive_once_skips_when_remaining_above_threshold(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    _write_creds(creds, NOW_MS + 5 * 3600 * 1000)

    called: list[Sequence[str]] = []

    def runner(argv: Sequence[str]) -> int:
        called.append(argv)
        return 0

    result = keepalive_once(
        credentials_path=creds,
        threshold_ms=2 * 3600 * 1000,
        now_ms=NOW_MS,
        runner=runner,
    )

    assert isinstance(result, KeepaliveResult)
    assert result.refreshed is False
    assert "skip" in result.reason.lower()
    assert called == []


def test_keepalive_once_invokes_runner_when_below_threshold(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    _write_creds(creds, NOW_MS + 30 * 60 * 1000)

    called: list[Sequence[str]] = []

    def runner(argv: Sequence[str]) -> int:
        called.append(argv)
        return 0

    result = keepalive_once(
        credentials_path=creds,
        threshold_ms=2 * 3600 * 1000,
        now_ms=NOW_MS,
        runner=runner,
    )

    assert result.refreshed is True
    assert len(called) == 1
    argv = list(called[0])
    assert argv[0] == "claude"
    assert "-p" in argv


def test_keepalive_once_dry_run_does_not_invoke_runner(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    _write_creds(creds, NOW_MS + 30 * 60 * 1000)

    called: list[Sequence[str]] = []

    def runner(argv: Sequence[str]) -> int:
        called.append(argv)
        return 0

    result = keepalive_once(
        credentials_path=creds,
        threshold_ms=2 * 3600 * 1000,
        now_ms=NOW_MS,
        runner=runner,
        dry_run=True,
    )

    assert result.refreshed is False
    assert "dry" in result.reason.lower()
    assert called == []


def test_keepalive_once_propagates_runner_failure(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    _write_creds(creds, NOW_MS + 30 * 60 * 1000)

    def runner(argv: Sequence[str]) -> int:
        return 7

    with pytest.raises(UsageError, match="exit 7"):
        keepalive_once(
            credentials_path=creds,
            threshold_ms=2 * 3600 * 1000,
            now_ms=NOW_MS,
            runner=runner,
        )


def test_keepalive_once_runner_timeout_is_usage_error(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    _write_creds(creds, NOW_MS + 30 * 60 * 1000)

    def runner(argv: Sequence[str]) -> int:
        raise subprocess.TimeoutExpired(cmd=list(argv), timeout=1.0)

    with pytest.raises(UsageError, match="timed out"):
        keepalive_once(
            credentials_path=creds,
            threshold_ms=2 * 3600 * 1000,
            now_ms=NOW_MS,
            runner=runner,
        )


def test_default_threshold_is_two_hours() -> None:
    assert DEFAULT_THRESHOLD_MS == 2 * 3600 * 1000
