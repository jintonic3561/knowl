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
