from waterfallhunter.core.notifier import TelegramNotifier


def test_signal_message_uses_unavailable_marker_instead_of_none():
    message = TelegramNotifier.build_signal_message("PEPE/USDT:USDT", {"score": 88.5, "metrics": {}})
    assert "None" not in message
    assert "Score:</b> 88.50/100" in message
    assert "No live order is placed" in message


def test_signal_message_escapes_ai_reasoning_and_includes_real_context():
    message = TelegramNotifier.build_signal_message("PEPE/USDT:USDT", {
        "score": 91,
        "metrics": {
            "ai_advisory": {"ai_advice": "SHORT", "ai_confidence": 82, "ai_reasoning": "<check>"},
            "dex_context": {"chain_id": "ethereum", "liquidity_usd": 1000000},
            "onchain_context": {"large_transfer_sample_count": 2, "largest_transfer_usd": 200000},
        },
    })
    assert "&lt;check&gt;" in message
    assert "DEX: ethereum" in message
    assert "On-chain sample: 2" in message


def test_signal_message_reports_the_actual_advisory_provider():
    message = TelegramNotifier.build_signal_message("PEPE/USDT:USDT", {
        "metrics": {"ai_advisory": {"ai_provider": "typesafe"}},
    })
    assert "AI advisory (typesafe)" in message


# --- Change G tests: notification title tiers -----------------------------

from waterfallhunter.core.notifier import (
    TIER_BLOCKED,
    TIER_CONFIRMED,
    TIER_ENTRY_READY,
    TIER_WATCH_PRE_TRIGGER,
    TITLE_BLOCKED_BULLISH_STRUCTURE,
    TITLE_CONFIRMED,
    TITLE_ENTRY_READY,
    TITLE_WATCH_PRE_TRIGGER,
    select_notification_tier,
)


def _decision(**overrides) -> dict:
    base = {
        "readiness": 90.0,
        "lifecycle": "TRIGGERED",
        "bearish_15m_structure": True,
        "support_break_close": True,
        "absorption_detected": False,
        "retest_rejected": True,
        "valid_execution": True,
        "gates_passed": True,
    }
    base.update(overrides)
    return base


def test_readiness_just_below_confirmed_floor_is_watch_pre_trigger() -> None:
    for readiness in (70.7, 71.99):
        result = select_notification_tier(_decision(readiness=readiness))
        assert result["tier"] == TIER_WATCH_PRE_TRIGGER
        assert result["title"] == TITLE_WATCH_PRE_TRIGGER


def test_pre_trigger_lifecycle_is_always_watch_regardless_of_readiness() -> None:
    result = select_notification_tier(_decision(readiness=95.0, lifecycle="PRE-TRIGGER"))
    assert result["tier"] == TIER_WATCH_PRE_TRIGGER
    assert result["title"] == TITLE_WATCH_PRE_TRIGGER


def test_confirmed_band_is_not_entry_ready() -> None:
    for readiness in (72.0, 80.0, 84.99):
        result = select_notification_tier(_decision(readiness=readiness, retest_rejected=None))
        assert result["tier"] == TIER_CONFIRMED
        assert result["title"] == TITLE_CONFIRMED
        assert result["title"] != TITLE_ENTRY_READY


def test_entry_ready_requires_high_readiness_and_confirmed_retest() -> None:
    result = select_notification_tier(_decision(readiness=90.0, retest_rejected=True))
    assert result["tier"] == TIER_ENTRY_READY
    assert result["title"] == TITLE_ENTRY_READY


def test_readiness_72_and_85_never_render_the_same_title() -> None:
    low = select_notification_tier(_decision(readiness=72.0))
    high = select_notification_tier(_decision(readiness=90.0, retest_rejected=True))
    assert low["title"] != high["title"]
    assert low["tier"] != high["tier"]


def test_bullish_structure_forces_blocked_title() -> None:
    result = select_notification_tier(_decision(bearish_15m_structure=False))
    assert result["tier"] == TIER_BLOCKED
    assert result["title"] == TITLE_BLOCKED_BULLISH_STRUCTURE


def test_absorption_blocks_even_with_high_readiness() -> None:
    result = select_notification_tier(_decision(absorption_detected=True))
    assert result["tier"] == TIER_BLOCKED
    assert result["primary_blocker"] == "ABSORPTION_DETECTED"


def test_missing_structure_fields_fail_closed_to_watch() -> None:
    result = select_notification_tier(
        _decision(bearish_15m_structure=None, support_break_close=None)
    )
    assert result["tier"] == TIER_WATCH_PRE_TRIGGER


def test_entry_ready_message_uses_tiered_title_and_keeps_signal_only_wording() -> None:
    from waterfallhunter.core.notifier import TelegramNotifier

    payload = {
        "contract_version": "entry_ready_notification_v1",
        "symbol": "SXT/USDT:USDT",
        "decision_packet": {
            "entry_readiness": 90.0,
            "lifecycle_state": "TRIGGERED",
            "bearish_15m_structure": True,
            "support_break_close": True,
            "absorption_detected": False,
            "retest_rejected": True,
            "valid_execution": True,
            "gates_passed": True,
            "evidence_coverage_pct": 95.0,
            "reason_codes": [],
            "trade_plan": {
                "entry_price": 0.1,
                "stop_loss": 0.103,
                "take_profit_1": 0.097,
                "take_profit_2": 0.094,
                "take_profit_3": 0.091,
                "leverage": 3.0,
            },
        },
    }
    message = TelegramNotifier.build_entry_ready_message(payload)
    assert "ENTRY READY" in message
    assert "No live order is placed" in message


def test_entry_ready_message_downgrades_title_for_confirmed_band() -> None:
    from waterfallhunter.core.notifier import TelegramNotifier

    payload = {
        "contract_version": "entry_ready_notification_v1",
        "symbol": "SXT/USDT:USDT",
        "decision_packet": {
            "entry_readiness": 72.0,
            "lifecycle_state": "TRIGGERED",
            "bearish_15m_structure": True,
            "support_break_close": True,
            "absorption_detected": False,
            "retest_rejected": None,
            "valid_execution": True,
            "gates_passed": True,
            "evidence_coverage_pct": 80.0,
            "reason_codes": [],
            "trade_plan": {
                "entry_price": 0.1,
                "stop_loss": 0.103,
                "take_profit_1": 0.097,
                "take_profit_2": 0.094,
                "take_profit_3": 0.091,
                "leverage": 3.0,
            },
        },
    }
    message = TelegramNotifier.build_entry_ready_message(payload)
    assert "CONFIRMED" in message
    assert "ENTRY READY</b>" not in message
    assert "No live order is placed" in message
