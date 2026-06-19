"""knowl.slack のテスト."""

from __future__ import annotations

import httpx
import pytest

from knowl.slack import (
    SlackError,
    SlackNotifier,
    build_cycle_start_notice,
    build_cycle_summary,
    build_idle_notice,
    build_limit_alert,
)


def test_build_cycle_start_notice_is_concise() -> None:
    text = build_cycle_start_notice(
        repo="acme/widgets",
        issue_number=42,
        issue_title="Fix login",
    )
    assert "acme/widgets" in text
    assert "#42" in text
    assert "Fix login" in text
    # 簡潔性: 1 行に収める
    assert "\n" not in text


def test_build_cycle_summary_minimal() -> None:
    text = build_cycle_summary(
        repo="acme/widgets",
        issue_number=12,
        issue_title="Fix login",
        issue_url="https://github.com/acme/widgets/issues/12",
        outcome="PR #34 opened for review",
        outcome_url="https://github.com/acme/widgets/pull/34",
        followups=["create issue: add tests"],
    )
    assert "acme/widgets" in text
    assert "#12" in text
    assert "Fix login" in text
    assert "PR #34" in text
    assert "create issue: add tests" in text
    # issue URL と outcome URL が文中に含まれる
    assert "https://github.com/acme/widgets/issues/12" in text
    assert "https://github.com/acme/widgets/pull/34" in text


def test_build_cycle_summary_without_followups() -> None:
    text = build_cycle_summary(
        repo="a/b",
        issue_number=1,
        issue_title="t",
        issue_url="https://github.com/a/b/issues/1",
        outcome="merged",
        outcome_url=None,
        followups=[],
    )
    assert "なし" in text
    # outcome_url 無しでも issue URL は出る
    assert "https://github.com/a/b/issues/1" in text


def test_build_limit_alert_mentions_limit() -> None:
    text = build_limit_alert("weekly limit reached")
    assert "limit" in text.lower()
    assert "weekly limit reached" in text


def test_build_idle_notice_carries_reason() -> None:
    text = build_idle_notice("open issue が見つからない")
    assert "open issue が見つからない" in text
    # 1 行に収める
    assert "\n" not in text
    # 他の通知と視覚的に区別できる prefix
    assert text[0] != "✅"
    assert text[0] != "▶"


def test_notifier_posts_when_token_present() -> None:
    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        captured["body"] = req.content
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier = SlackNotifier(token="tok", channel="#ops", client=client)
    notifier.post("hello")

    assert captured["url"] == "https://slack.com/api/chat.postMessage"
    assert captured["auth"] == "Bearer tok"
    assert b'"text": "hello"' in captured["body"]  # type: ignore[operator]


def test_notifier_raises_when_slack_returns_not_ok() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier = SlackNotifier(token="tok", channel="#x", client=client)
    with pytest.raises(SlackError):
        notifier.post("hi")


def test_notifier_without_token_skips() -> None:
    notifier = SlackNotifier(token=None, channel="#x")
    # トークン無しならエラーにせず黙って no-op
    notifier.post("hi")


def test_notifier_token_without_channel_rejected_at_construction() -> None:
    with pytest.raises(SlackError):
        SlackNotifier(token="tok", channel=None)
