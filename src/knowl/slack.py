"""Slack 通知.

R8 サマリ通知と Claude limit 到達アラートを 1 つの ``SlackNotifier`` で扱う。
``SLACK_BOT_TOKEN`` 未設定なら no-op (ログ警告のみ) として運用負荷を下げる。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from knowl.claude_runner import ClaudeError

SLACK_API = "https://slack.com/api/chat.postMessage"
_LOG = logging.getLogger(__name__)


class SlackError(RuntimeError):
    """Slack API 呼び出し失敗."""


class SlackNotifier:
    def __init__(
        self,
        *,
        token: str | None,
        channel: str | None,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        if token and not channel:
            raise SlackError(
                "Slack token is set but channel is missing; "
                "set SLACK_CHANNEL or slack.channel in config"
            )
        self._token = token
        self._channel = channel
        self._client = client
        self._timeout = timeout

    def post(self, text: str) -> None:
        if not self._token:
            _LOG.warning("SLACK_BOT_TOKEN not set; skipping notification")
            return
        if not self._channel:
            raise SlackError("Slack channel is not configured")
        payload = {"channel": self._channel, "text": text, "mrkdwn": True}
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            resp = client.post(
                SLACK_API,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                content=json.dumps(payload),
            )
        except httpx.HTTPError as exc:
            raise SlackError(f"Slack request failed: {exc}") from exc
        finally:
            if owns_client:
                client.close()
        if resp.status_code != 200:
            raise SlackError(f"Slack returned HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data: Any = resp.json()
        except json.JSONDecodeError as exc:
            raise SlackError(f"Slack returned non-JSON body: {exc}") from exc
        if not (isinstance(data, dict) and data.get("ok") is True):
            raise SlackError(f"Slack reported error: {data}")


def build_cycle_start_notice(
    *,
    repo: str,
    issue_number: int,
    issue_title: str,
) -> str:
    """着手 issue が決まり container が起動した直後に出す簡潔な開始通知."""
    return f"▶️ *{repo}#{issue_number}* — {issue_title} を開始"


def build_cycle_summary(
    *,
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_url: str,
    outcome: str,
    outcome_url: str | None,
    outcome_url_label: str = "PR",
    followups: Sequence[str],
) -> str:
    """R8 用の 1 サイクルサマリ文字列を生成する.

    issue URL は header の ``repo#番号`` を Slack mrkdwn のリンクにする形で埋め込む。
    タスクの成果物 URL (PR or コメント) は結果行と分離し、独立した URL 行として
    出力する。outcome 文字列全体をリンク化すると長文で読みづらく、URL も判別しにくいため。
    """
    header = f"<{issue_url}|{repo}#{issue_number}>"
    lines = [f"✅ *{header}* — {issue_title}"]
    if outcome_url:
        lines.append(f"• {outcome_url_label}: {outcome_url}")
    lines.append(f"• 結果: {outcome}")
    if followups:
        lines.append("• Follow-up:")
        for f in followups:
            lines.append(f"    - {f}")
    else:
        lines.append("• Follow-up: なし")
    return "\n".join(lines)


def build_limit_alert(reason: str) -> str:
    """Claude limit 到達時のアラート文字列."""
    return f"⚠️ Claude Code limit reached: {reason}"


def build_idle_notice(reason: str) -> str:
    """進めるべき issue がないときの簡潔な通知."""
    return f"💤 knowl: 進めるべき issue なし — {reason}"


def format_error_alert(prefix: str, exc: BaseException) -> str:
    """サイクル失敗時の Slack 通知文.

    ``prefix`` は ``"cycle failed during usage fetch"`` のように、
    どの実行系のどの段階で失敗したかを 1 行で読み取れる形で渡す。
    """
    return f"❌ knowl {prefix}: {exc}"


@dataclass(frozen=True, slots=True)
class ClaudeErrorAlert:
    """``ClaudeError`` を limit / generic に分類した結果.

    cycle / adhoc どちらの呼び出し側でも notice (Slack 通知文) と reason
    (``CycleResult.reason`` / ``AdhocResult.reason``) は同じテンプレでまとめたいので、
    両系列で共通の構造に詰めて返す。
    """

    notice: str
    reason: str
    limit_reached: bool


def classify_claude_error(
    exc: ClaudeError,
    *,
    notice_prefix: str,
    reason_label: str,
) -> ClaudeErrorAlert:
    """``ClaudeError`` を limit_reached / 通常エラーに分け、通知文と reason を生成する.

    cycle.py / adhoc.py で同じ分岐を 2 重に書いていたので片側だけ直すバグの温床になる
    のを防ぐためのヘルパ。 ``handle_step`` 風の完全テンプレ化はしない (return 型や
    付帯フィールドが site ごとに違うため)。

    - ``notice_prefix``: ``format_error_alert`` の prefix 引数 (例:
      ``"cycle failed during prioritization"``).
    - ``reason_label``: 通常エラー時の reason 文言の先頭ラベル (例: ``"prioritization"``
      → ``"prioritization claude error: ..."``)。
    """
    if exc.limit_reached:
        return ClaudeErrorAlert(
            notice=build_limit_alert(str(exc)),
            reason=f"claude limit reached: {exc}",
            limit_reached=True,
        )
    return ClaudeErrorAlert(
        notice=format_error_alert(notice_prefix, exc),
        reason=f"{reason_label} claude error: {exc}",
        limit_reached=False,
    )
