"""Pytest 共通設定.

テスト間で繰り返し使われる fixture を集約する。
小さなファクトリ fixture が中心で、各テストはキーワード引数で局所的に
カスタマイズして使う。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from knowl.config import AppConfig, RepoConfig, TemplatesConfig
from knowl.github_client import IssueRef
from knowl.prioritize import PriorityDecision, TaskKind
from knowl.usage import UsageSnapshot


@pytest.fixture
def make_repo() -> Callable[..., RepoConfig]:
    """RepoConfig のファクトリ. 既定は ``acme/widgets`` + docker container."""

    def _make(
        *,
        name: str = "acme/widgets",
        container_kind: str = "docker",
        container_name: str = "c",
    ) -> RepoConfig:
        return RepoConfig.model_validate(
            {
                "name": name,
                "container": {"kind": container_kind, "name": container_name},
            }
        )

    return _make


@pytest.fixture
def make_issue() -> Callable[..., IssueRef]:
    """IssueRef のファクトリ. 各テストは必要な属性だけ上書きする."""

    def _make(
        *,
        repo: str = "acme/widgets",
        number: int = 1,
        title: str = "t",
        body: str = "b",
        labels: tuple[str, ...] = (),
        url: str | None = None,
        updated_at: str = "2026-06-01T00:00:00Z",
        linked_pr_count: int = 0,
    ) -> IssueRef:
        return IssueRef(
            repo=repo,
            number=number,
            title=title,
            body=body,
            labels=labels,
            url=url if url is not None else f"https://github.com/{repo}/issues/{number}",
            updated_at=updated_at,
            linked_pr_count=linked_pr_count,
        )

    return _make


@pytest.fixture
def make_decision() -> Callable[..., PriorityDecision]:
    """PriorityDecision のファクトリ."""

    def _make(
        *,
        repo: str = "acme/widgets",
        number: int = 1,
        kind: TaskKind = TaskKind.IMPLEMENTATION,
        reason: str = "",
    ) -> PriorityDecision:
        return PriorityDecision(repo=repo, number=number, kind=kind, reason=reason)

    return _make


@pytest.fixture
def app_cfg(
    tmp_path: Path, make_repo: Callable[..., RepoConfig]
) -> Callable[..., AppConfig]:
    """AppConfig のファクトリ (templates を tmp_path に書き出し済み)."""

    def _make(
        *,
        impl_template: str = "p",
        inv_template: str = "p",
        repos: list[RepoConfig] | None = None,
    ) -> AppConfig:
        impl = tmp_path / "impl.md"
        inv = tmp_path / "inv.md"
        impl.write_text(impl_template, encoding="utf-8")
        inv.write_text(inv_template, encoding="utf-8")
        return AppConfig(
            templates=TemplatesConfig(implementation=impl, investigation=inv),
            repositories=repos if repos is not None else [make_repo()],
        )

    return _make


@pytest.fixture
def ok_snapshot() -> UsageSnapshot:
    """ゲートを通る使用量スナップショット (session/weekly 80%残)."""
    return UsageSnapshot(session_remaining_pct=80, weekly_remaining_pct=80)
