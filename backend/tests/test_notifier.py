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
