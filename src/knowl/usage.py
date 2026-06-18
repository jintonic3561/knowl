"""Claude Code 使用量取得.

Pro/Max subscription の OAuth トークンを使い、 ``/api/oauth/usage`` から
5h ローリングウィンドウと週次枠の残量を取得する。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

_LOG = logging.getLogger(__name__)

USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
USER_AGENT = "claude-code/2.0.31"
ANTHROPIC_BETA = "oauth-2025-04-20"
DEFAULT_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"

# 一過性ネットワークエラー (Server disconnected / 短時間 5xx) を救う最低限のリトライ。
# 攻めすぎると per-cycle 失敗のフィードバックが遅れるので、控えめに 2 回 / 計 3 試行。
# 試行回数の単一源は ``len(retry_backoffs_s) + 1``。
DEFAULT_RETRY_BACKOFFS_S: tuple[float, ...] = (1.0, 4.0)


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
        return _clamp(100.0 - float(section["utilization"]))
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


def _sleep(seconds: float) -> None:
    """テストから差し替えやすいよう time.sleep を薄く包む."""
    time.sleep(seconds)


def fetch_usage(
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 10.0,
    retry_backoffs_s: tuple[float, ...] = DEFAULT_RETRY_BACKOFFS_S,
    sleep: Callable[[float], None] | None = None,
) -> UsageSnapshot:
    """usage API を呼び、UsageSnapshot を返す.

    ``Server disconnected`` のような一過性のネットワークエラーと 5xx は
    ``retry_backoffs_s`` の長さ分だけリトライする。401 などの永続エラーは
    即終了し再試行しない。
    """
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    sleep_fn = sleep if sleep is not None else _sleep
    attempts = len(retry_backoffs_s) + 1
    try:
        last_exc: BaseException | None = None
        for attempt in range(attempts):
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
                last_exc = exc
                if attempt < attempts - 1:
                    backoff = retry_backoffs_s[attempt]
                    _LOG.warning(
                        "usage API transport error (attempt %d/%d): %s; "
                        "retrying in %.1fs",
                        attempt + 1,
                        attempts,
                        exc,
                        backoff,
                    )
                    sleep_fn(backoff)
                    continue
                raise UsageError(f"usage API request failed: {exc}") from exc
            if resp.status_code == 401:
                raise TokenExpiredError(
                    "usage API returned HTTP 401; OAuth token is invalid or "
                    "expired. Re-run `claude` on the host to refresh "
                    "~/.claude/.credentials.json."
                )
            if 500 <= resp.status_code < 600 and attempt < attempts - 1:
                backoff = retry_backoffs_s[attempt]
                _LOG.warning(
                    "usage API HTTP %d (attempt %d/%d); retrying in %.1fs",
                    resp.status_code,
                    attempt + 1,
                    attempts,
                    backoff,
                )
                sleep_fn(backoff)
                continue
            if resp.status_code != 200:
                raise UsageError(
                    f"usage API returned HTTP {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
            try:
                payload = resp.json()
            except json.JSONDecodeError as exc:
                raise UsageError(
                    f"usage API returned non-JSON body: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise UsageError("usage API payload must be a JSON object")
            return parse_usage_payload(payload)
        # ループは return か raise で抜けるはずだが、保険として last_exc を投げる。
        raise UsageError(  # pragma: no cover - 防衛的フォールバック
            f"usage API request failed: {last_exc}"
        )
    finally:
        if owns_client:
            http.close()
