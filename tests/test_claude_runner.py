"""knowl.claude_runner のテスト."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

import pytest

from knowl.claude_runner import (
    ClaudeError,
    ClaudeResult,
    escalate_limit_reached,
    extract_text,
    run_claude_local,
)
from knowl.usage import UsageSnapshot


def _ok(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=0, stdout=json.dumps(payload), stderr=""
    )


def test_extract_text_from_result_message() -> None:
    payload = {"type": "result", "result": "hello", "is_error": False}
    assert extract_text(payload) == "hello"


def test_extract_text_from_messages_list() -> None:
    payload = {
        "messages": [
            {"role": "assistant", "content": [{"type": "text", "text": "yo"}]}
        ]
    }
    assert extract_text(payload) == "yo"


def test_extract_text_raises_when_not_found() -> None:
    with pytest.raises(ClaudeError):
        extract_text({"foo": "bar"})


def test_run_claude_local_invokes_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Sequence[str]] = []

    def fake_run(
        cmd: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        timeout: float | None = None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return _ok({"type": "result", "result": "ok"})

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_claude_local("do a thing", model="claude-opus-4-7")
    assert isinstance(result, ClaudeResult)
    assert result.text == "ok"
    assert result.payload["type"] == "result"

    cmd = calls[0]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "--model" in cmd and "claude-opus-4-7" in cmd
    assert "--output-format" in cmd and "json" in cmd
    # プロンプトは末尾の位置引数
    assert cmd[-1] == "do a thing"


def test_run_claude_local_raises_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(
        cmd: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        timeout: float | None = None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=2, stdout="", stderr="rate limited"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ClaudeError) as exc:
        run_claude_local("hi")
    assert "rate limited" in str(exc.value)


def test_run_claude_local_raises_on_is_error_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(
        cmd: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        timeout: float | None = None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return _ok({"type": "result", "result": "boom", "is_error": True})

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ClaudeError):
        run_claude_local("hi")


def test_run_claude_local_detects_limit_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(
        cmd: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        timeout: float | None = None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=list(cmd),
            returncode=1,
            stdout="",
            stderr="usage limit reached",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ClaudeError) as exc:
        run_claude_local("hi")
    assert exc.value.limit_reached is True
    # snapshot は明示されていなければ None。
    assert exc.value.usage is None


def test_claude_error_accepts_usage_snapshot() -> None:
    snap = UsageSnapshot(session_remaining_pct=20, weekly_remaining_pct=80)
    exc = ClaudeError("boom", usage=snap)
    assert exc.usage is snap
    # 明示されない限り limit_reached は変わらない (escalation は別関数の責務)。
    assert exc.limit_reached is False


def test_escalate_limit_reached_keeps_false_for_healthy_usage() -> None:
    """通常レンジの usage では stderr ヒント無しの ClaudeError をそのまま扱う."""
    snap = UsageSnapshot(session_remaining_pct=80, weekly_remaining_pct=80)
    exc = ClaudeError("internal error")
    escalate_limit_reached(exc, snap, session_threshold=30, weekly_threshold=10)
    assert exc.limit_reached is False
    assert exc.usage is snap


def test_escalate_limit_reached_keeps_false_just_above_threshold() -> None:
    """閾値ちょうど (= gate 通過ライン) では limit 扱いしない."""
    snap = UsageSnapshot(session_remaining_pct=30, weekly_remaining_pct=10)
    exc = ClaudeError("internal error")
    escalate_limit_reached(exc, snap, session_threshold=30, weekly_threshold=10)
    assert exc.limit_reached is False


def test_escalate_limit_reached_promotes_when_session_below_threshold() -> None:
    """5h 残量が閾値割れなら stderr ヒント無しでも limit_reached=True に昇格."""
    snap = UsageSnapshot(session_remaining_pct=5, weekly_remaining_pct=80)
    exc = ClaudeError("internal error")
    escalate_limit_reached(exc, snap, session_threshold=30, weekly_threshold=10)
    assert exc.limit_reached is True
    assert exc.usage is snap


def test_escalate_limit_reached_promotes_when_weekly_below_threshold() -> None:
    """週次残量が閾値割れでも limit_reached=True に昇格."""
    snap = UsageSnapshot(session_remaining_pct=80, weekly_remaining_pct=2)
    exc = ClaudeError("internal error")
    escalate_limit_reached(exc, snap, session_threshold=30, weekly_threshold=10)
    assert exc.limit_reached is True


def test_escalate_limit_reached_preserves_existing_true() -> None:
    """既に limit_reached=True なら usage を後付けしても True のまま."""
    snap = UsageSnapshot(session_remaining_pct=80, weekly_remaining_pct=80)
    exc = ClaudeError("usage limit reached", limit_reached=True)
    escalate_limit_reached(exc, snap, session_threshold=30, weekly_threshold=10)
    assert exc.limit_reached is True
    assert exc.usage is snap


def test_escalate_limit_reached_does_not_overwrite_existing_usage() -> None:
    """既に usage が添えられていれば後付け snapshot で上書きしない."""
    initial = UsageSnapshot(session_remaining_pct=5, weekly_remaining_pct=80)
    later = UsageSnapshot(session_remaining_pct=80, weekly_remaining_pct=80)
    exc = ClaudeError("boom", usage=initial)
    escalate_limit_reached(exc, later, session_threshold=30, weekly_threshold=10)
    assert exc.usage is initial
