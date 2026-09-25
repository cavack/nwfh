"""Bounded public dashboard projections derived from canonical backend state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_OUTER_FIELDS = (
    "symbol",
    "status",
    "last_price",
    "score",
    "analysis_status",
    "data_status",
    "analysis_observed_at",
    "analysis_age_seconds",
    "reference_observed_at",
    "reference_age_seconds",
    "signal_class",
    "strategy_profile",
    "lifecycle_id",
)
_DECISION_FIELDS = (
    "contract_version",
    "policy_version",
    "evaluated_at",
    "decision",
    "lifecycle_state",
    "entry_readiness",
    "evidence_coverage_pct",
    "trade_plan",
    "block_reasons",
    "reason_codes",
)
_AI_FIELDS = (
    "ai_status",
    "ai_question",
    "ai_advice",
    "ai_answer_yes",
    "ai_confidence",
    "ai_confidence_label",
    "ai_reasoning",
    "ai_provider",
    "ai_model",
    "observational_only",
    "decision_mutated",
)
_FUNDAMENTAL_FIELDS = (
    "fundamental_score",
    "confidence",
    "observational_only",
    "decision_mutated",
    "sources",
    "collected_at",
)
_LEVERAGE_FIELDS = ("status", "leverage", "reason", "policy_version")


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _pick(source: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: deepcopy(source[field]) for field in fields if field in source}


def _evidence_projection(value: Any) -> dict[str, Any]:
    evidence = _record(value)
    derivatives = _record(evidence.get("derivatives"))
    flow = _record(evidence.get("order_flow"))
    execution = _record(evidence.get("execution"))
    cascade = _record(evidence.get("cascade"))
    return {
        "derivatives": _pick(derivatives, ("oi_change_1h_pct", "funding_rate_pct")),
        "order_flow": _pick(flow, ("taker_buy_sell_ratio", "sell_share_pct")),
        "execution": _pick(execution, ("spread_pct",)),
        "cascade": _pick(cascade, ("status", "readiness_points", "maximum_available")),
        "cross_exchange_confirmed": deepcopy(evidence.get("cross_exchange_confirmed")),
        "anti_chase_extension_atr": deepcopy(evidence.get("anti_chase_extension_atr")),
    }


def project_dashboard_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return only canonical fields consumed by the live decision dashboard."""
    projected = _pick(candidate, _OUTER_FIELDS)
    metrics = _record(candidate.get("metrics"))
    decision = _record(metrics.get("entry_decision"))
    decision_projection = _pick(decision, _DECISION_FIELDS)
    decision_projection["evidence_summary"] = _evidence_projection(
        decision.get("evidence_summary")
    )
    policy = _record(decision.get("policy"))
    # The dashboard must render thresholds from the policy that produced the
    # decision, not from constants compiled into the frontend. Shipping only
    # the freshness pair left the UI guessing the anti-chase boundary, and it
    # guessed 1.2 ATR while the engine used 2.5.
    decision_projection["policy"] = _pick(
        policy,
        (
            "max_analysis_age_seconds",
            "max_reference_age_seconds",
            "anti_chase_hard_block_atr",
            "entry_ready_minimum",
            "forming_minimum",
        ),
    )
    decision_leverage = _record(decision.get("leverage_advisory"))
    if decision_leverage:
        decision_projection["leverage_advisory"] = _pick(
            decision_leverage, _LEVERAGE_FIELDS
        )
    live_leverage = _record(metrics.get("leverage_advisory"))
    advisory = _record(metrics.get("ai_advisory"))
    technical_shadow = _record(metrics.get("technical_trade_plan_shadow"))
    projected["metrics"] = {
        "entry_decision": decision_projection,
        "applied_leverage": deepcopy(metrics.get("applied_leverage")),
        "leverage_advisory": _pick(live_leverage, _LEVERAGE_FIELDS),
        "ai_advisory": _pick(advisory, _AI_FIELDS),
    }
    fundamental = _record(metrics.get("fundamental_observational"))
    if fundamental:
        projected["metrics"]["fundamental_observational"] = _pick(
            fundamental, _FUNDAMENTAL_FIELDS
        )
    if technical_shadow:
        shadow_projection = _pick(
            technical_shadow,
            ("version", "observational_only", "hard_gating_allowed", "available", "feasible", "status"),
        )
        shadow_projection["setup"] = _pick(
            _record(technical_shadow.get("setup")),
            ("status", "entry_price", "stop_loss", "take_profit_1", "take_profit_2", "take_profit_3", "reward_to_risk"),
        )
        shadow_projection["reference"] = _pick(
            _record(technical_shadow.get("reference")), ("price", "source")
        )
        projected["metrics"]["technical_trade_plan_shadow"] = shadow_projection
    analysis_reason = metrics.get("analysis_reason") or metrics.get("error")
    if isinstance(analysis_reason, str) and analysis_reason:
        projected["metrics"]["analysis_reason"] = analysis_reason
    return projected


def project_dashboard_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep canonical terminal/ranking outputs while bounding candidate payload size."""
    candidates = _record(payload.get("candidates"))
    return {
        **payload,
        "candidates": {
            str(symbol): project_dashboard_candidate(candidate)
            for symbol, candidate in candidates.items()
            if isinstance(candidate, dict)
        },
    }
