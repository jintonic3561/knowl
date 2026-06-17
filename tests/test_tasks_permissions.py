"""knowl.tasks の permission フラグ既定挙動の回帰テスト."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from knowl.config import AppConfig, RepoConfig, TemplatesConfig
from knowl.github_client import IssueRef
from knowl.prioritize import PriorityDecision, TaskKind
from knowl.tasks import DEFAULT_RUN_EXTRA_ARGS, run_task


def repo() -> RepoConfig:
    return RepoConfig.model_validate(
        {"name": "acme/widgets", "container": {"kind": "docker", "name": "widgets-dev"}}
    )


def issue() -> IssueRef:
    return IssueRef(
        repo="acme/widgets",
        number=1,
        title="t",
        body="b",
        labels=(),
        url="https://x",
        updated_at="t",
    )


def app_cfg(tmp_path: Path) -> AppConfig:
    impl = tmp_path / "i.md"
    inv = tmp_path / "v.md"
    impl.write_text("p", encoding="utf-8")
    inv.write_text("p", encoding="utf-8")
    return AppConfig(
        templates=TemplatesConfig(implementation=impl, investigation=inv),
        repositories=[repo()],
    )


def test_default_runner_passes_skip_permissions_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """default runner は --dangerously-skip-permissions を渡す."""
    cfg = app_cfg(tmp_path)
    decision = PriorityDecision(
        repo="acme/widgets", number=1, kind=TaskKind.IMPLEMENTATION, reason=""
    )

    captured: list[Sequence[str]] = []

    def fake_run(
        cmd: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured.append(list(cmd))
        if list(cmd[:2]) == ["docker", "inspect"]:
            return subprocess.CompletedProcess(
                args=list(cmd), returncode=0, stdout="true\n", stderr=""
            )
        # docker exec ... claude -p ...
        return subprocess.CompletedProcess(
            args=list(cmd),
            returncode=0,
            stdout=(
                '{"type":"result","is_error":false,"result":'
                '"{\\"action\\":\\"pr-opened\\",\\"summary\\":\\"x\\",'
                '\\"pr_url\\":\\"https://pr/1\\",\\"followups\\":[]}"}'
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    outcome = run_task(cfg, decision, issue())
    assert outcome.action == "pr-opened"

    # 2 回目の docker exec 呼び出しに permission フラグが含まれているか
    exec_call = captured[1]
    assert "claude" in exec_call
    for flag in DEFAULT_RUN_EXTRA_ARGS:
        assert flag in exec_call, f"missing permission flag: {flag} in {exec_call!r}"
