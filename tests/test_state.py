"""knowl.state のテスト.

idle 状態の永続化 I/O を CLI 層から切り出した小さなモジュールを直接テストする。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from knowl.state import (
    DEFAULT_STATE_DIR,
    IDLE_STATE_FILENAME,
    idle_state_path,
    load_last_idle,
    save_last_idle,
)


def test_idle_state_path_uses_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWL_STATE_DIR", "/tmp/knowl-test")
    assert idle_state_path() == Path("/tmp/knowl-test") / IDLE_STATE_FILENAME


def test_idle_state_path_defaults_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KNOWL_STATE_DIR", raising=False)
    assert idle_state_path() == DEFAULT_STATE_DIR / IDLE_STATE_FILENAME


def test_load_last_idle_returns_false_when_missing(tmp_path: Path) -> None:
    assert load_last_idle(tmp_path / "missing.json") is False


def test_load_last_idle_reads_true(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"last_idle": True}), encoding="utf-8")
    assert load_last_idle(path) is True


def test_load_last_idle_reads_false(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"last_idle": False}), encoding="utf-8")
    assert load_last_idle(path) is False


def test_load_last_idle_invalid_json_returns_false(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "state.json"
    path.write_text("not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="knowl.state"):
        assert load_last_idle(path) is False
    # 失敗内容は warn に出すが exception は外に漏らさない
    assert any("idle state load failed" in r.message for r in caplog.records)


def test_load_last_idle_non_dict_returns_false(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[true]", encoding="utf-8")
    assert load_last_idle(path) is False


def test_load_last_idle_missing_key_returns_false(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"other": True}), encoding="utf-8")
    assert load_last_idle(path) is False


def test_save_last_idle_creates_parent_and_writes(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "state.json"
    save_last_idle(path, True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"last_idle": True}


def test_save_last_idle_round_trip_false(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_last_idle(path, False)
    assert load_last_idle(path) is False


def test_save_last_idle_swallows_oserror(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """書き込みに失敗してもサイクル本体を止めない (warn のみ)."""
    path = tmp_path / "state.json"

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    with caplog.at_level(logging.WARNING, logger="knowl.state"):
        save_last_idle(path, True)
    assert any("idle state save failed" in r.message for r in caplog.records)
