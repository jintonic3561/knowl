"""knowl.usage のテスト."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from knowl.usage import (
    USAGE_ENDPOINT,
    OAuthToken,
    TokenExpiredError,
    UsageError,
    UsageSnapshot,
    fetch_usage,
    load_oauth_credentials,
    load_oauth_token,
    parse_usage_payload,
)


def test_parse_usage_payload_basic() -> None:
    # 実 API は utilization を percent (0..100) で返す
    payload = {
        "five_hour": {"utilization": 40.0},
        "seven_day": {"utilization": 10.0},
    }
    snap = parse_usage_payload(payload)
    assert isinstance(snap, UsageSnapshot)
    assert snap.session_remaining_pct == pytest.approx(60.0)
    assert snap.weekly_remaining_pct == pytest.approx(90.0)


def test_parse_usage_payload_alternative_shape() -> None:
    """別形状(used_percent)のキーにも頑健."""
    payload = {
        "five_hour": {"used_percent": 25},
        "seven_day": {"used_percent": 80},
    }
    snap = parse_usage_payload(payload)
    assert snap.session_remaining_pct == pytest.approx(75.0)
    assert snap.weekly_remaining_pct == pytest.approx(20.0)


def test_parse_usage_payload_missing_section_raises() -> None:
    with pytest.raises(UsageError):
        parse_usage_payload({"five_hour": {"utilization": 10.0}})


def test_parse_usage_payload_clamps_to_0_100() -> None:
    payload = {
        "five_hour": {"utilization": 150.0},  # 150% → remaining -50 → 0
        "seven_day": {"utilization": -20.0},  # マイナス → remaining 120 → 100
    }
    snap = parse_usage_payload(payload)
    assert snap.session_remaining_pct == 0.0
    assert snap.weekly_remaining_pct == 100.0


def test_load_oauth_token_reads_credentials_json(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    creds.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok_xyz"}}),
        encoding="utf-8",
    )
    assert load_oauth_token(creds) == "tok_xyz"


def test_load_oauth_token_alternative_layout(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    creds.write_text(json.dumps({"oauth_token": "tok2"}), encoding="utf-8")
    assert load_oauth_token(creds) == "tok2"


def test_load_oauth_token_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(UsageError):
        load_oauth_token(tmp_path / "absent.json")


def test_load_oauth_token_invalid_layout(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    creds.write_text("{}", encoding="utf-8")
    with pytest.raises(UsageError):
        load_oauth_token(creds)


def test_fetch_usage_calls_endpoint_with_auth_header() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["beta"] = request.headers.get("anthropic-beta")
        return httpx.Response(
            200,
            json={
                "five_hour": {"utilization": 50.0},
                "seven_day": {"utilization": 20.0},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    snap = fetch_usage("tok", client=client)

    assert captured["url"] == USAGE_ENDPOINT
    assert captured["auth"] == "Bearer tok"
    assert captured["beta"] == "oauth-2025-04-20"
    assert snap.session_remaining_pct == pytest.approx(50.0)


def test_fetch_usage_non_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="nope")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(UsageError):
        fetch_usage("tok", client=client)


def test_fetch_usage_401_raises_token_expired() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid_grant")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(TokenExpiredError):
        fetch_usage("tok", client=client)


def test_load_oauth_credentials_extracts_expires_at(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    creds.write_text(
        json.dumps(
            {"claudeAiOauth": {"accessToken": "tok", "expiresAt": 1_900_000_000_000}}
        ),
        encoding="utf-8",
    )
    token = load_oauth_credentials(creds)
    assert token == OAuthToken(access_token="tok", expires_at_ms=1_900_000_000_000)


def test_oauth_token_is_expired() -> None:
    expired = OAuthToken(access_token="x", expires_at_ms=1_000)
    fresh = OAuthToken(access_token="x", expires_at_ms=10**13)
    unknown = OAuthToken(access_token="x", expires_at_ms=None)
    assert expired.is_expired(now_ms=2_000) is True
    assert fresh.is_expired(now_ms=2_000) is False
    # 期限不明は False に倒し、通常パスを邪魔しない
    assert unknown.is_expired(now_ms=2_000) is False
