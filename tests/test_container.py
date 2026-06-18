"""knowl.container のテスト."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

import pytest

from knowl.config import ContainerConfig, ContainerKind
from knowl.container import (
    ContainerError,
    ContainerExecResult,
    ensure_running,
    exec_in_container,
)


class RunRecorder:
    def __init__(self, scripted: list[tuple[int, str, str]]) -> None:
        self.scripted = scripted
        self.calls: list[Sequence[str]] = []

    def __call__(
        self,
        cmd: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        rc, out, err = self.scripted.pop(0)
        if check and rc != 0:
            raise subprocess.CalledProcessError(rc, list(cmd), out, err)
        return subprocess.CompletedProcess(args=list(cmd), returncode=rc, stdout=out, stderr=err)


def docker_cfg(name: str = "widgets-dev", user: str | None = None) -> ContainerConfig:
    return ContainerConfig(kind=ContainerKind.DOCKER, name=name, user=user)


def devcontainer_cfg(name: str = "widgets-dev", user: str | None = None) -> ContainerConfig:
    return ContainerConfig(kind=ContainerKind.DEVCONTAINER, name=name, user=user)


def test_ensure_running_skips_start_when_already_running(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder([(0, "true\n", "")])  # docker inspect → running=true
    monkeypatch.setattr(subprocess, "run", rec)

    ensure_running(docker_cfg())

    assert rec.calls == [["docker", "inspect", "-f", "{{.State.Running}}", "widgets-dev"]]


def test_ensure_running_starts_when_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder([(0, "false\n", ""), (0, "widgets-dev\n", "")])
    monkeypatch.setattr(subprocess, "run", rec)

    ensure_running(docker_cfg())

    assert rec.calls[0][0:2] == ["docker", "inspect"]
    assert rec.calls[1][0:2] == ["docker", "start"]


def test_ensure_running_raises_when_container_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(
        cmd: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            returncode=1, cmd=list(cmd), output="", stderr="No such container"
        )

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(ContainerError):
        ensure_running(docker_cfg())


def test_exec_in_container_uses_docker_exec(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder([(0, "true\n", ""), (0, "hi\n", "")])
    monkeypatch.setattr(subprocess, "run", rec)

    result = exec_in_container(docker_cfg(), ["echo", "hi"], workdir="/work")

    assert isinstance(result, ContainerExecResult)
    assert result.stdout == "hi\n"
    assert result.returncode == 0
    # 1 回目は inspect、2 回目に exec
    assert rec.calls[1][0:2] == ["docker", "exec"]
    assert "-w" in rec.calls[1] and "/work" in rec.calls[1]
    assert rec.calls[1][-2:] == ["echo", "hi"]


def test_exec_in_container_devcontainer_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """devcontainer kind も docker exec を発行する(単一バックエンド)."""
    rec = RunRecorder([(0, "true\n", ""), (0, "ok\n", "")])
    monkeypatch.setattr(subprocess, "run", rec)
    exec_in_container(devcontainer_cfg(), ["ls"], workdir="/workspaces/widgets")
    assert rec.calls[1][0:2] == ["docker", "exec"]


def test_exec_in_container_propagates_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder([(0, "true\n", ""), (3, "out", "err")])
    monkeypatch.setattr(subprocess, "run", rec)

    result = exec_in_container(docker_cfg(), ["false"], workdir="/work")

    assert result.returncode == 3
    assert result.stdout == "out"
    assert result.stderr == "err"


def test_exec_in_container_supports_env(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder([(0, "true\n", ""), (0, "", "")])
    monkeypatch.setattr(subprocess, "run", rec)

    exec_in_container(
        docker_cfg(),
        ["env"],
        workdir="/work",
        env={"FOO": "bar"},
    )

    flat = rec.calls[1]
    assert "-e" in flat
    assert "FOO=bar" in flat


def test_exec_in_container_passes_user_flag_and_wraps_login_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = RunRecorder([(0, "true\n", ""), (0, "", "")])
    monkeypatch.setattr(subprocess, "run", rec)

    exec_in_container(devcontainer_cfg(user="vscode"), ["claude", "-p"], workdir="/work")

    flat = rec.calls[1]
    assert "--user" in flat
    idx = flat.index("--user")
    assert flat[idx + 1] == "vscode"
    # user 指定時は bash -lc 経由で argv を1引数に joined して渡す
    assert flat[-3:] == ["bash", "-lc", "claude -p"]


def test_exec_in_container_login_shell_quotes_unsafe_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = RunRecorder([(0, "true\n", ""), (0, "", "")])
    monkeypatch.setattr(subprocess, "run", rec)

    exec_in_container(
        devcontainer_cfg(user="vscode"),
        ["claude", "-p", "fix; rm -rf /", "--flag"],
        workdir="/work",
    )

    flat = rec.calls[1]
    # 危険文字を含む引数が shlex.join でクォートされ、シェル解釈で展開されないこと
    joined = flat[-1]
    assert "'fix; rm -rf /'" in joined
    assert flat[-3:-1] == ["bash", "-lc"]


def test_exec_in_container_omits_user_flag_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder([(0, "true\n", ""), (0, "", "")])
    monkeypatch.setattr(subprocess, "run", rec)

    exec_in_container(docker_cfg(), ["echo", "hi"], workdir="/work")

    flat = rec.calls[1]
    assert "--user" not in flat
    # user 未指定時は shell wrap なし。argv 末尾そのまま。
    assert flat[-2:] == ["echo", "hi"]
    assert "bash" not in flat
