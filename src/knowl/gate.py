"""起動ゲート判定.

5h ローリングウィンドウと週次の両方の残量割合が、それぞれ閾値以上か検査する純粋関数。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from knowl.usage import UsageSnapshot


class GateDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str
    session_remaining_pct: float
    weekly_remaining_pct: float


def evaluate_gate(
    snapshot: UsageSnapshot,
    *,
    session_threshold: float,
    weekly_threshold: float,
) -> GateDecision:
    """残量が双方の閾値以上ならゲート通過。閾値ちょうども許可."""
    if snapshot.session_remaining_pct < session_threshold:
        reason = (
            f"session below threshold: "
            f"{snapshot.session_remaining_pct:.1f}% < {session_threshold:.1f}%"
        )
        allowed = False
    elif snapshot.weekly_remaining_pct < weekly_threshold:
        reason = (
            f"weekly below threshold: "
            f"{snapshot.weekly_remaining_pct:.1f}% < {weekly_threshold:.1f}%"
        )
        allowed = False
    else:
        reason = "ok"
        allowed = True
    return GateDecision(
        allowed=allowed,
        reason=reason,
        session_remaining_pct=snapshot.session_remaining_pct,
        weekly_remaining_pct=snapshot.weekly_remaining_pct,
    )
