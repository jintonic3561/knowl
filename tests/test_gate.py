"""knowl.gate のテスト."""

from __future__ import annotations

from knowl.gate import GateDecision, evaluate_gate
from knowl.usage import UsageSnapshot


def snap(session_pct: float, weekly_pct: float) -> UsageSnapshot:
    return UsageSnapshot(
        session_remaining_pct=session_pct,
        weekly_remaining_pct=weekly_pct,
    )


def test_gate_allows_when_both_exceed_thresholds() -> None:
    decision = evaluate_gate(snap(50, 20), session_threshold=30, weekly_threshold=10)
    assert decision.allowed is True
    assert decision.reason == "ok"


def test_gate_blocks_when_session_low() -> None:
    decision = evaluate_gate(snap(20, 50), session_threshold=30, weekly_threshold=10)
    assert decision.allowed is False
    assert "session" in decision.reason


def test_gate_blocks_when_weekly_low() -> None:
    decision = evaluate_gate(snap(80, 5), session_threshold=30, weekly_threshold=10)
    assert decision.allowed is False
    assert "weekly" in decision.reason


def test_gate_equality_at_threshold_is_allowed() -> None:
    decision = evaluate_gate(snap(30, 10), session_threshold=30, weekly_threshold=10)
    assert decision.allowed is True


def test_gate_decision_is_serializable() -> None:
    d = GateDecision(allowed=True, reason="ok", session_remaining_pct=42, weekly_remaining_pct=18)
    payload = d.model_dump()
    assert payload["allowed"] is True
    assert payload["session_remaining_pct"] == 42
