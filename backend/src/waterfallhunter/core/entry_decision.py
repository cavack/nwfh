"""Canonical, user-facing entry decision for WaterfallHunter.

The engine is pure and intentionally separates lifecycle context from the
single actionable decision exposed to the dashboard and notifications.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EntryDecisionPolicy:
    version: str = "entry_policy_v2_calibrated"
    forming_minimum: float = 55.0
    entry_ready_minimum: float = 72.0
    max_analysis_age_seconds: float = 600.0
    max_reference_age_seconds: float = 60.0
    anti_chase_hard_block_atr: float = 2.5
    maximum_spread_pct: float = 0.30
    maximum_slippage_pct: float = 0.30
    coverage_minimum: float = 55.0
    # Gate switches. Every one defaults to enabled, so an operator has to
    # deliberately weaken the contract; nothing is loosened by omission.
    gate_cascade_required: bool = True
    gate_cross_exchange_required: bool = True
    gate_timing_required: bool = True
    gate_execution_required: bool = True
    gate_anti_chase_enabled: bool = True
    gate_freshness_required: bool = True

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None) -> "EntryDecisionPolicy":
        """Build a policy from operator settings, ignoring unknown keys.

        Settings are applied at decision time, so a change affects only
        decisions taken after it was saved. Anything missing or malformed
        falls back to the shipped default for that field.
        """
        if not isinstance(settings, dict):
            return cls()
        allowed = {f for f in cls.__dataclass_fields__ if f != "version"}
        kwargs: dict[str, Any] = {}
        for key, value in settings.items():
            if key not in allowed:
                continue
            if key.startswith("gate_"):
                if isinstance(value, bool):
                    kwargs[key] = value
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                number = float(value)
                if math.isfinite(number):
                    kwargs[key] = number
        if not kwargs:
            return cls()
        # Mark the policy so a persisted packet shows it was not the stock one -
        # but only when a value actually differs. Writing the shipped defaults
        # back unchanged, or a reset to shipped defaults, leaves the effective
        # policy identical to the stock one, and an immutable decision packet
        # must not claim it was produced by an operator-tuned policy when it
        # was not. This happened in Production: after a reset to shipped
        # defaults every packet still recorded `entry_policy_v2_operator_tuned`
        # while every threshold equalled the shipped default.
        stock = cls()
        if all(getattr(stock, key) == value for key, value in kwargs.items()):
            return stock
        return cls(version="entry_policy_v2_operator_tuned", **kwargs)


# --- Change B: lifecycle veto -------------------------------------------
# Lifecycle states that represent an explicit, confirmed trigger. PRE-TRIGGER
# (and any earlier watch-only state such as WATCH / FUEL-RICH) is monitoring
# only and must never be treated as entry-ready by score alone: the gate
# below is an explicit membership check against real lifecycle strings, never
# a threshold/heuristic derived from readiness or any other score. ARMED is
# kept eligible because the existing ACTIVE-after-ENTRY_READY continuity
# contract (see the predecessor check in ``build_entry_decision``) already
# depends on ARMED being able to produce ENTRY_READY ahead of TRIGGERED; only
# PRE-TRIGGER/WATCH/FUEL-RICH and any other non-triggered state are newly
# blocked here.
_TRIGGER_CONFIRMED_LIFECYCLE_STATES = frozenset({"ARMED", "TRIGGERED"})
LIFECYCLE_NOT_TRIGGERED = "LIFECYCLE_NOT_TRIGGERED"

# --- Change C: AI-neutral cap --------------------------------------------
AI_NEUTRAL_WITHOUT_BEARISH_STRUCTURE = "AI_NEUTRAL_WITHOUT_BEARISH_STRUCTURE"


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


def _has_number(packet: dict[str, Any], key: str) -> bool:
    return _finite(packet.get(key)) is not None


def _lifecycle_confirmed(status: str) -> bool:
    """Explicit lifecycle-state membership check, never a score heuristic.

    ENTRY_READY/ACTIVE must require the candidate to actually be in a
    trigger-confirmed lifecycle state. A high readiness score, favorable
    evidence, or any other derived signal must never substitute for this.
    """
    return status in _TRIGGER_CONFIRMED_LIFECYCLE_STATES


def _bearish_structure_confirmed(metrics: dict[str, Any]) -> bool:
    """Optional-dependency hook for the deterministic bearish structure gate.

    A parallel work-stream is adding a deterministic bearish 15m
    price-structure / support-break-close gate to ``candle_analyzer.py``
    (e.g. ``bearish_15m_structure`` / ``support_break_close``). That module
    may not exist yet, or may not yet expose either function. This call is
    therefore fully defensive: any import failure, missing attribute, raised
    exception, or non-``True`` return is treated as "structure NOT
    confirmed" (fail closed) rather than crashing or being treated as
    confirmation. Once the other work-stream lands its gate, it slots in
    here automatically with no further coordination required.
    """
    try:
        from waterfallhunter.core import candle_analyzer  # type: ignore[import-not-found]
    except ImportError:
        return False
    checker = getattr(candle_analyzer, "bearish_15m_structure", None)
    if checker is None:
        checker = getattr(candle_analyzer, "support_break_close", None)
    if not callable(checker):
        return False
    try:
        result = checker(metrics)
    except Exception:
        return False
    return result is True


def _ai_neutral_or_unavailable(metrics: dict[str, Any]) -> bool:
    """True when a directional-sentiment AI advisory ran and carries no
    directional confirmation.

    Covers NEUTRAL, stale, or invalid AI evidence alike -- anything short of
    an explicit, available, short-supporting verdict is folded into
    "neutral" here. AI is advisory-only: it is never treated as
    bullish/bearish confirmation on its own.

    IMPORTANT -- key naming: this deliberately reads "ai_sentiment_advisory",
    NOT "ai_advisory". "ai_advisory" is already used by the pre-existing,
    unrelated deterministic order-book/ticker veto populated by
    ``ai_veto.evaluate_deterministic`` in main.py's
    ``_apply_deterministic_entry_gate`` (see ``deterministic_veto`` /
    ``DETERMINISTIC_MARKET_DATA_VETO`` below) -- that dict is present for
    essentially every live candidate and has a completely different shape
    (no "ai_status"/"ai_advice" fields). Reusing that key here would make
    this cap fire for almost every real candidate, turning it into a new,
    unintended mandatory gate. No directional-sentiment AI subsystem exists
    in this codebase yet, so "ai_sentiment_advisory" is correctly absent for
    every real candidate today, keeping this cap dormant (fail-closed as "no
    cap") until such a subsystem is actually wired in -- consistent with
    "do not add a new mandatory AI dependency where none exists".
    """
    if "ai_sentiment_advisory" not in metrics:
        return False
    advisory = _record(metrics.get("ai_sentiment_advisory"))
    status = str(advisory.get("ai_status") or "").upper()
    if status != "AVAILABLE":
        return True
    advice = str(advisory.get("ai_advice") or "").upper()
    if advice == "SUPPORTS_SHORT" and advisory.get("ai_answer_yes") is True:
        return False
    return True


def _structure_points(metrics: dict[str, Any]) -> tuple[float, float, list[str]]:
    candles = _record(metrics.get("candle_features"))
    context = _record(candles.get("4h"))
    if context.get("valid") is not True:
        return 0.0, 0.0, ["STRUCTURE_UNAVAILABLE"]
    score = 0.0
    score += 4.0 if context.get("hype_context") is True else 0.0
    score += 4.0 if context.get("lower_high") is True else 0.0
    score += 5.0 if context.get("setup") == "FAILED_PULLBACK" else 0.0
    score += 3.0 if context.get("bearish_close") is True else 0.0
    score += 2.0 if context.get("volume_acceleration") is True else 0.0
    reasons = ["STRUCTURE_SUPPORTIVE"] if score >= 10.0 else ["STRUCTURE_WEAK"]
    return score, 20.0, reasons


def _timing_points(metrics: dict[str, Any]) -> tuple[float, float, list[str]]:
    candles = _record(metrics.get("candle_features"))
    score = 0.0
    available = 0.0
    for timeframe in ("1h", "15m", "5m"):
        context = _record(candles.get(timeframe))
        if context.get("valid") is not True:
            continue
        available += 5.0
        ready = bool(
            context.get("lower_high") is True
            and (context.get("reclaim") is True or context.get("repump") is True)
            and context.get("rsi_rollover") is True
            and context.get("bearish_close") is True
        )
        if ready:
            score += 5.0
    if available == 0:
        return 0.0, 0.0, ["TIMING_UNAVAILABLE"]
    # ``gates_pass`` requires one confirming lower timeframe (5 points). The
    # old reason called that same packet TIMING_INCOMPLETE until two timeframes
    # confirmed, so an ENTRY_READY decision could carry a reason that claimed
    # one of its required gates failed. Preserve the stronger signal as extra
    # provenance without lying about the gate that actually passed.
    if score >= 10.0:
        return score, available, ["TIMING_CONFIRMED", "TIMING_MULTI_CONFIRMED"]
    if score >= 5.0:
        return score, available, ["TIMING_CONFIRMED"]
    return score, available, ["TIMING_INCOMPLETE"]


def _taker_ratio_points(taker_ratio: float | None) -> tuple[float, float]:
    if taker_ratio is None:
        return 0.0, 0.0
    if taker_ratio <= 0.8:
        score = 10.0
    elif taker_ratio <= 0.9:
        score = 8.0
    elif taker_ratio < 1.0:
        score = 6.0
    elif taker_ratio <= 1.1:
        score = 3.0
    else:
        score = 0.0
    return score, 10.0


def _flow_imbalance_points(
    sell_flow: float | None, buy_flow: float | None
) -> tuple[float, float]:
    if sell_flow is None or buy_flow is None:
        return 0.0, 0.0
    total = sell_flow + buy_flow
    score = _ramp(sell_flow / total, 0.5, 0.75, 5.0) if total > 0 else 0.0
    return score, 5.0


def _footprint_points(footprint: dict[str, Any]) -> tuple[float, float]:
    aggressive = footprint.get("aggressive_selling")
    if not isinstance(aggressive, bool):
        return 0.0, 0.0
    score = 3.0 if footprint.get("available") is True and aggressive is True else 0.0
    return score, 3.0


def _micro_approval_points(micro: dict[str, Any]) -> tuple[float, float]:
    approved = micro.get("approved")
    if not isinstance(approved, bool):
        return 0.0, 0.0
    score = 2.0 if approved is True and micro.get("spoofing_detected") is not True else 0.0
    return score, 2.0


def _order_flow_points(metrics: dict[str, Any]) -> tuple[float, float, list[str], bool]:
    micro = _record(metrics.get("microstructure"))
    derivatives = _record(metrics.get("derivatives"))
    taker_ratio = _finite(derivatives.get("taker_buy_sell_ratio"))
    sell_flow = _finite(micro.get("sell_flow_usdt"))
    buy_flow = _finite(micro.get("buy_flow_usdt"))
    point_pairs = (
        _taker_ratio_points(taker_ratio),
        _flow_imbalance_points(sell_flow, buy_flow),
        _footprint_points(_record(micro.get("footprint"))),
        _micro_approval_points(micro),
    )
    score = sum(pair[0] for pair in point_pairs)
    available = sum(pair[1] for pair in point_pairs)
    direction_ok = bool(
        (taker_ratio is not None and taker_ratio < 1.0)
        or (sell_flow is not None and buy_flow is not None and sell_flow > buy_flow)
    )
    reason = "SELL_PRESSURE_CONFIRMED" if direction_ok else "BUYERS_ACTIVE"
    return score, available, [reason], direction_ok

def _funding_points(funding: float | None) -> tuple[float, float]:
    if funding is None:
        return 0.0, 0.0
    return (min(2.0, _ramp(funding, 0.0, 0.0005, 2.0)) if funding > 0 else 0.0), 2.0


def _funding_percentile_points(value: float | None) -> tuple[float, float]:
    if value is None:
        return 0.0, 0.0
    return _ramp(value, 0.5, 0.95, 4.0), 4.0


def _oi_points(value: float | None) -> tuple[float, float]:
    if value is None:
        return 0.0, 0.0
    if value >= 0.5:
        score = 4.0
    elif value > 0:
        score = 3.0
    elif value >= -0.25:
        score = 2.0
    elif value >= -0.5:
        score = 1.0
    else:
        score = 0.0
    return score, 4.0


def _top_ratio_points(top_ratio: float | None, taker_ratio: float | None) -> tuple[float, float]:
    if top_ratio is None:
        return 0.0, 0.0
    score = _ramp(top_ratio, 1.2, 2.0, 3.0) if taker_ratio is None or taker_ratio <= 1.5 else 0.0
    return score, 3.0


def _taker_change_points(taker_change: float | None, taker_ratio: float | None) -> tuple[float, float]:
    if taker_change is None:
        return 0.0, 0.0
    score = (
        _ramp(abs(taker_change), 0.1, 0.4, 2.0)
        if taker_ratio is not None and taker_ratio < 1.0 and taker_change < 0
        else 0.0
    )
    return score, 2.0


def _derivative_reasons(
    *, funding: float | None, oi_change: float | None, top_ratio: float | None
) -> list[str]:
    reasons: list[str] = []
    if top_ratio is not None and top_ratio >= 1.5:
        reasons.append("LONG_CROWDING_PRESENT")
    if funding is not None and funding < 0:
        reasons.append("SHORT_SQUEEZE_RISK")
    if oi_change is not None and oi_change <= -0.5:
        reasons.append("OI_UNWINDING")
    return reasons


def _derivative_points(metrics: dict[str, Any]) -> tuple[float, float, list[str]]:
    derivatives = _record(metrics.get("derivatives"))
    if derivatives.get("available") is not True:
        return 0.0, 0.0, ["DERIVATIVES_UNAVAILABLE"]
    funding = _finite(derivatives.get("funding_rate"))
    funding_percentile = _finite(derivatives.get("funding_percentile"))
    oi_change = _finite(derivatives.get("oi_change_1h_pct"))
    taker_change = _finite(derivatives.get("taker_ratio_change_1h"))
    top_ratio = _finite(derivatives.get("top_trader_long_short_ratio"))
    taker_ratio = _finite(derivatives.get("taker_buy_sell_ratio"))
    point_pairs = (
        _funding_points(funding),
        _funding_percentile_points(funding_percentile),
        _oi_points(oi_change),
        _top_ratio_points(top_ratio, taker_ratio),
        _taker_change_points(taker_change, taker_ratio),
    )
    return (
        sum(pair[0] for pair in point_pairs),
        sum(pair[1] for pair in point_pairs),
        _derivative_reasons(funding=funding, oi_change=oi_change, top_ratio=top_ratio),
    )

def _execution_approval_points(micro: dict[str, Any]) -> tuple[float, float]:
    approved = micro.get("approved")
    if not isinstance(approved, bool):
        return 0.0, 0.0
    return (4.0 if approved and micro.get("spoofing_detected") is not True else 0.0), 4.0


def _execution_friction_points(value: float | None, maximum: float) -> tuple[float, float]:
    if value is None:
        return 0.0, 0.0
    if value <= 0.10:
        score = 2.0
    elif value <= maximum:
        score = 1.0
    else:
        score = 0.0
    return score, 2.0


def _execution_depth_points(bid_depth: float | None, ask_depth: float | None) -> tuple[float, float]:
    if bid_depth is None or ask_depth is None:
        return 0.0, 0.0
    return (2.0 if bid_depth > 0 and ask_depth > 0 else 0.0), 2.0


def _execution_points(metrics: dict[str, Any], policy: EntryDecisionPolicy) -> tuple[float, float, list[str], bool]:
    micro = _record(metrics.get("microstructure"))
    approved = micro.get("approved")
    spread = _finite(micro.get("spread_pct"))
    slippage = _finite(micro.get("slippage_pct"))
    bid_depth = _finite(micro.get("bid_depth_usdt"))
    ask_depth = _finite(micro.get("ask_depth_usdt"))
    point_pairs = (
        _execution_approval_points(micro),
        _execution_friction_points(spread, policy.maximum_spread_pct),
        _execution_friction_points(slippage, policy.maximum_slippage_pct),
        _execution_depth_points(bid_depth, ask_depth),
    )
    execution_ok = bool(
        approved is True
        and spread is not None
        and slippage is not None
        and spread <= policy.maximum_spread_pct
        and slippage <= policy.maximum_slippage_pct
    )
    reason = "EXECUTION_OK" if execution_ok else "EXECUTION_DEGRADED"
    return sum(pair[0] for pair in point_pairs), sum(pair[1] for pair in point_pairs), [reason], execution_ok

def _cross_exchange_points(metrics: dict[str, Any]) -> tuple[float, float, list[str], bool]:
    breakdown = _record(metrics.get("breakdown_confirmation"))
    value = breakdown.get("confirmation_exchange_15m")
    if not isinstance(value, bool):
        return 0.0, 0.0, ["CROSS_EXCHANGE_UNAVAILABLE"], False
    # Calibrated: cross_exchange is a soft gate. Disagreement reduces points
    # but does not hard-block ENTRY_READY. Only explicit disagreement blocks.
    confirmed = value is True
    if confirmed:
        return 5.0, 5.0, ["CROSS_EXCHANGE_CONFIRMED"], True
    return 0.0, 5.0, ["CROSS_EXCHANGE_DISAGREEMENT"], False


def _price_location_points(metrics: dict[str, Any]) -> tuple[float, float, list[str]]:
    location = _record(metrics.get("price_location"))
    below_vwap = location.get("below_vwap")
    if not isinstance(below_vwap, bool):
        return 0.0, 0.0, ["PRICE_LOCATION_UNAVAILABLE"]
    return (5.0 if below_vwap else 0.0), 5.0, ["BELOW_VWAP" if below_vwap else "ABOVE_VWAP"]


def _cascade_points(metrics: dict[str, Any]) -> tuple[float, float, list[str]]:
    cascade = _record(metrics.get("cascade_intelligence"))
    points = _finite(cascade.get("readiness_points"))
    maximum = _finite(cascade.get("maximum_available"))
    status = str(cascade.get("status") or "UNAVAILABLE")
    if points is None or maximum is None or maximum <= 0.0 or status == "UNAVAILABLE":
        return 0.0, 0.0, ["CASCADE_EVIDENCE_UNAVAILABLE"]
    available = _clamp(maximum, 0.0, 10.0)
    return _clamp(points, 0.0, available), available, [f"CASCADE_{status}"]


def _leverage_advisory(metrics: dict[str, Any]) -> dict[str, Any] | None:
    advisory = _record(metrics.get("leverage_advisory"))
    status = str(advisory.get("status") or "")
    if status not in {"AVAILABLE", "UNAVAILABLE", "NOT_RECOMMENDED"}:
        return None
    leverage = _finite(advisory.get("leverage"))
    if status == "AVAILABLE":
        if leverage is None or leverage < 4 or leverage > 18:
            return None
    else:
        leverage = None
    packet = {
        "status": status,
        "leverage": leverage,
        "policy_version": str(advisory.get("policy_version") or ""),
        "reason": advisory.get("reason") if isinstance(advisory.get("reason"), str) else None,
    }
    execution_input = advisory.get("execution_suitability_input")
    if isinstance(execution_input, dict):
        packet["execution_suitability_input"] = dict(execution_input)
    causal_input = advisory.get("causal_input")
    if isinstance(causal_input, dict):
        packet["causal_input"] = {
            key: dict(value) if isinstance(value, dict) else value
            for key, value in causal_input.items()
        }
    return packet


def project_leverage_advisory(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """Return the bounded leverage advisory persisted with a canonical decision."""
    return _leverage_advisory(metrics)


def _trade_plan(metrics: dict[str, Any]) -> dict[str, Any] | None:
    setup = _record(metrics.get("position_setup"))
    required = ("entry_price", "stop_loss", "take_profit_1", "take_profit_2")
    if str(setup.get("status") or "").upper().startswith("REJECTED"):
        return None
    if not all(_has_number(setup, key) for key in required):
        return None
    plan = {
        "entry_price": float(setup["entry_price"]),
        "stop_loss": float(setup["stop_loss"]),
        "take_profit_1": float(setup["take_profit_1"]),
        "take_profit_2": float(setup["take_profit_2"]),
        "take_profit_3": _finite(setup.get("take_profit_3")),
        "reward_to_risk": _finite(setup.get("reward_to_risk")),
        "leverage": _finite(metrics.get("applied_leverage")),
    }
    # These are execution facts captured when the plan was calculated. Carry
    # them into the immutable decision packet so Telegram and the dashboard do
    # not have to read a later, potentially changed live-metrics snapshot.
    for key in (
        "risk_pct",
        "stop_basis",
        "atr_pct",
        "atr_stop_multiple",
        "reference_divergence_pct",
        "margin_mode",
    ):
        value = setup.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            plan[key] = value
    expires_at = setup.get("expires_at")
    if isinstance(expires_at, int) and not isinstance(expires_at, bool) and expires_at >= 0:
        plan["expires_at"] = expires_at
    return plan

def _anti_chase_extension(metrics: dict[str, Any]) -> float | None:
    anti = _record(metrics.get("anti_chase"))
    cross = _record(anti.get("cross_timeframe"))
    direct = _finite(cross.get("max_post_break_extension_atr"))
    if direct is not None:
        return direct
    candles = _record(metrics.get("candle_features"))
    extensions = [
        value
        for packet in candles.values()
        if isinstance(packet, dict)
        and (value := _finite(packet.get("extension_from_support_atr"))) is not None
    ]
    return max(extensions) if extensions else None


def _evidence_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    derivatives = _record(metrics.get("derivatives"))
    micro = _record(metrics.get("microstructure"))
    cascade = _record(metrics.get("cascade_intelligence"))
    breakdown = _record(metrics.get("breakdown_confirmation"))
    anti = _record(metrics.get("anti_chase"))
    sell_flow = _finite(micro.get("sell_flow_usdt"))
    buy_flow = _finite(micro.get("buy_flow_usdt"))
    sell_share = None
    if sell_flow is not None and buy_flow is not None and sell_flow + buy_flow > 0:
        sell_share = 100.0 * sell_flow / (sell_flow + buy_flow)
    funding_rate = _finite(derivatives.get("funding_rate"))
    return {
        "derivatives": {
            "funding_rate": funding_rate,
            "funding_rate_pct": funding_rate * 100.0 if funding_rate is not None else None,
            "funding_percentile": _finite(derivatives.get("funding_percentile")),
            "oi_change_1h_pct": _finite(derivatives.get("oi_change_1h_pct")),
            "top_trader_long_short_ratio": _finite(derivatives.get("top_trader_long_short_ratio")),
        },
        "order_flow": {
            "taker_buy_sell_ratio": _finite(derivatives.get("taker_buy_sell_ratio")),
            "taker_ratio_change_1h": _finite(derivatives.get("taker_ratio_change_1h")),
            "sell_flow_usdt": sell_flow,
            "buy_flow_usdt": buy_flow,
            "sell_share_pct": round(sell_share, 2) if sell_share is not None else None,
        },
        "execution": {
            "spread_pct": _finite(micro.get("spread_pct")),
            "slippage_pct": _finite(micro.get("slippage_pct")),
            "bid_depth_usdt": _finite(micro.get("bid_depth_usdt")),
            "ask_depth_usdt": _finite(micro.get("ask_depth_usdt")),
        },
        "cascade": {
            "status": cascade.get("status"),
            "readiness_points": _finite(cascade.get("readiness_points")),
            "maximum_available": _finite(cascade.get("maximum_available")),
            "components": cascade.get("components") if isinstance(cascade.get("components"), dict) else {},
        },
        "cross_exchange_confirmed": breakdown.get("confirmation_exchange_15m"),
        "anti_chase_extension_atr": _anti_chase_extension(metrics),
        "deterministic_market_data_veto": {
            "blocked": _record(metrics.get("ai_advisory")).get("deterministic_veto") is True,
            "reason": _record(metrics.get("ai_advisory")).get("deterministic_reason"),
        },
    }


def _terminal_transition_packet(
    previous: dict[str, Any], *, evaluated_at: int, decision: str
) -> dict[str, Any]:
    packet = dict(previous)
    for transient in (
        "event_id",
        "event_at",
        "symbol",
        "previous_decision",
        "ai_advisory",
        "event_persisted",
    ):
        packet.pop(transient, None)
    packet["evaluated_at"] = evaluated_at
    packet["decision"] = decision
    packet["hard_blocked"] = True
    return packet


def build_expired_entry_decision(
    previous_decision: dict[str, Any],
    *,
    evaluated_at: int,
) -> dict[str, Any] | None:
    """Return a durable EXPIRED transition only for an explicit plan expiry."""
    if isinstance(evaluated_at, bool) or not isinstance(evaluated_at, int) or evaluated_at < 0:
        raise ValueError("evaluated_at must be a non-negative integer")
    previous = _record(previous_decision)
    if str(previous.get("decision") or "") not in {"ENTRY_READY", "ACTIVE"}:
        return None
    plan = _record(previous.get("trade_plan"))
    expires_at = plan.get("expires_at")
    if (
        isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or expires_at < 0
        or evaluated_at < expires_at
    ):
        return None

    packet = _terminal_transition_packet(previous, evaluated_at=evaluated_at, decision="EXPIRED")
    packet["block_reasons"] = ["TRADE_PLAN_EXPIRED"]
    packet["trade_plan"] = dict(plan)
    return packet


def build_invalidated_entry_decision(
    previous_decision: dict[str, Any],
    *,
    evaluated_at: int,
    block_reason: str,
) -> dict[str, Any] | None:
    """Return a durable INVALIDATED transition for a prior actionable decision."""
    if isinstance(evaluated_at, bool) or not isinstance(evaluated_at, int) or evaluated_at < 0:
        raise ValueError("evaluated_at must be a non-negative integer")
    reason = str(block_reason or "").strip()
    if not reason:
        raise ValueError("block_reason must be non-empty")
    previous = _record(previous_decision)
    if str(previous.get("decision") or "") not in {"ENTRY_READY", "ACTIVE"}:
        return None

    packet = _terminal_transition_packet(previous, evaluated_at=evaluated_at, decision="INVALIDATED")
    packet["block_reasons"] = [reason]
    packet["reason_codes"] = sorted(
        set([*list(packet.get("reason_codes") or []), reason])
    )
    return packet


def _initial_block_reasons(
    metrics: dict[str, Any],
    status: str,
    *,
    analysis_age_seconds: float | None,
    reference_age_seconds: float | None,
    policy: EntryDecisionPolicy,
) -> tuple[list[str], bool]:
    reasons: list[str] = []
    analysis_age = _finite(analysis_age_seconds)
    reference_age = _finite(reference_age_seconds)
    if (
        analysis_age is None
        or analysis_age < 0
        or analysis_age > policy.max_analysis_age_seconds
    ):
        reasons.append("STALE_ANALYSIS")
    if (
        reference_age is None
        or reference_age < 0
        or reference_age > policy.max_reference_age_seconds
    ):
        reasons.append("STALE_REFERENCE")
    if status == "INVALIDATED":
        reasons.append("STRUCTURE_INVALIDATED")
    if _record(metrics.get("ai_advisory")).get("deterministic_veto") is True:
        reasons.append("DETERMINISTIC_MARKET_DATA_VETO")
    extension = _anti_chase_extension(metrics)
    anti_chase_late = bool(
        policy.gate_anti_chase_enabled
        and extension is not None
        and extension >= policy.anti_chase_hard_block_atr
    )
    if anti_chase_late:
        # Record the measured blocker. Without this the LATE packet carries no
        # evidence of why it was reclassified, and the terminal-origin recovery
        # path that searches for this reason can never fire.
        reasons.append("ANTI_CHASE_HARD_BLOCK")
    return reasons, anti_chase_late


def _record_component(
    components: dict[str, dict[str, float]],
    name: str,
    points: float,
    maximum: float,
) -> None:
    components[name] = {"points": round(points, 2), "maximum": maximum}


def _score_entry_components(
    metrics: dict[str, Any], policy: EntryDecisionPolicy
) -> tuple[dict[str, dict[str, float]], float, float, list[str], bool, float, bool, bool]:
    components: dict[str, dict[str, float]] = {}
    reasons: list[str] = []
    total_points = 0.0
    available_weight = 0.0

    structure, maximum, component_reasons = _structure_points(metrics)
    _record_component(components, "structure", structure, maximum)
    total_points += structure
    available_weight += maximum
    reasons.extend(component_reasons)

    timing, maximum, component_reasons = _timing_points(metrics)
    _record_component(components, "timing", timing, maximum)
    total_points += timing
    available_weight += maximum
    reasons.extend(component_reasons)

    order_flow, maximum, component_reasons, direction_ok = _order_flow_points(metrics)
    _record_component(components, "order_flow", order_flow, maximum)
    total_points += order_flow
    available_weight += maximum
    reasons.extend(component_reasons)

    derivatives, maximum, component_reasons = _derivative_points(metrics)
    _record_component(components, "derivatives", derivatives, maximum)
    total_points += derivatives
    available_weight += maximum
    reasons.extend(component_reasons)

    execution, maximum, component_reasons, execution_ok = _execution_points(metrics, policy)
    _record_component(components, "execution", execution, maximum)
    total_points += execution
    available_weight += maximum
    reasons.extend(component_reasons)

    cross, maximum, component_reasons, cross_ok = _cross_exchange_points(metrics)
    _record_component(components, "cross_exchange", cross, maximum)
    total_points += cross
    available_weight += maximum
    reasons.extend(component_reasons)

    location, maximum, component_reasons = _price_location_points(metrics)
    _record_component(components, "price_location", location, maximum)
    total_points += location
    available_weight += maximum
    reasons.extend(component_reasons)

    cascade, maximum, component_reasons = _cascade_points(metrics)
    _record_component(components, "cascade", cascade, maximum)
    total_points += cascade
    available_weight += maximum
    reasons.extend(component_reasons)
    return components, total_points, available_weight, reasons, direction_ok, timing, execution_ok, cross_ok


def _base_decision(
    *,
    block_reasons: list[str],
    anti_chase_late: bool,
    status: str,
    cascade_status: str = "FAIL",
    readiness: float,
    coverage_pct: float,
    direction_ok: bool,
    timing_ok: bool,
    execution_ok: bool,
    cross_ok: bool,
    trade_plan_ok: bool,
    lifecycle_ok: bool,
    ai_gate_ok: bool,
    policy: EntryDecisionPolicy,
) -> str:
    if status == "EXHAUSTED":
        return "LATE"
    # STRUCTURE_INVALIDATED is always a hard block
    if "STRUCTURE_INVALIDATED" in block_reasons:
        return "INVALIDATED"
    # Cascade is a hard gate unless an operator has explicitly disabled it.
    if policy.gate_cascade_required and cascade_status != "PASS":
        return "NO_TRADE"
    # Fail-closed contract: evidence freshness and execution viability are hard
    # blocks, not scoring penalties. A packet must never advertise an actionable
    # decision while it is simultaneously hard-blocked.
    hard_block_reasons = {
        "DETERMINISTIC_MARKET_DATA_VETO",
        "TRADE_PLAN_EXPIRED",
    }
    if policy.gate_freshness_required:
        hard_block_reasons |= {"STALE_ANALYSIS", "STALE_REFERENCE"}
    if policy.gate_execution_required:
        hard_block_reasons.add("EXECUTION_UNAVAILABLE")
    if hard_block_reasons.intersection(block_reasons):
        return "NO_TRADE"
    # Freshness is settled above, so a surviving extended move is a genuine
    # late entry rather than a stale artefact.
    if anti_chase_late:
        return "LATE"

    gates_pass = (
        readiness >= policy.entry_ready_minimum
        and coverage_pct >= policy.coverage_minimum
        and direction_ok
        and trade_plan_ok
        # Change B: lifecycle veto. PRE-TRIGGER (and any other non-triggered
        # state) can never reach ENTRY_READY/ACTIVE no matter how strong the
        # rest of the evidence looks.
        and lifecycle_ok
        # Change C: AI-neutral cap. A NEUTRAL/missing/stale AI advisory caps
        # the decision unless a deterministic bearish structure gate has
        # explicitly confirmed independently of AI. This can only restrict
        # the decision further; it never upgrades or bypasses any other gate.
        and ai_gate_ok
        and (timing_ok or not policy.gate_timing_required)
        and (execution_ok or not policy.gate_execution_required)
        and (cross_ok or not policy.gate_cross_exchange_required)
    )
    if gates_pass:
        decision = "ACTIVE" if status == "TRIGGERED" else "ENTRY_READY"
    else:
        decision = "FORMING" if readiness >= policy.forming_minimum else "NO_TRADE"
    return decision


def _distinct_lifecycle(
    previous_lifecycle_id: Any,
    lifecycle_id: int | None,
) -> bool:
    return bool(
        isinstance(previous_lifecycle_id, int)
        and not isinstance(previous_lifecycle_id, bool)
        and isinstance(lifecycle_id, int)
        and not isinstance(lifecycle_id, bool)
        and previous_lifecycle_id != lifecycle_id
    )


def _previous_late_origin(previous: dict[str, Any]) -> str | None:
    explicit = str(previous.get("late_origin") or "").upper()
    if explicit in {"ANTI_CHASE", "LIFECYCLE_EXHAUSTED"}:
        return explicit
    if str(previous.get("lifecycle_state") or "").upper() == "EXHAUSTED":
        return "LIFECYCLE_EXHAUSTED"
    if "ANTI_CHASE_HARD_BLOCK" in set(previous.get("block_reasons") or []):
        return "ANTI_CHASE"
    return None


def _legacy_low_readiness_late(
    previous: dict[str, Any],
    *,
    previous_state: str,
    forming_minimum: float,
) -> bool:
    previous_readiness = _finite(previous.get("entry_readiness"))
    return bool(
        previous_state == "LATE"
        and not previous.get("late_origin")
        and str(previous.get("lifecycle_state") or "").upper() != "EXHAUSTED"
        and previous_readiness is not None
        and previous_readiness < forming_minimum
        and set(previous.get("block_reasons") or []) == {"ANTI_CHASE_HARD_BLOCK"}
    )


def _trade_plan_expired(previous: dict[str, Any], evaluated_at: int) -> bool:
    previous_expiry = _record(previous.get("trade_plan")).get("expires_at")
    return bool(
        isinstance(previous_expiry, int)
        and not isinstance(previous_expiry, bool)
        and evaluated_at >= previous_expiry
    )


def _valid_entry_transition(previous_state: str, decision: str) -> bool:
    return bool(
        decision in {"LATE", "INVALIDATED", "EXPIRED"}
        or (previous_state == "ENTRY_READY" and decision in {"ENTRY_READY", "ACTIVE"})
        or (previous_state == "ACTIVE" and decision == "ACTIVE")
    )


def _current_late_origin(
    *,
    decision: str,
    lifecycle_state: str,
    anti_chase_late: bool,
) -> str | None:
    if decision != "LATE":
        return None
    if lifecycle_state == "EXHAUSTED":
        return "LIFECYCLE_EXHAUSTED"
    return "ANTI_CHASE" if anti_chase_late else None



def _retained_terminal_transition(
    previous: dict[str, Any],
    *,
    previous_state: str,
    decision: str,
    block_reasons: list[str],
    late_origin: str | None,
) -> tuple[str, list[str], str | None]:
    current_repeats_terminal = decision == previous_state
    if current_repeats_terminal:
        effective_reasons = block_reasons
    else:
        previous_reasons = previous.get("block_reasons")
        effective_reasons = (
            list(previous_reasons)
            if isinstance(previous_reasons, list)
            else block_reasons
        )

    retained_origin = None
    if previous_state == "LATE":
        if current_repeats_terminal and late_origin is not None:
            retained_origin = late_origin
        else:
            retained_origin = _previous_late_origin(previous)
    return previous_state, effective_reasons, retained_origin

def _apply_previous_transition(
    previous_decision: dict[str, Any] | None,
    *,
    evaluated_at: int,
    decision: str,
    block_reasons: list[str],
    lifecycle_id: int | None,
    forming_minimum: float,
    late_origin: str | None,
) -> tuple[str, list[str], str | None]:
    previous = _record(previous_decision)
    previous_state = str(previous.get("decision") or "")
    distinct_lifecycle = _distinct_lifecycle(previous.get("lifecycle_id"), lifecycle_id)
    recoverable_legacy_late = _legacy_low_readiness_late(
        previous,
        previous_state=previous_state,
        forming_minimum=forming_minimum,
    )
    if (
        previous_state in {"LATE", "INVALIDATED", "EXPIRED"}
        and not distinct_lifecycle
        and not recoverable_legacy_late
        and decision not in {"FORMING", "ENTRY_READY", "ACTIVE"}
    ):
        return _retained_terminal_transition(
            previous,
            previous_state=previous_state,
            decision=decision,
            block_reasons=block_reasons,
            late_origin=late_origin,
        )
    if distinct_lifecycle or previous_state not in {"ENTRY_READY", "ACTIVE"}:
        return decision, block_reasons, late_origin
    if _trade_plan_expired(previous, evaluated_at):
        return "EXPIRED", ["TRADE_PLAN_EXPIRED"], None
    if _valid_entry_transition(previous_state, decision):
        return decision, block_reasons, late_origin
    return "INVALIDATED", [*block_reasons, "ENTRY_CONDITIONS_LOST"], None

def build_entry_decision(
    metrics: dict[str, Any],
    candidate_status: str,
    *,
    evaluated_at: int,
    analysis_age_seconds: float | None,
    reference_age_seconds: float | None,
    policy: EntryDecisionPolicy | None = None,
    lifecycle_id: int | None = None,
    previous_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = policy or EntryDecisionPolicy()
    if lifecycle_id is not None and (
        isinstance(lifecycle_id, bool)
        or not isinstance(lifecycle_id, int)
        or lifecycle_id < 1
    ):
        raise ValueError("lifecycle_id must be a positive integer when provided")
    status = str(candidate_status or "WATCH").upper()
    block_reasons, anti_chase_late = _initial_block_reasons(
        metrics, status, analysis_age_seconds=analysis_age_seconds,
        reference_age_seconds=reference_age_seconds, policy=policy,
    )
    (
        components, total_points, available_weight, reasons, direction_ok, timing,
        execution_ok, cross_ok,
    ) = _score_entry_components(metrics, policy)

    execution_packet = _record(metrics.get("microstructure"))
    execution_inputs_available = bool(
        isinstance(execution_packet.get("approved"), bool)
        and _finite(execution_packet.get("spread_pct")) is not None
        and _finite(execution_packet.get("slippage_pct")) is not None
    )
    if not execution_inputs_available:
        block_reasons.append("EXECUTION_UNAVAILABLE")

    # Change B: lifecycle veto — explicit state check, never derived from
    # readiness/score. Reason is surfaced even when other gates already fail
    # so downstream consumers (notifier, dashboard) can see why a strong
    # score did not translate into an actionable decision.
    lifecycle_ok = _lifecycle_confirmed(status)
    if not lifecycle_ok:
        reasons.append(LIFECYCLE_NOT_TRIGGERED)

    # Change C: AI-neutral cap. Additive only — never removes any other
    # gate's effect, only ever adds a further restriction on top of them.
    # TODO(structure/absorption gate, Change D/E): once candle_analyzer.py
    # exposes the deterministic bearish_15m_structure / support_break_close
    # gate, `_bearish_structure_confirmed` picks it up automatically. If a
    # future absorption gate needs its own independent cap, follow this same
    # pattern (compute a bool, fold into `ai_gate_ok`/a new gate variable,
    # append a dedicated reason code) rather than overloading this one.
    ai_neutral = _ai_neutral_or_unavailable(metrics)
    structure_confirmed = _bearish_structure_confirmed(metrics) if ai_neutral else False
    ai_gate_ok = not (ai_neutral and not structure_confirmed)
    if not ai_gate_ok:
        reasons.append(AI_NEUTRAL_WITHOUT_BEARISH_STRUCTURE)

    readiness = round(_clamp(total_points, 0.0, 100.0), 2)
    coverage_pct = round(_clamp(available_weight, 0.0, 100.0), 2)
    trade_plan = _trade_plan(metrics)
    trade_plan_ok = trade_plan is not None
    if not trade_plan_ok:
        reasons.append("TRADE_PLAN_UNAVAILABLE")
    elif (
        isinstance(trade_plan.get("expires_at"), int)
        and not isinstance(trade_plan.get("expires_at"), bool)
        and evaluated_at >= int(trade_plan["expires_at"])
    ):
        block_reasons.append("TRADE_PLAN_EXPIRED")

    decision = _base_decision(
        block_reasons=block_reasons, anti_chase_late=anti_chase_late,
        status=status,
    cascade_status=str((metrics.get("cascade_intelligence") or {}).get("status", "FAIL")), readiness=readiness,
        coverage_pct=coverage_pct, direction_ok=direction_ok, timing_ok=timing >= 5.0,
        execution_ok=execution_ok, cross_ok=cross_ok, trade_plan_ok=trade_plan_ok,
        lifecycle_ok=lifecycle_ok, ai_gate_ok=ai_gate_ok, policy=policy,
    )
    # Anti-chase no longer hard-blocks; it is a scoring penalty.
    if decision == "ACTIVE":
        previous = _record(previous_decision)
        previous_state = str(previous.get("decision") or "")
        previous_lifecycle_id = previous.get("lifecycle_id")
        lifecycle_matches = (
            previous_lifecycle_id == lifecycle_id
            if lifecycle_id is not None
            else previous_lifecycle_id is None
        )
        if previous_state not in {"ENTRY_READY", "ACTIVE"} or not lifecycle_matches:
            decision = "NO_TRADE"
            block_reasons.append("ENTRY_READY_PREDECESSOR_REQUIRED")
    if decision in {"ENTRY_READY", "ACTIVE"} and not block_reasons:
        reasons.append("ENTRY_GATES_PASS")
    late_origin = _current_late_origin(
        decision=decision, lifecycle_state=status, anti_chase_late=anti_chase_late
    )
    decision, block_reasons, late_origin = _apply_previous_transition(
        previous_decision, evaluated_at=evaluated_at, decision=decision,
        block_reasons=block_reasons, lifecycle_id=lifecycle_id,
        forming_minimum=policy.forming_minimum, late_origin=late_origin,
    )
    packet = {
        "contract_version": "entry_decision_v1",
        "policy_version": policy.version,
        "evaluated_at": int(evaluated_at),
        "decision": decision,
        "lifecycle_state": status,
        "entry_readiness": readiness,
        "evidence_coverage_pct": coverage_pct,
        "hard_blocked": bool(block_reasons),
        "block_reasons": sorted(set(block_reasons)),
        "reason_codes": sorted(set(reasons)),
        "components": components,
        "evidence_summary": _evidence_summary(metrics),
        "trade_plan": trade_plan,
        "policy": asdict(policy),
    }
    leverage_advisory = _leverage_advisory(metrics)
    if leverage_advisory is not None:
        packet["leverage_advisory"] = leverage_advisory
    if lifecycle_id is not None:
        packet["lifecycle_id"] = lifecycle_id
    if decision == "LATE" and late_origin is not None:
        packet["late_origin"] = late_origin
    return packet
