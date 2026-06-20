"""knowl._proc のテスト."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any

import pytest

from knowl._proc import run_checked


class _Boom(RuntimeError):
    """テスト用カスタム例外."""


def _fake_factory(
    rc: int = 0,
    stdout: str = "",
    stderr: str = "",
    raises: BaseException | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []

    def _fake(
        cmd: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None = None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        seen.append(
            {
                "cmd": list(cmd),
                "capture_output": capture_output,
                "text": text,
                "check": check,
                "timeout": timeout,
                "input": input,
            }
        )
        if raises is not None:
            raise raises
        if check and rc != 0:
            raise subprocess.CalledProcessError(rc, list(cmd), stdout, stderr)
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=rc, stdout=stdout, stderr=stderr
        )

    return _fake, seen


def test_run_checked_returns_completed_process(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, seen = _fake_factory(rc=0, stdout="ok\n")
    monkeypatch.setattr(subprocess, "run", fake)

    result = run_checked(
        ["echo", "hi"], error_cls=_Boom, label="echo", timeout=5.0
    )

    assert result.returncode == 0
    assert result.stdout == "ok\n"
    assert seen[0]["cmd"] == ["echo", "hi"]
    assert seen[0]["capture_output"] is True
    assert seen[0]["text"] is True
    assert seen[0]["check"] is True
    assert seen[0]["timeout"] == 5.0


def test_run_checked_wraps_called_process_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_factory(rc=2, stdout="", stderr="boom")
    monkeypatch.setattr(subprocess, "run", fake)

    with pytest.raises(_Boom) as exc:
        run_checked(["false"], error_cls=_Boom, label="something")

    assert "something" in str(exc.value)
    assert "boom" in str(exc.value)
    # exc chain で元の CalledProcessError が辿れる
    assert isinstance(exc.value.__cause__, subprocess.CalledProcessError)


def test_run_checked_wraps_called_process_error_uses_stdout_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, _ = _fake_factory(rc=2, stdout="out-only", stderr="")
    monkeypatch.setattr(subprocess, "run", fake)

    with pytest.raises(_Boom) as exc:
        run_checked(["false"], error_cls=_Boom, label="foo")

    assert "out-only" in str(exc.value)


def test_run_checked_wraps_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_factory(
        raises=subprocess.TimeoutExpired(cmd=["sleep"], timeout=1.0)
    )
    monkeypatch.setattr(subprocess, "run", fake)

    with pytest.raises(_Boom) as exc:
        run_checked(["sleep", "10"], error_cls=_Boom, label="naptime")

    assert "naptime" in str(exc.value)
    assert "timed out" in str(exc.value)
    assert isinstance(exc.value.__cause__, subprocess.TimeoutExpired)


def test_run_checked_check_false_skips_called_process_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """check=False のとき、rc != 0 は呼び出し側が後段で処理する."""
    fake, _ = _fake_factory(rc=3, stdout="meh", stderr="bad")
    monkeypatch.setattr(subprocess, "run", fake)

    result = run_checked(["foo"], error_cls=_Boom, label="foo", check=False)

    assert result.returncode == 3
    assert result.stderr == "bad"


def test_run_checked_passes_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """input= は subprocess.run に転送される (claude_runner の stdin 経路用)."""
    fake, seen = _fake_factory(rc=0, stdout="ok")
    monkeypatch.setattr(subprocess, "run", fake)

    run_checked(["cat"], error_cls=_Boom, label="cat", input="hello")

    assert seen[0]["input"] == "hello"
