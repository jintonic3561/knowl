"""1 サイクルあたりの相互排他ロック.

cron 側 (`docker/run-cycle.sh`) は shell の ``flock -n`` で
``/var/run/knowl-cycle.lock`` を取得する。 ad-hoc 起動 (Slack slash command) は
Python から直接 ``run_adhoc`` を呼ぶので同じ shell ロックを通らない。両者が
対象 container で同時に ``claude -p`` を叩くと、対象 repo の git working tree
で同時にブランチ切り・コミット・push が走り、PR が壊れる。

そこで Python 側からも同じ path に ``fcntl.flock`` をかけ、両系統が同じ
カーネルレベルの advisory lock を共有する形にする。
"""

from __future__ import annotations

import fcntl
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_LOG = logging.getLogger(__name__)

DEFAULT_CYCLE_LOCK_PATH = Path(
    os.environ.get("KNOWL_CYCLE_LOCK", "/var/run/knowl-cycle.lock")
)


@contextmanager
def cycle_lock(path: Path = DEFAULT_CYCLE_LOCK_PATH) -> Iterator[bool]:
    """サイクル排他用 lock を non-blocking で取得する.

    ``with`` 値が ``True`` ならロック取得済、 ``False`` なら他プロセスが保持中。
    ロックは ``with`` を抜けるとファイル close により自動解放される
    (fcntl.flock は fd close で自動 UN するが、明示的に解放した方が読みやすい)。
    """
    fd: int | None = None
    acquired = False
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            acquired = False
        yield acquired
    finally:
        if fd is not None:
            if acquired:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError as exc:
                    _LOG.warning("cycle_lock release failed: %s", exc)
            os.close(fd)
