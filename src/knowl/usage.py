"""Claude Code 使用量取得.

Pro/Max subscription の OAuth トークンを使い、 ``/api/oauth/usage`` から
5h ローリングウィンドウと週次枠の残量を取得する。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
USER_AGENT = "claude-code/2.0.31"
ANTHROPIC_BETA = "oauth-2025-04-20"
DEFAULT_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


class UsageError(RuntimeError):
    """使用量取得失敗."""


class TokenExpiredError(UsageError):
    """OAuth トークンが期限切れまたは無効 (401)."""


@dataclass(frozen=True, slots=True)
class OAuthToken:
    access_token: str
    # ミリ秒 UNIX エポック (Claude Code の credentials.json 仕様)。None なら不明。
    expires_at_ms: int | None = None

    def is_expired(self, *, now_ms: int | None = None) -> bool:
        if self.expires_at_ms is None:
            return False
        current = now_ms if now_ms is not None else int(time.time() * 1000)
        return current >= self.expires_at_ms


class UsageSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_remaining_pct: float = Field(ge=0.0, le=100.0)
    weekly_remaining_pct: float = Field(ge=0.0, le=100.0)


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def _extract_remaining_pct(section: dict[str, Any]) -> float:
    """セクション(dict)から残量割合(0..100)を抽出する。

    サーバ実装の揺らぎを許容するため、複数キーをフォールバック評価する。
    """
    if "utilization" in section:
        return _clamp((1.0 - float(section["utilization"])) * 100.0)
    if "used_percent" in section:
        return _clamp(100.0 - float(section["used_percent"]))
    if "remaining_percent" in section:
        return _clamp(float(section["remaining_percent"]))
    if "remaining" in section and "limit" in section:
        limit = float(section["limit"])
        if limit <= 0:
            raise UsageError("limit must be positive")
        return _clamp(float(section["remaining"]) / limit * 100.0)
    raise UsageError(f"unable to extract remaining percentage from section: {section}")


def parse_usage_payload(payload: dict[str, Any]) -> UsageSnapshot:
    """usage API のレスポンス JSON から UsageSnapshot を組み立てる."""
    if "five_hour" not in payload or "seven_day" not in payload:
        raise UsageError(
            "usage payload must contain 'five_hour' and 'seven_day' sections"
        )
    session = _extract_remaining_pct(payload["five_hour"])
    weekly = _extract_remaining_pct(payload["seven_day"])
    return UsageSnapshot(session_remaining_pct=session, weekly_remaining_pct=weekly)


def load_oauth_token(path: Path | str = DEFAULT_CREDENTIALS_PATH) -> str:
    """``~/.claude/.credentials.json`` から OAuth アクセストークンを読む (後方互換)."""
    return load_oauth_credentials(path).access_token


def load_oauth_credentials(
    path: Path | str = DEFAULT_CREDENTIALS_PATH,
) -> OAuthToken:
    """credentials.json から access token と expiresAt(ms) を抽出する."""
    p = Path(path)
    if not p.is_file():
        raise UsageError(f"credentials file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UsageError(f"credentials file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise UsageError("credentials file root must be a JSON object")

    node = data.get("claudeAiOauth")
    if isinstance(node, dict) and isinstance(node.get("accessToken"), str):
        expires = node.get("expiresAt")
        expires_ms = int(expires) if isinstance(expires, int | float) else None
        return OAuthToken(access_token=str(node["accessToken"]), expires_at_ms=expires_ms)
    if isinstance(data.get("oauth_token"), str):
        return OAuthToken(access_token=str(data["oauth_token"]))
    raise UsageError("could not locate access token in credentials file")


def fetch_usage(
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 10.0,
) -> UsageSnapshot:
    """usage API を呼び、UsageSnapshot を返す."""
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        resp = http.get(
            USAGE_ENDPOINT,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": ANTHROPIC_BETA,
                "User-Agent": USER_AGENT,
            },
        )
    except httpx.HTTPError as exc:
        raise UsageError(f"usage API request failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()
    if resp.status_code == 401:
        raise TokenExpiredError(
            "usage API returned HTTP 401; OAuth token is invalid or expired. "
            "Re-run `claude` on the host to refresh ~/.claude/.credentials.json."
        )
    if resp.status_code != 200:
        raise UsageError(
            f"usage API returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    try:
        payload = resp.json()
    except json.JSONDecodeError as exc:
        raise UsageError(f"usage API returned non-JSON body: {exc}") from exc
    if not isinstance(payload, dict):
        raise UsageError("usage API payload must be a JSON object")
    return parse_usage_payload(payload)
