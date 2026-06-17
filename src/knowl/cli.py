"""knowl CLI エントリポイント."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import click

from knowl.config import AppConfig, ConfigError, RepoConfig, load_config
from knowl.cycle import CycleResult, run_cycle
from knowl.github_client import IssueRef, list_open_issues
from knowl.prioritize import PriorityDecision, pick_priority
from knowl.slack import SlackNotifier
from knowl.tasks import TaskOutcome
from knowl.tasks import run_task as run_task_impl
from knowl.usage import (
    TokenExpiredError,
    UsageSnapshot,
    fetch_usage,
    load_oauth_credentials,
)

_LOG = logging.getLogger("knowl")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@click.group()
@click.option("--verbose", is_flag=True, help="DEBUG ログを出す。")
def main(verbose: bool) -> None:
    """knowl — 自律 issue 駆動エージェント."""
    _setup_logging(verbose)


@main.command("check-config")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def check_config(config_path: Path) -> None:
    """設定ファイルの妥当性チェック."""
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        click.echo(f"config invalid: {exc}", err=True)
        sys.exit(2)
    click.echo(f"ok: model={cfg.model}, repositories={len(cfg.repositories)}")


def _fetch_usage(token_path: Path | None) -> UsageSnapshot:
    creds = (
        load_oauth_credentials(token_path) if token_path else load_oauth_credentials()
    )
    if creds.is_expired():
        raise TokenExpiredError(
            "OAuth token in credentials.json is past expiresAt; "
            "re-run `claude` on the host to refresh."
        )
    return fetch_usage(creds.access_token)


def _list_issues(repos: Sequence[RepoConfig]) -> list[IssueRef]:
    return list_open_issues(repos)


def _prioritize(
    issues: list[IssueRef], *, model: str
) -> tuple[PriorityDecision, IssueRef]:
    return pick_priority(issues, model=model)


def _run_task(
    cfg: AppConfig, decision: PriorityDecision, issue: IssueRef
) -> TaskOutcome:
    return run_task_impl(cfg, decision, issue)


def _make_notifier(cfg: AppConfig) -> SlackNotifier:
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL") or cfg.slack.channel
    return SlackNotifier(token=token, channel=channel)


@main.command("run-once")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--credentials",
    "credentials_path",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="~/.claude/.credentials.json の代替パス.",
)
def run_once(config_path: Path, credentials_path: Path | None) -> None:
    """1 サイクル実行する(cron からの呼び出し想定)."""
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        click.echo(f"config invalid: {exc}", err=True)
        sys.exit(2)

    notifier = _make_notifier(cfg)

    def notify(text: str) -> None:
        try:
            notifier.post(text)
        except Exception as exc:
            _LOG.warning("slack notification failed: %s", exc)
        click.echo(text)

    result: CycleResult = run_cycle(
        cfg,
        fetch_usage=lambda: _fetch_usage(credentials_path),
        list_issues=_list_issues,
        prioritize=_prioritize,
        run_task=_run_task,
        notify=notify,
    )

    if not result.executed:
        click.echo(f"no-op: {result.reason}")
        return
    click.echo(f"done: {result.reason}")


if __name__ == "__main__":  # pragma: no cover
    main()
