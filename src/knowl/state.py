"""idle 状態の永続化 I/O.

cron で 1 サイクル毎に呼び出される CLI が、前回サイクルが idle (進めるべき
issue 無し) だったかを記憶しておくための小さな JSON ファイル I/O。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_LOG = logging.getLogger(__name__)

DEFAULT_STATE_DIR = Path("/var/lib/knowl")
IDLE_STATE_FILENAME = "idle_state.json"


def idle_state_path() -> Path:
    """前回 idle フラグを保存するパスを返す."""
    base = os.environ.get("KNOWL_STATE_DIR")
    return (Path(base) if base else DEFAULT_STATE_DIR) / IDLE_STATE_FILENAME


def load_last_idle(path: Path) -> bool:
    """前回サイクルが idle だったかをロード。読み取れない場合は False."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("idle state load failed (treating as not-idle): %s", exc)
        return False
    return bool(data.get("last_idle", False)) if isinstance(data, dict) else False


def save_last_idle(path: Path, value: bool) -> None:
    """idle フラグを保存。失敗してもサイクル全体は止めない."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_idle": value}), encoding="utf-8")
    except OSError as exc:
        _LOG.warning("idle state save failed: %s", exc)


__all__ = [
    "DEFAULT_STATE_DIR",
    "IDLE_STATE_FILENAME",
    "idle_state_path",
    "load_last_idle",
    "save_last_idle",
]
