"""knowl.cli のテスト."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from knowl import cli


def write_config(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        """
repositories:
  - name: acme/widgets
    container:
      kind: docker
      name: widgets-dev
""",
        encoding="utf-8",
    )
    return cfg_path


def test_run_once_invokes_cycle_and_prints_summary(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg_path = write_config(tmp_path)
    runner = CliRunner()

    captured: dict[str, object] = {}

    def fake_run_cycle(
        cfg: object,
        **kwargs: object,
    ) -> object:
        captured["called"] = True
        captured["model"] = cfg.model  # type: ignore[attr-defined]
        # CycleResult スタブ。executed=False で reason 出力を確認
        from knowl.cycle import CycleResult

        return CycleResult(executed=False, reason="no open issues")

    monkeypatch.setattr(cli, "run_cycle", fake_run_cycle)  # type: ignore[attr-defined]

    result = runner.invoke(cli.main, ["run-once", "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert captured.get("called") is True
    assert "no open issues" in result.output


def test_run_once_persists_idle_state_and_suppresses_next(
    tmp_path: Path, monkeypatch: object
) -> None:
    """idle のサイクル後、次の idle サイクルは通知を抑止する."""
    cfg_path = write_config(tmp_path)
    state_dir = tmp_path / "state"
    monkeypatch.setenv("KNOWL_STATE_DIR", str(state_dir))  # type: ignore[attr-defined]
    runner = CliRunner()

    captured: list[bool] = []

    def fake_run_cycle(cfg: object, **kwargs: object) -> object:
        from knowl.cycle import CycleResult

        captured.append(bool(kwargs.get("suppress_idle_notice")))
        return CycleResult(executed=False, reason="no open issues", idle=True)

    monkeypatch.setattr(cli, "run_cycle", fake_run_cycle)  # type: ignore[attr-defined]

    # 1 回目: 前回の状態は無いので suppress=False
    result1 = runner.invoke(cli.main, ["run-once", "--config", str(cfg_path)])
    assert result1.exit_code == 0, result1.output
    assert captured == [False]

    # 2 回目: 前回 idle だったので suppress=True
    result2 = runner.invoke(cli.main, ["run-once", "--config", str(cfg_path)])
    assert result2.exit_code == 0, result2.output
    assert captured == [False, True]


def test_run_once_clears_idle_state_after_execution(
    tmp_path: Path, monkeypatch: object
) -> None:
    """executed=True のサイクル後は idle 状態をクリアし次回は通知する."""
    from knowl.cycle import CycleResult
    from knowl.prioritize import TaskKind
    from knowl.tasks import TaskOutcome

    cfg_path = write_config(tmp_path)
    state_dir = tmp_path / "state"
    monkeypatch.setenv("KNOWL_STATE_DIR", str(state_dir))  # type: ignore[attr-defined]
    runner = CliRunner()

    outcomes: list[CycleResult] = [
        CycleResult(executed=False, reason="no open issues", idle=True),
        CycleResult(
            executed=True,
            reason="ok",
            outcome=TaskOutcome(
                kind=TaskKind.IMPLEMENTATION,
                action="pr-opened",
                summary="",
                url=None,
                followups=[],
            ),
        ),
        CycleResult(executed=False, reason="no open issues", idle=True),
    ]
    suppress_args: list[bool] = []

    def fake_run_cycle(cfg: object, **kwargs: object) -> object:
        suppress_args.append(bool(kwargs.get("suppress_idle_notice")))
        return outcomes.pop(0)

    monkeypatch.setattr(cli, "run_cycle", fake_run_cycle)  # type: ignore[attr-defined]

    for _ in range(3):
        result = runner.invoke(cli.main, ["run-once", "--config", str(cfg_path)])
        assert result.exit_code == 0, result.output

    # 1: 初回, 2: 前回 idle なので suppress, 3: 前回 executed なので suppress 解除
    assert suppress_args == [False, True, False]


def test_run_once_keeps_idle_state_on_error(
    tmp_path: Path, monkeypatch: object
) -> None:
    """エラー / ゲートブロックでは idle 状態を維持する."""
    from knowl.cycle import CycleResult

    cfg_path = write_config(tmp_path)
    state_dir = tmp_path / "state"
    monkeypatch.setenv("KNOWL_STATE_DIR", str(state_dir))  # type: ignore[attr-defined]
    runner = CliRunner()

    outcomes: list[CycleResult] = [
        CycleResult(executed=False, reason="no open issues", idle=True),
        # エラー: idle=False (idle ではない)
        CycleResult(executed=False, reason="issue collection failed", idle=False),
        # 次の idle サイクルでも suppress=True であってほしい
        CycleResult(executed=False, reason="no open issues", idle=True),
    ]
    suppress_args: list[bool] = []

    def fake_run_cycle(cfg: object, **kwargs: object) -> object:
        suppress_args.append(bool(kwargs.get("suppress_idle_notice")))
        return outcomes.pop(0)

    monkeypatch.setattr(cli, "run_cycle", fake_run_cycle)  # type: ignore[attr-defined]

    for _ in range(3):
        result = runner.invoke(cli.main, ["run-once", "--config", str(cfg_path)])
        assert result.exit_code == 0, result.output

    # 1: 初回 (False), 2: idle 後 (True), 3: エラー中は状態維持なので True のまま
    assert suppress_args == [False, True, True]


def test_check_config_validates_file(tmp_path: Path) -> None:
    cfg_path = write_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["check-config", "--config", str(cfg_path)])
    assert result.exit_code == 0
    assert "ok" in result.output.lower()


def test_check_config_invalid(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("model: x\n", encoding="utf-8")  # repositories 欠落
    runner = CliRunner()
    result = runner.invoke(cli.main, ["check-config", "--config", str(bad)])
    assert result.exit_code != 0


def test_bot_requires_slack_tokens(
    tmp_path: Path, monkeypatch: object
) -> None:
    cfg_path = write_config(tmp_path)
    runner = CliRunner()
    # 両 token を空にすると early exit (Socket Mode 接続前) する
    monkeypatch.setenv("SLACK_BOT_TOKEN", "")  # type: ignore[attr-defined]
    monkeypatch.setenv("SLACK_APP_TOKEN", "")  # type: ignore[attr-defined]
    result = runner.invoke(cli.main, ["bot", "--config", str(cfg_path)])
    assert result.exit_code == 2
    assert "SLACK" in result.output
