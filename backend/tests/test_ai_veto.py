import asyncio
import time

import pytest

from waterfallhunter import main
from waterfallhunter.core.ai_veto import AICascadeOpinion, AIVetoEngine


def test_ai_veto_engine_has_no_local_model_fallback():
    engine = AIVetoEngine()

    assert not hasattr(engine, "ollama_url")
    assert not hasattr(engine, "ollama_model")
    assert not hasattr(engine, "_get_ollama_opinion")
    assert not hasattr(engine._intel, "ollama_url")
    assert not hasattr(engine._intel, "ollama_model")


def test_deterministic_veto_does_not_invoke_the_advisory_provider(monkeypatch):
    engine = AIVetoEngine()
    invoked = False

    async def advisory(*args, **kwargs):
        nonlocal invoked
        invoked = True
        return AICascadeOpinion(
            verified=False,
            note="provider must not be part of deterministic evaluation",
            score=99,
            provider="typesafe",
            model="jev-latest",
            raw={},
        )

    monkeypatch.setattr(engine._intel, "get_advisory", advisory)

    vetoed, advisory_data = engine.evaluate_deterministic(
        "TESTUSDT",
        {"bids": [[1.0, 1.0]], "asks": [[1.1, 1.0]]},
        {"last": 1.0},
    )

    assert vetoed is False
    assert invoked is False
    assert advisory_data["deterministic_veto"] is False
    assert advisory_data["ai_observational_only"] is True
    assert advisory_data["ai_decision_critical"] is False


def test_evaluate_symbol_preserves_the_advisory_provider(monkeypatch):
    engine = AIVetoEngine()

    async def advisory(*args, **kwargs):
        return AICascadeOpinion(
            verified=True,
            note="live data is mixed",
            score=42,
            provider="typesafe",
            model="jev-1.13.0",
            raw={},
        )

    monkeypatch.setattr(engine._intel, "get_advisory", advisory)
    vetoed, advisory_data = asyncio.run(
        engine.evaluate_symbol(
            "TESTUSDT",
            {"bids": [[1.0, 1.0]], "asks": [[1.1, 1.0]]},
            {"last": 1.0},
        )
    )

    assert vetoed is False
    assert advisory_data["ai_provider"] == "typesafe"
    assert advisory_data["ai_advice"] == "NEUTRAL"
    assert advisory_data["ai_model"] == "jev-1.13.0"
    assert advisory_data["ai_observational_only"] is True
    assert advisory_data["ai_decision_critical"] is False


def test_deterministic_entry_gate_runs_for_armed_candidate(monkeypatch):
    metrics = {"orderbook": {"bids": [[1.0, 4.0]], "asks": [[1.1, 1.0]]}, "ticker": {"last": 1.0}}

    state, vetoed = main._apply_deterministic_entry_gate("TESTUSDT", "ARMED", metrics)

    assert state == "ARMED"
    assert vetoed is True
    assert metrics["ai_advisory"]["deterministic_veto"] is True


def test_runtime_applies_deterministic_gate_before_canonical_decision(monkeypatch) -> None:
    symbol = "ORDER/USDT:USDT"
    calls: list[str] = []
    monkeypatch.setattr(main.scanner, "active_candidates", {symbol: {}})
    monkeypatch.setattr(main.scanner, "get_live_reference", lambda _symbol: (0.01, time.time()))
    monkeypatch.setattr(main.execution_decision_logger, "observe_evaluation", lambda *args, **kwargs: None)

    async def cross_check_symbol(*args, **kwargs):
        return {
            "is_valid": True, "score": 80.0, "suggested_status": "ARMED",
            "metrics": {"exchange": "binance", "mapped_symbol": symbol},
        }

    monkeypatch.setattr(main.validator, "cross_check_symbol", cross_check_symbol)
    monkeypatch.setattr(main, "build_signal_leverage_advisory", lambda metrics, execution_suitability=None, **kwargs: {
        "policy_version": "adaptive_signal_leverage_v1",
        "minimum": 4, "maximum": 18, "symbol_agnostic": True,
        "signal_only": True, "advisory_only": True,
        "status": "UNAVAILABLE", "leverage": None, "reason": "test unavailable",
    })
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _symbol: None)

    def gate(_symbol, state, _metrics):
        calls.append("gate")
        return state, False

    def build(*args, **kwargs):
        calls.append("decision")
        return {"decision": "FORMING"}

    def stop_append(*args, **kwargs):
        raise RuntimeError("stop-after-canonical-append")

    monkeypatch.setattr(main, "_apply_deterministic_entry_gate", gate)
    monkeypatch.setattr(main, "build_entry_decision", build)
    monkeypatch.setattr(main.entry_decision_store, "append_if_changed", stop_append)
    with pytest.raises(RuntimeError, match="stop-after-canonical-append"):
        asyncio.run(main.evaluate_candidate(symbol, {
            "status": "ARMED", "lifecycle_id": 7, "scan_eligible": True,
            "quote_volume": 3_000_000.0, "last_price": 0.01,
        }))
    assert calls == ["gate", "decision"]
