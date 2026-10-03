from waterfallhunter.core.cascade_intelligence import build_cascade_evidence


def base_metrics() -> dict:
    return {
        "microstructure": {
            "approved": True,
            "spoofing_detected": False,
            "sell_flow_usdt": 180_000.0,
            "buy_flow_usdt": 60_000.0,
            "bid_depth_usdt": 140_000.0,
            "ask_depth_usdt": 210_000.0,
            "spread_pct": 0.04,
            "slippage_pct": 0.05,
            "footprint": {"available": True, "aggressive_selling": True},
        },
        "derivatives": {
            "available": True,
            "funding_rate": 0.0002,
            "funding_percentile": 0.92,
            "oi_change_1h_pct": 0.8,
            "taker_buy_sell_ratio": 0.70,
            "taker_ratio_change_1h": -0.30,
            "top_trader_long_short_ratio": 2.2,
        },
    }


def test_partial_packet_uses_free_existing_evidence_without_fake_liquidations() -> None:
    packet = build_cascade_evidence(base_metrics())
    assert packet["contract_version"] == "cascade_intelligence_v1"
    # Calibrated: with 3 of 4 components available (trade_flow+derivatives+liquidity = 8.0),
    # cascade evaluates PASS/FAIL instead of staying PARTIAL.
    # base_metrics has strong sell pressure and good derivatives → readiness_pct >= 65% → PASS
    assert packet["status"] in ("PASS", "FAIL")
    assert packet["components"]["liquidations"]["available"] is False
    assert packet["maximum_available"] == 8.0
    assert packet["readiness_points"] >= 6.0
    assert "liquidation_heatmap" not in packet


def test_direct_observed_liquidations_upgrade_packet_without_claiming_latent_levels() -> None:
    metrics = base_metrics()
    metrics["liquidation_flow"] = {
        "available": True,
        "observed_at": 1_788_000_000,
        "long_liquidation_notional_1m": 420_000.0,
        "short_liquidation_notional_1m": 40_000.0,
        "liquidation_velocity_usd_per_min": 420_000.0,
        "burst_ratio": 3.2,
    }
    packet = build_cascade_evidence(metrics, evaluated_at=1_788_000_010)
    assert packet["status"] == "PASS"
    assert packet["maximum_available"] == 10.0
    assert packet["components"]["liquidations"]["long_share"] > 0.9
    assert packet["readiness_points"] > 8.0
    assert packet["latent_liquidation_levels"] is None


def test_active_buying_reduces_cascade_readiness() -> None:
    weak = base_metrics()
    weak["derivatives"]["taker_buy_sell_ratio"] = 1.55
    weak["microstructure"]["sell_flow_usdt"] = 30_000.0
    weak["microstructure"]["buy_flow_usdt"] = 180_000.0
    weak["microstructure"]["footprint"]["aggressive_selling"] = False
    packet = build_cascade_evidence(weak)
    assert packet["readiness_points"] < build_cascade_evidence(base_metrics())["readiness_points"]
    assert packet["components"]["trade_flow"]["sell_dominance"] is False


def test_future_liquidation_observation_is_rejected() -> None:
    metrics = base_metrics()
    metrics["liquidation_flow"] = {
        "available": True,
        "observed_at": 1_788_000_020,
        "long_liquidation_notional_1m": 420_000.0,
        "short_liquidation_notional_1m": 40_000.0,
        "liquidation_velocity_usd_per_min": 420_000.0,
        "burst_ratio": 3.2,
    }
    packet = build_cascade_evidence(metrics, evaluated_at=1_788_000_010)
    assert packet["components"]["liquidations"]["available"] is False
    assert packet["maximum_available"] == 8.0


def test_full_coverage_without_support_is_fail_not_pass() -> None:
    metrics = base_metrics()
    metrics["derivatives"].update({
        "funding_rate": -0.0004,
        "funding_percentile": 0.05,
        "oi_change_1h_pct": -2.0,
        "taker_buy_sell_ratio": 1.8,
        "taker_ratio_change_1h": 0.6,
        "top_trader_long_short_ratio": 0.7,
    })
    metrics["microstructure"].update({
        "sell_flow_usdt": 20_000.0,
        "buy_flow_usdt": 220_000.0,
        "bid_depth_usdt": 250_000.0,
        "ask_depth_usdt": 80_000.0,
        "spread_pct": 0.25,
        "slippage_pct": 0.25,
    })
    metrics["microstructure"]["footprint"]["aggressive_selling"] = False
    metrics["liquidation_flow"] = {
        "available": True,
        "observed_at": 1_788_000_000,
        "long_liquidation_notional_1m": 10_000.0,
        "short_liquidation_notional_1m": 400_000.0,
        "liquidation_velocity_usd_per_min": 1_000.0,
        "burst_ratio": 0.2,
    }
    packet = build_cascade_evidence(metrics, evaluated_at=1_788_000_010)
    assert packet["maximum_available"] == 10.0
    assert packet["readiness_pct"] < 65.0
    assert packet["status"] == "FAIL"


def test_liquidation_observation_within_shared_sixty_second_window_is_fresh() -> None:
    metrics = base_metrics()
    metrics["liquidation_flow"] = {
        "available": True,
        "observed_at": 1_788_000_000,
        "long_liquidation_notional_1m": 420_000.0,
        "short_liquidation_notional_1m": 40_000.0,
        "liquidation_velocity_usd_per_min": 420_000.0,
        "burst_ratio": 3.2,
    }
    packet = build_cascade_evidence(metrics, evaluated_at=1_788_000_055)
    assert packet["components"]["liquidations"]["available"] is True
    assert packet["maximum_available"] == 10.0


# --- Change F tests: multi-factor long-crowding classification -----------

from waterfallhunter.core.cascade_intelligence import (
    LONG_CROWDING_UNCONFIRMED,
    LONG_HEAVY_MARKET,
    LONGS_TRAPPED_FOR_SHORT,
    NOT_LONG_HEAVY,
    classify_long_crowding,
)


def _crowding_metrics(**overrides) -> dict:
    derivatives = {
        "available": True,
        "funding_rate": 0.0002,
        "funding_percentile": 0.6,
        "oi_change_1h_pct": 0.1,
        "taker_buy_sell_ratio": 0.9,
        "taker_ratio_change_1h": -0.1,
        "top_trader_long_short_ratio": 2.2,
    }
    derivatives.update(overrides)
    return {"derivatives": derivatives}


def test_ratio_alone_never_yields_trapped_longs() -> None:
    result = classify_long_crowding(_crowding_metrics())
    assert result["classification"] in (LONG_HEAVY_MARKET, LONG_CROWDING_UNCONFIRMED)
    assert result["classification"] != LONGS_TRAPPED_FOR_SHORT


def test_ratio_below_threshold_is_not_long_heavy() -> None:
    result = classify_long_crowding(_crowding_metrics(top_trader_long_short_ratio=1.1))
    assert result["classification"] == NOT_LONG_HEAVY


def test_ratio_plus_structure_plus_break_plus_corroboration_confirms_trapped_longs() -> None:
    metrics = _crowding_metrics(oi_change_1h_pct=-0.8)
    result = classify_long_crowding(
        metrics,
        bearish_15m_structure=True,
        support_break_close=True,
    )
    assert result["classification"] == LONGS_TRAPPED_FOR_SHORT
    assert "OI_UNWINDING_CONSISTENT" in result["corroboration_reason_codes"]


def test_structure_and_break_without_any_corroboration_is_unconfirmed() -> None:
    # Ratio elevated, structure+break confirmed, but no corroborating
    # OI/funding/liquidation evidence -- must not be enough on its own.
    metrics = _crowding_metrics(oi_change_1h_pct=0.1, funding_percentile=0.5)
    result = classify_long_crowding(
        metrics,
        bearish_15m_structure=True,
        support_break_close=True,
    )
    assert result["classification"] == LONG_CROWDING_UNCONFIRMED
    assert result["classification"] != LONGS_TRAPPED_FOR_SHORT


def test_negative_funding_alone_does_not_qualify_as_trapped_long_confirmation() -> None:
    metrics = _crowding_metrics(funding_rate=-0.0003, funding_percentile=0.5, oi_change_1h_pct=0.1)
    result = classify_long_crowding(metrics)  # no structure/break passed in, none importable
    assert result["classification"] != LONGS_TRAPPED_FOR_SHORT
    assert result["classification"] == LONG_HEAVY_MARKET
    assert result["short_squeeze_risk_flag"] is True
    assert "OI_UNWINDING_CONSISTENT" not in result["corroboration_reason_codes"]
    assert "FUNDING_OVERHEATED_LONG" not in result["corroboration_reason_codes"]


def test_missing_oi_and_funding_data_is_not_positive_evidence() -> None:
    metrics = _crowding_metrics(oi_change_1h_pct=None, funding_percentile=None)
    result = classify_long_crowding(
        metrics,
        bearish_15m_structure=True,
        support_break_close=True,
    )
    # Missing data must not silently corroborate -- with no other evidence
    # this stays UNCONFIRMED at best, never trapped-for-short.
    assert result["classification"] in (LONG_HEAVY_MARKET, LONG_CROWDING_UNCONFIRMED)
    assert result["classification"] != LONGS_TRAPPED_FOR_SHORT
    assert result["corroboration_reason_codes"] == []


def test_liquidation_dominance_can_serve_as_the_corroborating_condition() -> None:
    metrics = _crowding_metrics(oi_change_1h_pct=0.1, funding_percentile=0.5)
    metrics["liquidation_flow"] = {
        "available": True,
        "long_liquidation_notional_1m": 400_000.0,
        "short_liquidation_notional_1m": 30_000.0,
    }
    result = classify_long_crowding(
        metrics,
        bearish_15m_structure=True,
        support_break_close=True,
    )
    assert result["classification"] == LONGS_TRAPPED_FOR_SHORT
    assert "LONG_LIQUIDATIONS_DOMINANT" in result["corroboration_reason_codes"]


def test_derivatives_unavailable_stays_unconfirmed_and_never_crashes() -> None:
    result = classify_long_crowding({"derivatives": {"available": False}})
    assert result["available"] is False
    assert result["classification"] == LONG_CROWDING_UNCONFIRMED
