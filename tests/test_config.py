"""knowl.config のテスト."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from knowl.config import (
    AppConfig,
    ConfigError,
    ContainerKind,
    RepoConfig,
    load_config,
)


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(dedent(body), encoding="utf-8")
    return path


def test_load_minimal_uses_defaults(tmp_path: Path) -> None:
    cfg_path = write(
        tmp_path,
        """
        repositories:
          - name: acme/widgets
            container:
              kind: docker
              name: widgets-dev
        """,
    )

    cfg = load_config(cfg_path)

    assert cfg.model == "claude-opus-4-7"
    assert cfg.cron_interval_minutes == 60
    assert cfg.thresholds.session_remaining_pct == 30
    assert cfg.thresholds.weekly_remaining_pct == 10
    assert cfg.slack.channel is None  # 環境変数優先
    assert cfg.templates.implementation == Path("templates/implementation.md")
    assert cfg.templates.investigation == Path("templates/investigation.md")
    assert len(cfg.repositories) == 1
    repo = cfg.repositories[0]
    assert repo.name == "acme/widgets"
    assert repo.container.kind == ContainerKind.DOCKER
    assert repo.container.name == "widgets-dev"
    assert repo.workdir == Path("/workspace")


def test_load_full(tmp_path: Path) -> None:
    cfg_path = write(
        tmp_path,
        """
        model: claude-sonnet-4-6
        cron_interval_minutes: 30
        thresholds:
          session_remaining_pct: 50
          weekly_remaining_pct: 20
        slack:
          channel: "#ops"
        templates:
          implementation: prompts/impl.md
          investigation: prompts/inv.md
        repositories:
          - name: acme/widgets
            container:
              kind: devcontainer
              name: widgets-dev
              workdir: /workspaces/widgets
          - name: acme/gizmos
            container:
              kind: docker
              name: gizmos
        """,
    )

    cfg = load_config(cfg_path)

    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.cron_interval_minutes == 30
    assert cfg.thresholds.session_remaining_pct == 50
    assert cfg.thresholds.weekly_remaining_pct == 20
    assert cfg.slack.channel == "#ops"
    assert cfg.templates.implementation == Path("prompts/impl.md")
    assert cfg.repositories[0].container.kind == ContainerKind.DEVCONTAINER
    assert cfg.repositories[0].workdir == Path("/workspaces/widgets")
    assert cfg.repositories[1].container.kind == ContainerKind.DOCKER


def test_repositories_required(tmp_path: Path) -> None:
    cfg_path = write(tmp_path, "model: claude-opus-4-7\n")
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_repository_name_must_be_owner_slash_repo(tmp_path: Path) -> None:
    cfg_path = write(
        tmp_path,
        """
        repositories:
          - name: just-a-name
            container:
              kind: docker
              name: c
        """,
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_unknown_container_kind_rejected(tmp_path: Path) -> None:
    cfg_path = write(
        tmp_path,
        """
        repositories:
          - name: a/b
            container:
              kind: podman
              name: c
        """,
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")


def test_repo_config_helpers() -> None:
    repo = RepoConfig.model_validate(
        {"name": "acme/widgets", "container": {"kind": "docker", "name": "c"}}
    )
    assert repo.owner == "acme"
    assert repo.repo == "widgets"


def test_cron_interval_minutes_invalid_non_multiple_rejected(tmp_path: Path) -> None:
    cfg_path = write(
        tmp_path,
        """
        cron_interval_minutes: 90
        repositories:
          - name: a/b
            container:
              kind: docker
              name: c
        """,
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_cron_interval_minutes_valid_sub_hour(tmp_path: Path) -> None:
    cfg_path = write(
        tmp_path,
        """
        cron_interval_minutes: 30
        repositories:
          - name: a/b
            container:
              kind: docker
              name: c
        """,
    )
    cfg = load_config(cfg_path)
    assert cfg.cron_interval_minutes == 30


def test_cron_interval_minutes_valid_multi_hour(tmp_path: Path) -> None:
    cfg_path = write(
        tmp_path,
        """
        cron_interval_minutes: 120
        repositories:
          - name: a/b
            container:
              kind: docker
              name: c
        """,
    )
    cfg = load_config(cfg_path)
    assert cfg.cron_interval_minutes == 120


def test_app_config_default_factory_invariants() -> None:
    """AppConfig を直接組み立てても妥当."""
    cfg = AppConfig(
        repositories=[
            RepoConfig.model_validate(
                {"name": "a/b", "container": {"kind": "docker", "name": "c"}}
            )
        ]
    )
    assert cfg.thresholds.session_remaining_pct == 30
    assert cfg.thresholds.weekly_remaining_pct == 10
