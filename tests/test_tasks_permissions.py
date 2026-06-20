"""knowl.tasks の permission フラグ既定挙動の回帰テスト."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

import pytest

from knowl.config import AppConfig, RepoConfig
from knowl.github_client import IssueRef
from knowl.prioritize import PriorityDecision, TaskKind
from knowl.tasks import DEFAULT_RUN_EXTRA_ARGS, run_task


@pytest.fixture
def permissions_cfg(
    app_cfg: Callable[..., AppConfig],
    make_repo: Callable[..., RepoConfig],
) -> AppConfig:
    return app_cfg(repos=[make_repo(container_name="widgets-dev")])


def test_default_runner_passes_skip_permissions_flag(
    permissions_cfg: AppConfig,
    make_issue: Callable[..., IssueRef],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """default runner は --dangerously-skip-permissions を渡す."""
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

    outcome = run_task(
        permissions_cfg, decision, make_issue(url="https://x", updated_at="t")
    )
    assert outcome.action == "pr-opened"

    # 2 回目の docker exec 呼び出しに permission フラグが含まれているか
    exec_call = captured[1]
    assert "claude" in exec_call
    for flag in DEFAULT_RUN_EXTRA_ARGS:
        assert flag in exec_call, f"missing permission flag: {flag} in {exec_call!r}"
