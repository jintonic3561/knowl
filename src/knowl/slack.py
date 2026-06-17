"""Slack 通知.

R8 サマリ通知と Claude limit 到達アラートを 1 つの ``SlackNotifier`` で扱う。
``SLACK_BOT_TOKEN`` 未設定なら no-op (ログ警告のみ) として運用負荷を下げる。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

import httpx

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


def build_cycle_summary(
    *,
    repo: str,
    issue_number: int,
    issue_title: str,
    outcome: str,
    followups: Sequence[str],
) -> str:
    """R8 用の 1 サイクルサマリ文字列を生成する."""
    lines = [
        f"✅ *{repo}#{issue_number}* — {issue_title}",
        f"• 結果: {outcome}",
    ]
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
