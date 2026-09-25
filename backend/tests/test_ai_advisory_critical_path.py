from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

from waterfallhunter import main
from waterfallhunter.core.signal_metadata import STRICT_STRATEGY_PROFILE


def test_trigger_persistence_does_not_wait_for_gemini_advisory(monkeypatch) -> None:
    symbol = "AIOUTSIDE/USDT:USDT"
    reference_observed_at = int(time.time()) - 1

    monkeypatch.setattr(
        main.scanner,
        "active_candidates",
        {symbol: {}},
    )
    monkeypatch.setattr(
        main.execution_decision_logger,
        "observe_evaluation",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        main.scanner,
        "get_live_reference",
        lambda requested_symbol: (
            0.01,
            reference_observed_at,
        ),
    )

    async def valid_trigger(*args, **kwargs):
        return {
            "is_valid": True,
            "score": 91.0,
            "suggested_status": "TRIGGERED",
            "metrics": {
                "exchange": "binance",
                "mapped_symbol": symbol,
                "orderbook": {
                    "bids": [[0.0099, 10.0]],
                    "asks": [[0.0101, 10.0]],
                },
                "ticker": {"last": 0.01},
                "strategy_profile": STRICT_STRATEGY_PROFILE,
                "score_version": "score_v2",
            },
        }

    monkeypatch.setattr(
        main.validator,
        "cross_check_symbol",
        valid_trigger,
    )
    monkeypatch.setattr(
        main,
        "build_signal_leverage_advisory",
        lambda metrics, execution_suitability=None, **kwargs: {
            "policy_version": "adaptive_signal_leverage_v1",
            "minimum": 4, "maximum": 18, "symbol_agnostic": True,
            "signal_only": True, "advisory_only": True,
            "status": "AVAILABLE", "leverage": 4, "reason": None,
        },
    )
    monkeypatch.setattr(
        main.execution_suitability_enricher,
        "for_symbol",
        lambda requested_symbol: {
            "symbol": requested_symbol,
            "status": "UNKNOWN",
            "observational_only": True,
            "trade_eligible": None,
        },
    )
    monkeypatch.setattr(
        main.production_evidence_recorder,
        "record",
        lambda *args, **kwargs: True,
    )
    direct_unsubscribed: list[tuple[str, str]] = []
    shared_unsubscribed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main.validator.ws_manager,
        "unsubscribe",
        lambda exchange, mapped: direct_unsubscribed.append((exchange, mapped)),
    )
    monkeypatch.setattr(
        main.validator.ws_manager,
        "unsubscribe_shared_evidence",
        lambda exchange, mapped: shared_unsubscribed.append((exchange, mapped)),
    )
    monkeypatch.setattr(
        main.notifier,
        "send_signal_alert",
        AsyncMock(),
    )
    # This test isolates non-blocking AI behavior; catalogue lifecycle CAS is
    # covered independently by EntryDecisionStore regression tests.
    monkeypatch.setattr(
        main.entry_decision_store,
        "latest_for_symbol",
        lambda _symbol: None,
    )
    monkeypatch.setattr(
        main.entry_decision_store,
        "append_if_changed",
        lambda *_args, **_kwargs: 9001,
    )
    monkeypatch.setattr(
        main.entry_decision_store,
        "append_advisory",
        lambda *_args, **_kwargs: 9002,
    )

    async def scenario() -> None:
        provider_started = asyncio.Event()
        release_provider = asyncio.Event()
        persisted = asyncio.Event()

        async def slow_typesafe(*args, **kwargs):
            provider_started.set()
            await release_provider.wait()
            return {"answers": {"setup_verified": {"noul": 0.5}, "confidence": {"score": 2}}}

        main.ai_veto._intel.api_key = "test"
        monkeypatch.setattr(main.ai_veto._intel, "_request_typesafe", slow_typesafe)

        def persist_trigger(*args, **kwargs):
            persisted.set()
            return 4242

        monkeypatch.setattr(
            main.signal_ledger,
            "persist_trigger",
            persist_trigger,
        )

        evaluation = asyncio.create_task(
            main.evaluate_candidate(
                symbol,
                {
                    "status": "ARMED",
                    "lifecycle_id": 1,
                    "scan_eligible": True,
                    "quote_volume": 3_000_000.0,
                    "last_price": 0.01,
                },
            )
        )

        await asyncio.wait_for(
            provider_started.wait(),
            timeout=1.0,
        )

        try:
            await asyncio.wait_for(
                asyncio.shield(persisted.wait()),
                timeout=0.2,
            )
        finally:
            release_provider.set()
            await evaluation

        assert persisted.is_set()

    asyncio.run(scenario())
    assert direct_unsubscribed == [("binance", symbol)]
    assert shared_unsubscribed == [("binance", symbol)]
