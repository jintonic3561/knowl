"""knowl CLI エントリポイント."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import click

from knowl.adhoc import AdhocResult, run_adhoc
from knowl.config import AppConfig, ConfigError, RepoConfig, load_config
from knowl.container import ensure_running
from knowl.cycle import CycleResult, run_cycle
from knowl.github_client import (
    GitHubError,
    IssueRef,
    create_issue,
    list_open_issues,
    resolve_gh_login,
)
from knowl.keepalive import DEFAULT_THRESHOLD_MS, DEFAULT_TIMEOUT_S, keepalive_once
from knowl.prioritize import NoActionableIssue, PriorityDecision, pick_priority
from knowl.slack import SlackNotifier
from knowl.slack_bot import run_bot_forever
from knowl.tasks import TaskOutcome
from knowl.tasks import run_task as run_task_impl
from knowl.usage import (
    DEFAULT_CREDENTIALS_PATH,
    UsageError,
    UsageSnapshot,
    fetch_usage,
    load_oauth_credentials,
)

_LOG = logging.getLogger("knowl")

DEFAULT_STATE_DIR = Path("/var/lib/knowl")
IDLE_STATE_FILENAME = "idle_state.json"


def _idle_state_path() -> Path:
    """前回 idle フラグを保存するパスを返す."""
    base = os.environ.get("KNOWL_STATE_DIR")
    return (Path(base) if base else DEFAULT_STATE_DIR) / IDLE_STATE_FILENAME


def _load_last_idle(path: Path) -> bool:
    """前回サイクルが idle だったかをロード。読み取れない場合は False."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("idle state load failed (treating as not-idle): %s", exc)
        return False
    return bool(data.get("last_idle", False)) if isinstance(data, dict) else False


def _save_last_idle(path: Path, value: bool) -> None:
    """idle フラグを保存。失敗してもサイクル全体は止めない."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_idle": value}), encoding="utf-8")
    except OSError as exc:
        _LOG.warning("idle state save failed: %s", exc)


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
    # ローカルの `expiresAt` 早切りは敢えてしない。
    # host keepalive と cron tick の境界で credentials.json の差し替えが間に合うか
    # 否かは race に依存しがちで、ローカルチェックは「expiresAt 過去だが API は
    # まだ refresh 直後で 200 を返す」「逆に expiresAt 未来だが既に revoke」
    # といった食い違いで誤判定する。判定は usage API の 401 に一本化する。
    creds = (
        load_oauth_credentials(token_path) if token_path else load_oauth_credentials()
    )
    return fetch_usage(creds.access_token)


def _list_issues(repos: Sequence[RepoConfig]) -> list[IssueRef]:
    return list_open_issues(repos)


def _prioritize(
    issues: list[IssueRef], *, model: str
) -> tuple[PriorityDecision, IssueRef] | NoActionableIssue:
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

    state_path = _idle_state_path()
    suppress_idle_notice = _load_last_idle(state_path)

    result: CycleResult = run_cycle(
        cfg,
        fetch_usage=lambda: _fetch_usage(credentials_path),
        list_issues=_list_issues,
        prioritize=_prioritize,
        run_task=_run_task,
        notify=notify,
        ensure_container=ensure_running,
        suppress_idle_notice=suppress_idle_notice,
    )

    # 状態更新:
    # - executed=True: 進捗あり → 次の idle は通知する (False)
    # - idle=True: 進捗なし → 連続なら次回抑止 (True)
    # - それ以外 (gate block / error / limit reached): 前回値を維持
    if result.executed:
        _save_last_idle(state_path, False)
    elif result.idle:
        _save_last_idle(state_path, True)

    if not result.executed:
        click.echo(f"no-op: {result.reason}")
        return
    click.echo(f"done: {result.reason}")


@main.command("bot")
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
def bot(config_path: Path, credentials_path: Path | None) -> None:
    """Slack slash command `/knowl` を Socket Mode で待受ける常駐モード."""
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        click.echo(f"config invalid: {exc}", err=True)
        sys.exit(2)

    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not bot_token or not app_token:
        click.echo(
            "SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set for the bot to start.",
            err=True,
        )
        sys.exit(2)

    try:
        login = resolve_gh_login()
    except GitHubError as exc:
        click.echo(f"gh login resolution failed: {exc}", err=True)
        sys.exit(2)

    notifier = _make_notifier(cfg)

    def notify(text: str) -> None:
        try:
            notifier.post(text)
        except Exception as exc:
            _LOG.warning("slack notification failed: %s", exc)
        click.echo(text)

    async def adhoc_runner(*, repo: str, task: str, user: str) -> AdhocResult:
        return await asyncio.to_thread(
            run_adhoc,
            cfg,
            repo_name=repo,
            task_description=task,
            user=user,
            fetch_usage=lambda: _fetch_usage(credentials_path),
            create_issue=create_issue,
            ensure_container=ensure_running,
            run_task=_run_task,
            notify=notify,
        )

    run_bot_forever(
        bot_token=bot_token,
        app_token=app_token,
        login=login,
        adhoc_runner=adhoc_runner,
    )


@main.command("keepalive")
@click.option(
    "--credentials",
    "credentials_path",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="~/.claude/.credentials.json の代替パス.",
)
@click.option(
    "--threshold-hours",
    default=DEFAULT_THRESHOLD_MS / 3_600_000,
    show_default=True,
    type=click.FloatRange(min=0.0),
    help="残り寿命がこの値未満なら refresh を走らせる (時間単位).",
)
@click.option(
    "--timeout-seconds",
    default=DEFAULT_TIMEOUT_S,
    show_default=True,
    type=click.FloatRange(min=1.0),
    help="claude 呼び出しのタイムアウト (秒).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="判定のみ行い claude を起動しない.",
)
def keepalive(
    credentials_path: Path | None,
    threshold_hours: float,
    timeout_seconds: float,
    dry_run: bool,
) -> None:
    """OAuth トークンを必要に応じて refresh する (cron 呼び出し想定)."""
    threshold_ms = int(threshold_hours * 3_600_000)
    try:
        result = keepalive_once(
            credentials_path=credentials_path or DEFAULT_CREDENTIALS_PATH,
            threshold_ms=threshold_ms,
            dry_run=dry_run,
            timeout_s=timeout_seconds,
        )
    except UsageError as exc:
        click.echo(f"keepalive failed: {exc}", err=True)
        sys.exit(1)
    click.echo(result.reason)


if __name__ == "__main__":  # pragma: no cover
    main()
