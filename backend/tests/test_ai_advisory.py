import asyncio

from schema_test_support import migrate_test_database
from waterfallhunter import main
from waterfallhunter.core.ai_veto import AIVetoEngine
from waterfallhunter.core.entry_decision_store import EntryDecisionStore


def canonical_metrics() -> dict:
    return {
        "derivatives": {
            "funding_rate": 0.0002,
            "oi_change_1h_pct": 0.8,
            "taker_buy_sell_ratio": 0.72,
            "top_trader_long_short_ratio": 2.1,
        },
        "microstructure": {
            "sell_flow_usdt": 180000.0,
            "buy_flow_usdt": 70000.0,
            "spread_pct": 0.04,
            "slippage_pct": 0.06,
        },
        "cascade_intelligence": {
            "status": "PASS",
            "readiness_points": 8.4,
        },
        "breakdown_confirmation": {"confirmation_exchange_15m": True},
    }


def decision_packet() -> dict:
    return {"decision": "ENTRY_READY", "entry_readiness": 84.0}


def test_typesafe_state_contains_canonical_decision_evidence() -> None:
    engine = AIVetoEngine()
    state = engine._intel._build_state({**canonical_metrics(), "symbol": "SXTUSDT"}, decision_packet())
    assert state["symbol"] == "SXTUSDT"
    assert state["decision"] == "ENTRY_READY"
    assert state["cascade"]["status"] == "PASS"
    assert state["cascade"]["readiness_points"] == 8.4


def test_canonical_advisory_failure_cannot_change_decision(monkeypatch) -> None:
    engine = AIVetoEngine()

    async def unavailable(*args, **kwargs):
        return None

    engine._intel.api_key = "test"
    monkeypatch.setattr(engine._intel, "_request_typesafe", unavailable)
    packet = decision_packet()
    advisory = asyncio.run(engine.advisory_for_decision("SXTUSDT", canonical_metrics(), packet))
    assert packet["decision"] == "ENTRY_READY"
    assert advisory["ai_advice"] == "UNAVAILABLE"
    assert advisory["ai_provider"] == "none"


def test_canonical_advisory_is_persisted_before_live_projection(tmp_path, monkeypatch) -> None:
    db_path = migrate_test_database(tmp_path / "advisory.db")
    store = EntryDecisionStore(db_path)
    decision = {
        "contract_version": "entry_decision_v1",
        "policy_version": "entry_policy_v1",
        "evaluated_at": 100,
        "decision": "ENTRY_READY",
        "lifecycle_state": "ARMED",
        "entry_readiness": 84.0,
        "evidence_coverage_pct": 82.0,
    }
    event_id = store.append_if_changed("SXTUSDT", decision)
    assert event_id is not None
    live_decision = {**decision, "event_id": event_id}
    monkeypatch.setattr(main, "entry_decision_store", store)
    monkeypatch.setattr(
        main.scanner,
        "active_candidates",
        {"SXTUSDT": {"metrics": {"entry_decision": live_decision}}},
    )

    async def advisory(*args, **kwargs):
        return {
            "observational_only": True,
            "decision_mutated": False,
            "ai_advice": "SHORT",
            "ai_confidence": 81,
            "ai_reasoning": "Evidence agrees.",
            "ai_provider": "gemini",
            "ai_model": "gemini-test",
            "ai_status": "AVAILABLE",
        }

    monkeypatch.setattr(main.ai_veto, "advisory_for_decision", advisory)
    asyncio.run(main._refresh_canonical_ai_advisory("SXTUSDT", event_id, {}, decision))

    persisted = EntryDecisionStore(db_path).history_for_symbol("SXTUSDT")[0]
    projected = main.scanner.active_candidates["SXTUSDT"]["metrics"]["ai_advisory"]
    assert persisted["ai_advisory"]["ai_advice"] == "SHORT"
    assert projected["ai_advice"] == "SHORT"
    assert projected["advisory_event_id"] > 0


def test_stable_decision_projection_carries_durable_advisory(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "stable-advisory.db")
    store = EntryDecisionStore(db_path)
    decision = {
        "contract_version": "entry_decision_v1",
        "policy_version": "entry_policy_v1",
        "evaluated_at": 100,
        "decision": "ENTRY_READY",
        "lifecycle_state": "ARMED",
        "entry_readiness": 84.0,
        "evidence_coverage_pct": 82.0,
        "trade_plan": None,
    }
    event_id = store.append_if_changed("SXTUSDT", decision)
    assert event_id is not None
    store.append_advisory(
        event_id,
        {
            "observational_only": True,
            "decision_mutated": False,
            "ai_advice": "SHORT",
            "ai_confidence": 81,
            "ai_reasoning": "Evidence agrees.",
            "ai_provider": "gemini",
            "ai_model": "gemini-test",
            "ai_status": "AVAILABLE",
        },
        advisory_at=110,
    )
    persisted = store.latest_for_symbol("SXTUSDT")
    current = {**decision, "evaluated_at": 120}
    metrics = {"ai_advisory": {"deterministic_veto": False}}

    main._restore_persisted_decision_projection(current, metrics, persisted)

    assert current["event_id"] == event_id
    assert current["event_persisted"] is False
    assert metrics["ai_advisory"]["ai_advice"] == "SHORT"


def test_runtime_expiry_reconciliation_persists_explicit_expiry(tmp_path, monkeypatch) -> None:
    db_path = migrate_test_database(tmp_path / "expiry.db")
    store = EntryDecisionStore(db_path)
    decision = {
        "contract_version": "entry_decision_v1",
        "policy_version": "entry_policy_v1",
        "evaluated_at": 100,
        "decision": "ENTRY_READY",
        "lifecycle_state": "ARMED",
        "entry_readiness": 84.0,
        "evidence_coverage_pct": 82.0,
        "hard_blocked": False,
        "block_reasons": [],
        "reason_codes": ["ENTRY_GATES_PASS"],
        "components": {},
        "evidence_summary": {},
        "trade_plan": {
            "entry_price": 0.1,
            "stop_loss": 0.103,
            "take_profit_1": 0.097,
            "take_profit_2": 0.094,
            "expires_at": 150,
        },
        "policy": {},
    }
    event_id = store.append_if_changed("SXTUSDT", decision)
    assert event_id is not None
    monkeypatch.setattr(main, "entry_decision_store", store)
    monkeypatch.setattr(
        main.scanner,
        "active_candidates",
        {"SXTUSDT": {"metrics": {"entry_decision": {**decision, "event_id": event_id}}}},
    )

    assert main._reconcile_explicit_entry_expirations(evaluated_at=149) == 0
    assert main._reconcile_explicit_entry_expirations(evaluated_at=150) == 1

    latest = store.latest_for_symbol("SXTUSDT")
    assert latest is not None
    assert latest["decision"] == "EXPIRED"
    assert latest["trade_plan"]["expires_at"] == 150
    assert main.scanner.active_candidates["SXTUSDT"]["metrics"]["entry_decision"]["decision"] == "EXPIRED"


def test_runtime_reconciles_actionable_decision_when_symbol_leaves_active_candidates(tmp_path, monkeypatch) -> None:
    db_path = migrate_test_database(tmp_path / "inactive-candidate.db")
    store = EntryDecisionStore(db_path)
    decision = {
        "contract_version": "entry_decision_v1",
        "policy_version": "entry_policy_v1",
        "evaluated_at": 100,
        "decision": "ENTRY_READY",
        "lifecycle_state": "ARMED",
        "entry_readiness": 84.0,
        "evidence_coverage_pct": 82.0,
        "hard_blocked": False,
        "block_reasons": [],
        "reason_codes": ["ENTRY_GATES_PASS"],
        "components": {},
        "evidence_summary": {},
        "trade_plan": {
            "entry_price": 0.1,
            "stop_loss": 0.103,
            "take_profit_1": 0.097,
            "take_profit_2": 0.094,
        },
        "policy": {},
    }
    event_id = store.append_if_changed("GONEUSDT", decision)
    assert event_id is not None
    monkeypatch.setattr(main, "entry_decision_store", store)

    assert main._reconcile_inactive_actionable_decisions(
        active_symbols={"OTHERUSDT"},
        evaluated_at=150,
    ) == 1
    latest = store.latest_for_symbol("GONEUSDT")
    assert latest is not None
    assert latest["decision"] == "INVALIDATED"
    assert latest["block_reasons"] == ["CANDIDATE_NO_LONGER_ACTIVE"]

    assert main._reconcile_inactive_actionable_decisions(
        active_symbols={"OTHERUSDT"},
        evaluated_at=151,
    ) == 0


def test_runtime_does_not_invalidate_actionable_symbol_still_active(tmp_path, monkeypatch) -> None:
    db_path = migrate_test_database(tmp_path / "active-candidate.db")
    store = EntryDecisionStore(db_path)
    decision = {
        "contract_version": "entry_decision_v1",
        "policy_version": "entry_policy_v1",
        "evaluated_at": 100,
        "decision": "ENTRY_READY",
        "lifecycle_state": "ARMED",
        "entry_readiness": 84.0,
        "evidence_coverage_pct": 82.0,
        "hard_blocked": False,
        "block_reasons": [],
        "reason_codes": ["ENTRY_GATES_PASS"],
        "components": {},
        "evidence_summary": {},
        "trade_plan": None,
        "policy": {},
    }
    event_id = store.append_if_changed("STAYUSDT", decision)
    assert event_id is not None
    monkeypatch.setattr(main, "entry_decision_store", store)

    assert main._reconcile_inactive_actionable_decisions(
        active_symbols={"STAYUSDT"},
        evaluated_at=150,
    ) == 0
    assert store.latest_for_symbol("STAYUSDT")["decision"] == "ENTRY_READY"


def test_stable_entry_ready_projection_keeps_persisted_trade_plan_levels() -> None:
    persisted = {
        "decision": "ENTRY_READY",
        "event_id": 7,
        "trade_plan": {
            "entry_price": 1.0,
            "stop_loss": 1.05,
            "take_profit_1": 0.95,
            "take_profit_2": 0.90,
        },
    }
    current = {
        "decision": "ENTRY_READY",
        "evaluated_at": 200,
        "trade_plan": {
            "entry_price": 1.01,
            "stop_loss": 1.06,
            "take_profit_1": 0.96,
            "take_profit_2": 0.91,
        },
    }
    metrics: dict = {}

    main._restore_persisted_decision_projection(current, metrics, persisted)

    assert current["event_id"] == 7
    assert current["trade_plan"] == persisted["trade_plan"]


def test_canonical_advisory_rejects_invalid_gemini_enum_and_confidence(monkeypatch) -> None:
    engine = AIVetoEngine()

    async def malformed(*args, **kwargs):
        return {"answers": {"setup_verified": {"noul": "invalid"}, "confidence": {"score": 140}}}

    engine._intel.api_key = "test"
    monkeypatch.setattr(engine._intel, "_request_typesafe", malformed)
    advisory = asyncio.run(
        engine.advisory_for_decision("SXTUSDT", canonical_metrics(), decision_packet())
    )

    assert advisory["ai_status"] == "UNAVAILABLE"
    assert advisory["ai_provider"] == "none"
    assert advisory["ai_advice"] == "UNAVAILABLE"
    assert advisory["ai_confidence"] == 0


def test_stable_projection_restores_persisted_canonical_leverage_consistently() -> None:
    persisted = {
        "decision": "ENTRY_READY",
        "event_id": 9,
        "trade_plan": {"entry_price": 1.0, "stop_loss": 1.05, "take_profit_1": 0.95, "take_profit_2": 0.90, "leverage": 8},
        "leverage_advisory": {
            "status": "AVAILABLE", "leverage": 8,
            "policy_version": "adaptive_signal_leverage_v1", "reason": None,
            "execution_suitability_input": {"status": "SUITABLE", "observed_samples": 37},
        },
    }
    current = {
        "decision": "ENTRY_READY",
        "trade_plan": {"entry_price": 1.01, "stop_loss": 1.06, "take_profit_1": 0.96, "take_profit_2": 0.91, "leverage": 10},
        "leverage_advisory": {"status": "AVAILABLE", "leverage": 10, "policy_version": "adaptive_signal_leverage_v1"},
    }
    metrics = {"leverage_advisory": dict(current["leverage_advisory"]), "applied_leverage": 10}
    main._restore_persisted_decision_projection(current, metrics, persisted)
    assert current["trade_plan"]["leverage"] == 8
    assert current["leverage_advisory"]["leverage"] == 8
    assert metrics["leverage_advisory"]["leverage"] == 8
    assert metrics["applied_leverage"] == 8
