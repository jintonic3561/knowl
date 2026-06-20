"""コンテナ操作.

docker / devcontainer の双方を ``docker`` CLI 経由で扱う薄いラッパ。
devcontainer であっても起動済みのコンテナを対象に ``docker exec`` を流すだけ
(devcontainers CLI を要求しない)。
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from knowl._proc import run_checked
from knowl.config import ContainerConfig

_INSPECT_TIMEOUT = 10.0
_START_TIMEOUT = 30.0
_DEFAULT_EXEC_TIMEOUT = 60.0 * 60.0  # 1h


class ContainerError(RuntimeError):
    """container 操作失敗."""


@dataclass(frozen=True, slots=True)
class ContainerExecResult:
    returncode: int
    stdout: str
    stderr: str


def _inspect_running(name: str) -> bool:
    result = run_checked(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        error_cls=ContainerError,
        label=f"docker inspect for {name}",
        timeout=_INSPECT_TIMEOUT,
    )
    return result.stdout.strip().lower() == "true"


def ensure_running(container: ContainerConfig) -> None:
    """対象コンテナを必要なら起動する."""
    if _inspect_running(container.name):
        return
    run_checked(
        ["docker", "start", container.name],
        error_cls=ContainerError,
        label=f"docker start for {container.name}",
        timeout=_START_TIMEOUT,
    )


def exec_in_container(
    container: ContainerConfig,
    argv: Sequence[str],
    *,
    workdir: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = _DEFAULT_EXEC_TIMEOUT,
) -> ContainerExecResult:
    """コンテナ内でコマンドを実行する(停止していれば自動起動)."""
    ensure_running(container)
    cmd: list[str] = ["docker", "exec"]
    if container.user:
        cmd.extend(["--user", container.user])
    if workdir:
        cmd.extend(["-w", workdir])
    if env:
        for key, value in env.items():
            cmd.extend(["-e", f"{key}={value}"])
    cmd.append(container.name)
    # docker exec は非対話実行のため、シェル hook 型ツール (direnv 等) が発火しない。
    # 明示的な exec ラッパ (direnv exec / mise exec --) を argv の前に prepend する。
    final_argv: list[str] = (
        [*container.exec_prefix, *argv] if container.exec_prefix else list(argv)
    )
    if container.user:
        # 非 root user の home 配下にしか PATH が通らないバイナリ
        # (例: devcontainer の ~/.local/bin/claude) を呼べるよう login shell でラップ。
        # argv は shlex.join で安全にエスケープ。
        cmd.extend(["bash", "-lc", shlex.join(final_argv)])
    else:
        cmd.extend(final_argv)
    result = run_checked(
        cmd,
        error_cls=ContainerError,
        label=f"docker exec for {container.name}: {' '.join(argv)}",
        timeout=timeout,
        check=False,
    )
    return ContainerExecResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )
