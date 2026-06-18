"""設定ファイル読込.

YAML を pydantic で型安全に読み込む。閾値・モデル・cron 頻度・登録リポジトリと
対応 container を表現する最小スキーマ。
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConfigError(RuntimeError):
    """設定ロード失敗."""


class ContainerKind(StrEnum):
    DOCKER = "docker"
    DEVCONTAINER = "devcontainer"


class ContainerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ContainerKind
    name: str = Field(min_length=1)
    workdir: Path = Path("/workspace")
    # devcontainer の remoteUser (vscode / node 等) を docker exec --user に渡す。
    # 未指定なら root 実行。
    user: str | None = Field(default=None, min_length=1)


class RepoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    container: ContainerConfig
    workdir: Path | None = None

    @field_validator("name")
    @classmethod
    def _name_must_be_owner_slash_repo(cls, v: str) -> str:
        parts = v.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("repository name must be 'owner/repo'")
        return v

    @property
    def owner(self) -> str:
        return self.name.split("/", 1)[0]

    @property
    def repo(self) -> str:
        return self.name.split("/", 1)[1]

    def model_post_init(self, __context: object) -> None:
        # workdir 未指定なら container.workdir を採用
        if self.workdir is None:
            object.__setattr__(self, "workdir", self.container.workdir)


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_remaining_pct: Annotated[int, Field(ge=0, le=100)] = 30
    weekly_remaining_pct: Annotated[int, Field(ge=0, le=100)] = 10


class SlackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str | None = None


class TemplatesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    implementation: Path = Path("templates/implementation.md")
    investigation: Path = Path("templates/investigation.md")


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = "claude-opus-4-7"
    cron_interval_minutes: Annotated[int, Field(ge=1, le=10080)] = 60
    thresholds: Thresholds = Field(default_factory=Thresholds)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    templates: TemplatesConfig = Field(default_factory=TemplatesConfig)
    repositories: list[RepoConfig] = Field(min_length=1)

    @field_validator("cron_interval_minutes")
    @classmethod
    def _interval_must_be_cron_compatible(cls, v: int) -> int:
        # entrypoint.sh は 60 未満なら "*/N * * * *"、60 以上なら N が 60 の倍数の時のみ
        # "0 */H * * *" として cron 式を生成する。それ以外は無効 cron 式になるため拒否。
        if v < 60:
            return v
        if v % 60 != 0:
            raise ValueError(
                "cron_interval_minutes must be <60 or a multiple of 60 "
                f"(got {v}); otherwise the generated cron schedule is invalid"
            )
        return v


def load_config(path: Path | str) -> AppConfig:
    """YAML 設定ファイルを読み AppConfig を返す."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc
