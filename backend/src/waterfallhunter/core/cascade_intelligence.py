"""Bounded cascade evidence derived from canonical live market observations."""

from __future__ import annotations

import math
from typing import Any

from waterfallhunter.core.liquidation_flow import LIQUIDATION_FLOW_FRESHNESS_SECONDS


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(float(value), upper))


def _ramp(value: float, lower: float, upper: float, maximum: float) -> float:
    if upper <= lower:
        return 0.0
    return _clamp((value - lower) / (upper - lower), 0.0, 1.0) * maximum

def _trade_flow(metrics: dict[str, Any]) -> tuple[dict[str, Any], float, float]:
    micro = _record(metrics.get("microstructure"))
    derivatives = _record(metrics.get("derivatives"))
    taker_ratio = _finite(derivatives.get("taker_buy_sell_ratio"))
    sell_flow = _finite(micro.get("sell_flow_usdt"))
    buy_flow = _finite(micro.get("buy_flow_usdt"))
    footprint = _record(micro.get("footprint"))
    available = taker_ratio is not None or (sell_flow is not None and buy_flow is not None)
    if not available:
        return {"available": False, "reason": "trade flow unavailable"}, 0.0, 0.0

    points = 0.0
    sell_share = None
    if taker_ratio is not None:
        if taker_ratio <= 0.8:
            points += 1.5
        elif taker_ratio < 1.0:
            points += _ramp(1.0 - taker_ratio, 0.0, 0.2, 1.5)
    if sell_flow is not None and buy_flow is not None and sell_flow + buy_flow > 0:
        sell_share = sell_flow / (sell_flow + buy_flow)
        points += _ramp(sell_share, 0.5, 0.75, 1.0)
    if footprint.get("available") is True and footprint.get("aggressive_selling") is True:
        points += 0.5
    sell_dominance = bool((taker_ratio is not None and taker_ratio < 1.0) or (sell_share is not None and sell_share > 0.5))
    return {
        "available": True,
        "taker_buy_sell_ratio": taker_ratio,
        "sell_flow_usdt": sell_flow,
        "buy_flow_usdt": buy_flow,
        "sell_share": round(sell_share, 4) if sell_share is not None else None,
        "sell_dominance": sell_dominance,
    }, _clamp(points, 0.0, 3.0), 3.0

def _derivatives(metrics: dict[str, Any]) -> tuple[dict[str, Any], float, float]:
    derivatives = _record(metrics.get("derivatives"))
    if derivatives.get("available") is not True:
        return {"available": False, "reason": "derivatives unavailable"}, 0.0, 0.0
    funding_percentile = _finite(derivatives.get("funding_percentile"))
    oi_change = _finite(derivatives.get("oi_change_1h_pct"))
    top_ratio = _finite(derivatives.get("top_trader_long_short_ratio"))
    taker_change = _finite(derivatives.get("taker_ratio_change_1h"))
    taker_ratio = _finite(derivatives.get("taker_buy_sell_ratio"))
    points = 0.0
    if top_ratio is not None and (taker_ratio is None or taker_ratio <= 1.5):
        points += _ramp(top_ratio, 1.2, 2.0, 1.0)
    if funding_percentile is not None:
        points += _ramp(funding_percentile, 0.5, 0.95, 0.8)
    if oi_change is not None:
        if oi_change >= 0.5:
            points += 0.8
        elif oi_change > 0:
            points += 0.6
        elif oi_change >= -0.25:
            points += 0.3
    if taker_change is not None and taker_ratio is not None and taker_ratio < 1.0 and taker_change < 0:
        points += _ramp(abs(taker_change), 0.1, 0.4, 0.4)
    return {
        "available": True,
        "funding_rate": _finite(derivatives.get("funding_rate")),
        "funding_percentile": funding_percentile,
        "oi_change_1h_pct": oi_change,
        "top_trader_long_short_ratio": top_ratio,
        "taker_ratio_change_1h": taker_change,
    }, _clamp(points, 0.0, 3.0), 3.0


def _liquidity(metrics: dict[str, Any]) -> tuple[dict[str, Any], float, float]:
    micro = _record(metrics.get("microstructure"))
    bid_depth = _finite(micro.get("bid_depth_usdt"))
    ask_depth = _finite(micro.get("ask_depth_usdt"))
    spread = _finite(micro.get("spread_pct"))
    slippage = _finite(micro.get("slippage_pct"))
    if bid_depth is None or ask_depth is None or spread is None or slippage is None:
        return {"available": False, "reason": "liquidity packet unavailable"}, 0.0, 0.0

    total_depth = bid_depth + ask_depth
    ask_share = ask_depth / total_depth if total_depth > 0 else 0.5
    points = _ramp(ask_share, 0.5, 0.7, 0.9)
    points += _ramp(0.12 - spread, 0.0, 0.10, 0.55)
    points += _ramp(0.20 - slippage, 0.0, 0.18, 0.55)
    return {
        "available": True,
        "bid_depth_usdt": bid_depth,
        "ask_depth_usdt": ask_depth,
        "ask_depth_share": round(ask_share, 4),
        "spread_pct": spread,
        "slippage_pct": slippage,
        "sell_side_liquidity_pressure": ask_share > 0.55,
    }, _clamp(points, 0.0, 2.0), 2.0


def _liquidations(metrics: dict[str, Any], evaluated_at: int | None) -> tuple[dict[str, Any], float, float]:
    packet = _record(metrics.get("liquidation_flow"))
    if packet.get("available") is not True:
        return {"available": False, "reason": "observed liquidation flow unavailable"}, 0.0, 0.0
    observed_at = _finite(packet.get("observed_at"))
    long_notional = _finite(packet.get("long_liquidation_notional_1m"))
    short_notional = _finite(packet.get("short_liquidation_notional_1m"))
    velocity = _finite(packet.get("liquidation_velocity_usd_per_min"))
    burst_ratio = _finite(packet.get("burst_ratio"))
    if None in {observed_at, long_notional, short_notional, velocity, burst_ratio}:
        return {"available": False, "reason": "incomplete liquidation flow"}, 0.0, 0.0
    if evaluated_at is not None and not 0 <= evaluated_at - observed_at < LIQUIDATION_FLOW_FRESHNESS_SECONDS:
        return {"available": False, "reason": "stale or future liquidation flow"}, 0.0, 0.0
    total = long_notional + short_notional
    long_share = long_notional / total if total > 0 else 0.0
    points = _ramp(long_share, 0.55, 0.9, 0.8)
    points += _ramp(velocity, 50_000.0, 400_000.0, 0.6)
    points += _ramp(burst_ratio, 1.0, 3.0, 0.6)
    return {
        "available": True,
        "observed_at": observed_at,
        "long_liquidation_notional_1m": long_notional,
        "short_liquidation_notional_1m": short_notional,
        "long_share": round(long_share, 4),
        "liquidation_velocity_usd_per_min": velocity,
        "burst_ratio": burst_ratio,
    }, _clamp(points, 0.0, 2.0), 2.0


def build_cascade_evidence(
    metrics: dict[str, Any],
    *,
    evaluated_at: int | None = None,
) -> dict[str, Any]:
    """Build a bounded cascade packet without inventing latent liquidation levels."""
    components: dict[str, dict[str, Any]] = {}
    total_points = 0.0
    maximum_available = 0.0
    for name, builder in (
        ("trade_flow", lambda: _trade_flow(metrics)),
        ("derivatives", lambda: _derivatives(metrics)),
        ("liquidity", lambda: _liquidity(metrics)),
        ("liquidations", lambda: _liquidations(metrics, evaluated_at)),
    ):
        component, points, maximum = builder()
        components[name] = {**component, "points": round(points, 3), "maximum": maximum}
        if component.get("available") is True:
            total_points += points
            maximum_available += maximum

    readiness_pct = (total_points / maximum_available * 100.0) if maximum_available else None
    if maximum_available == 0:
        status = "UNAVAILABLE"
    elif maximum_available >= 4.0:
        # Any two components can supply >=4 points (trade-flow + derivatives
        # are 3+3; either plus liquidity/liquidations is 3+2). The previous
        # comment said "at least 3 of 4", which was mathematically false and
        # encouraged operators to believe PASS had more independent evidence
        # than it did. Liquidation flow is often unavailable because it needs
        # real-time WS, so it must not permanently leave cascade PARTIAL.
        status = "PASS" if readiness_pct is not None and readiness_pct >= 50.0 else "FAIL"
    else:
        status = "PARTIAL"
    return {
        "contract_version": "cascade_intelligence_v1",
        "status": status,
        "readiness_points": round(total_points, 3),
        "maximum_available": round(maximum_available, 3),
        "readiness_pct": round(readiness_pct, 2) if readiness_pct is not None else None,
        "components": components,
        "latent_liquidation_levels": None,
    }


# --- Change F: multi-factor long-crowding classification -----------------
#
# Previously, any consumer that saw top_trader_long_short_ratio >= 1.5 alone
# treated that as "long crowding present" (see entry_decision.py's
# LONG_CROWDING_PRESENT reason code, which this function does not replace --
# entry_decision.py is out of scope for this change and is owned by another
# workstream). A single elevated ratio is evidence that longs are the
# dominant position, not evidence that those longs are trapped and about to
# be forced out. classify_long_crowding() below produces a real
# classification with three possible outcomes so callers (once wired up)
# can distinguish "market is long-heavy" from "longs are demonstrably
# trapped and unwinding" from "we genuinely cannot tell yet".

LONG_HEAVY_MARKET = "LONG_HEAVY_MARKET"
LONGS_TRAPPED_FOR_SHORT = "LONGS_TRAPPED_FOR_SHORT"
LONG_CROWDING_UNCONFIRMED = "LONG_CROWDING_UNCONFIRMED"
NOT_LONG_HEAVY = "NOT_LONG_HEAVY"

DEFAULT_LONG_SHORT_RATIO_THRESHOLD = 1.5


def _safe_structure_signal(metrics: dict[str, Any]) -> dict[str, Any]:
    """Best-effort, fail-closed lookup of the deterministic bearish-structure /
    absorption signal that another workstream is adding to candle_analyzer.py.

    That module may not yet expose this function (or may expose it under a
    slightly different name/shape) at the time this code runs. Any import
    failure, missing attribute, or exception while calling it must NOT crash
    crowding classification, and missing/unavailable structure data must
    fail closed -- i.e. be treated as "not confirmed", never as "confirmed".
    We deliberately do not import candle_analyzer.py at module load time so
    this file keeps working even if that module is mid-edit or absent.
    """
    try:
        from waterfallhunter.core import candle_analyzer  # type: ignore
    except Exception:
        return {}

    evaluator = getattr(candle_analyzer, "evaluate_bearish_15m_structure", None) or getattr(
        candle_analyzer, "evaluate_bearish_structure", None
    )
    if evaluator is None:
        return {}
    try:
        result = evaluator(metrics)
    except Exception:
        return {}
    if not isinstance(result, dict) or result.get("available") is not True:
        return {}
    return {
        "bearish_15m_structure": result.get("bearish_15m_structure"),
        "support_break_close": result.get("support_break_close"),
        "downside_displacement_confirmed": result.get("downside_displacement_confirmed"),
    }


def classify_long_crowding(
    metrics: dict[str, Any],
    *,
    bearish_15m_structure: bool | None = None,
    support_break_close: bool | None = None,
    downside_displacement_confirmed: bool | None = None,
    long_short_ratio_threshold: float = DEFAULT_LONG_SHORT_RATIO_THRESHOLD,
) -> dict[str, Any]:
    """Classify what an elevated top-trader long/short ratio actually means.

    Outcomes:
      * NOT_LONG_HEAVY -- ratio missing or below threshold; not applicable.
      * LONG_HEAVY_MARKET -- ratio elevated, no corroborating evidence that
        those longs are trapped. This must never be treated as short-entry
        confirmation.
      * LONG_CROWDING_UNCONFIRMED -- ratio elevated and *some* (but not all)
        of the required evidence is present (e.g. deterministic bearish
        structure confirmed but no corroborating derivatives/liquidation
        evidence, or vice versa). Insufficient to call it either way. Must
        NEVER be treated as supporting ENTRY_READY.
      * LONGS_TRAPPED_FOR_SHORT -- ratio elevated AND deterministic bearish
        15m structure confirmed AND a support-break close confirmed AND at
        least one independent corroborating condition from data that
        actually exists in this codebase.

    `bearish_15m_structure` / `support_break_close` /
    `downside_displacement_confirmed` come from another agent's new
    candle_analyzer.py function, which may not exist yet. Callers may pass
    them explicitly once wired up; if omitted, we attempt a guarded,
    fail-closed lookup via `_safe_structure_signal`.

    Corroborating conditions actually wired up in this codebase (derivatives.py
    / cascade evidence), used because the underlying fields are genuinely
    populated here:
      * OI_UNWINDING_CONSISTENT -- oi_change_1h_pct <= -0.5 (open interest
        actively contracting, consistent with forced long deleveraging).
      * FUNDING_OVERHEATED_LONG -- funding_percentile >= 0.95 (funding is at
        a multi-period extreme, i.e. longs are paying a historically high
        premium to stay positioned -- a positioning-extremity signal, NOT a
        raw negative-funding signal).
      * LONG_LIQUIDATIONS_DOMINANT -- observed liquidation_flow packet shows
        long-side liquidation notional dominating short-side (>=55% share),
        i.e. actual forced long liquidations are happening right now.
      * DOWNSIDE_DISPLACEMENT_CONFIRMED -- passed in (or discovered via
        `_safe_structure_signal`) from the other agent's structure work.

    Explicitly NOT used as corroboration, by design:
      * Negative funding_rate alone. Negative funding alone is not proof of
        a short entry -- it only means shorts are currently paying longs,
        which is a crowd-positioning signal, not evidence longs are trapped.
        We still surface it as an informational `short_squeeze_risk_flag`
        (mirroring/never contradicting the existing SHORT_SQUEEZE_RISK
        concept elsewhere in the codebase) but it never counts toward
        LONGS_TRAPPED_FOR_SHORT.
      * Order-book depth / OI absolute level -- not genuinely available as
        forced-unwinding evidence in this codebase beyond the OI *change*
        already used above, so no additional claim is made about them.

    Missing OI/funding/liquidation data never counts as positive evidence --
    if a field is absent, that corroborating condition simply does not
    trigger (it is not treated as true, and not treated as a bad-outcome
    default either; it just does not count).
    """
    derivatives = _record(metrics.get("derivatives"))
    if derivatives.get("available") is not True:
        return {
            "available": False,
            "classification": LONG_CROWDING_UNCONFIRMED,
            "reason": "derivatives unavailable",
            "reason_codes": ["DERIVATIVES_UNAVAILABLE", LONG_CROWDING_UNCONFIRMED],
        }

    top_ratio = _finite(derivatives.get("top_trader_long_short_ratio"))
    if top_ratio is None or top_ratio < long_short_ratio_threshold:
        return {
            "available": True,
            "classification": NOT_LONG_HEAVY,
            "top_trader_long_short_ratio": top_ratio,
            "long_short_ratio_threshold": long_short_ratio_threshold,
            "reason_codes": [NOT_LONG_HEAVY],
        }

    funding_rate = _finite(derivatives.get("funding_rate"))
    funding_percentile = _finite(derivatives.get("funding_percentile"))
    oi_change = _finite(derivatives.get("oi_change_1h_pct"))

    if bearish_15m_structure is None or support_break_close is None or downside_displacement_confirmed is None:
        imported = _safe_structure_signal(metrics)
        if bearish_15m_structure is None:
            bearish_15m_structure = imported.get("bearish_15m_structure")
        if support_break_close is None:
            support_break_close = imported.get("support_break_close")
        if downside_displacement_confirmed is None:
            downside_displacement_confirmed = imported.get("downside_displacement_confirmed")

    structure_confirmed = bearish_15m_structure is True and support_break_close is True

    corroboration_reason_codes: list[str] = []
    if oi_change is not None and oi_change <= -0.5:
        corroboration_reason_codes.append("OI_UNWINDING_CONSISTENT")
    if funding_percentile is not None and funding_percentile >= 0.95:
        corroboration_reason_codes.append("FUNDING_OVERHEATED_LONG")

    liquidations = _record(metrics.get("liquidation_flow"))
    if liquidations.get("available") is True:
        long_notional = _finite(liquidations.get("long_liquidation_notional_1m"))
        short_notional = _finite(liquidations.get("short_liquidation_notional_1m"))
        if long_notional is not None and short_notional is not None and (long_notional + short_notional) > 0:
            long_share = long_notional / (long_notional + short_notional)
            if long_share >= 0.55:
                corroboration_reason_codes.append("LONG_LIQUIDATIONS_DOMINANT")
    if downside_displacement_confirmed is True:
        corroboration_reason_codes.append("DOWNSIDE_DISPLACEMENT_CONFIRMED")

    has_corroboration = len(corroboration_reason_codes) > 0

    # Informational only -- never used as trapped-long proof by itself.
    short_squeeze_risk_flag = funding_rate is not None and funding_rate < 0

    if structure_confirmed and has_corroboration:
        classification = LONGS_TRAPPED_FOR_SHORT
    elif structure_confirmed or has_corroboration:
        classification = LONG_CROWDING_UNCONFIRMED
    else:
        classification = LONG_HEAVY_MARKET

    reason_codes = [classification] + corroboration_reason_codes
    if short_squeeze_risk_flag:
        reason_codes.append("SHORT_SQUEEZE_RISK")

    return {
        "available": True,
        "classification": classification,
        "top_trader_long_short_ratio": top_ratio,
        "long_short_ratio_threshold": long_short_ratio_threshold,
        "bearish_15m_structure": bearish_15m_structure,
        "support_break_close": support_break_close,
        "downside_displacement_confirmed": downside_displacement_confirmed,
        "structure_confirmed": structure_confirmed,
        "corroboration_reason_codes": corroboration_reason_codes,
        "short_squeeze_risk_flag": short_squeeze_risk_flag,
        "reason_codes": reason_codes,
    }
