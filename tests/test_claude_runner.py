"""knowl.claude_runner のテスト."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

import pytest

from knowl.claude_runner import (
    ClaudeError,
    ClaudeResult,
    extract_text,
    run_claude_local,
)


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
