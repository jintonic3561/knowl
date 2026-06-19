"""knowl._lock の排他テスト."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from knowl._lock import cycle_lock


def test_cycle_lock_acquires(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    with cycle_lock(lock) as acquired:
        assert acquired is True


def test_cycle_lock_excludes_second_acquire(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    with cycle_lock(lock) as outer:
        assert outer is True
        with cycle_lock(lock) as inner:
            assert inner is False


def test_cycle_lock_released_after_with(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    with cycle_lock(lock) as outer:
        assert outer is True
    with cycle_lock(lock) as again:
        assert again is True


@pytest.mark.skipif(shutil.which("flock") is None, reason="util-linux flock unavailable")
def test_cycle_lock_excludes_shell_flock(tmp_path: Path) -> None:
    """cron 側 (run-cycle.sh の `flock -n 9`) と Python 側 (fcntl.flock) が
    同じ ``/var/run/knowl-cycle.lock`` で排他することを担保する回帰テスト.

    util-linux ``flock(1)`` で sleep プロセスをホストし、その間に
    ``cycle_lock`` が False を返すことを確認する。
    """
    lock = tmp_path / "lock"
    # 子側でロックを取った合図用に touch するファイル
    ready = tmp_path / "ready"
    script = (
        f"flock -x {lock} sh -c '"
        f"touch {ready}; sleep 0.5'"
    )
    proc = subprocess.Popen(["sh", "-c", script])
    try:
        deadline = time.monotonic() + 2.0
        while not ready.exists():
            if time.monotonic() > deadline:
                pytest.fail("child flock process did not acquire lock in time")
            time.sleep(0.02)
        # 子が flock を握っている間、 Python 側の cycle_lock は取れないこと
        with cycle_lock(lock) as acquired:
            assert acquired is False
    finally:
        proc.wait(timeout=3)
    # 子が release した後は再度取れること
    with cycle_lock(lock) as again:
        assert again is True
