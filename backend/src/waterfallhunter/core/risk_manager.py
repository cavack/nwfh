import copy
import logging
import math
from typing import Dict, Any, Tuple

logger = logging.getLogger("WaterfallHunter.RiskManager")

def get_leverage(symbol: str) -> int:
    """Legacy symbol-only leverage retained for deterministic replay compatibility."""
    base = symbol.split("/")[0].upper()
    return 2 if base in {"BTC", "ETH"} else 3


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


class LeverageUnavailableError(ValueError):
    """Required causal inputs are unavailable for an adaptive leverage advisory."""


class LeverageNotRecommendedError(ValueError):
    """Complete inputs imply that leverage of at least 4x is not recommended."""


LEVERAGE_POLICY_VERSION = "adaptive_signal_leverage_v3"

# Leverage is bounded by the canonical entry readiness, not the retired
# Gen-1 ``metrics["score"]``. v2 read ``score`` (None on every live packet
# since the ScoreV2 merge stopped populating it) and rejected 1,641 of 1,646
# ENTRY_READY packets with "strict finite score required". The score bound
# was also calibrated for the old TRIGGERED>=85 scale; readiness>=78 occurred
# once in 276k evaluations, so the ramp is re-anchored to the band that
# actually produces actionable decisions.
LEVERAGE_MIN = 4
LEVERAGE_MAX = 18
READINESS_FLOOR = 72.0  # EntryDecisionPolicy.entry_ready_minimum
READINESS_CEILING = 95.0


def _readiness_bound(readiness: float) -> int:
    """Map readiness in [floor, ceiling] linearly onto [4, 18]."""
    span = READINESS_CEILING - READINESS_FLOOR
    ratio = (readiness - READINESS_FLOOR) / span if span > 0 else 0.0
    ratio = max(0.0, min(1.0, ratio))
    return int(math.floor(LEVERAGE_MIN + ratio * (LEVERAGE_MAX - LEVERAGE_MIN)))


def _canonical_readiness(metrics: Dict[str, Any]) -> float | None:
    decision = metrics.get("entry_decision")
    if isinstance(decision, dict):
        value = _finite_number(decision.get("entry_readiness"))
        if value is not None:
            return value
    return _finite_number(metrics.get("entry_readiness"))


def _normalized_leverage_causal_input(
    metrics: Dict[str, Any],
    execution_suitability: Dict[str, Any] | None,
) -> dict[str, Any]:
    source = metrics if isinstance(metrics, dict) else {}
    position = source.get("position_setup") if isinstance(source.get("position_setup"), dict) else {}
    micro = source.get("microstructure") if isinstance(source.get("microstructure"), dict) else {}
    features = source.get("candle_features") if isinstance(source.get("candle_features"), dict) else {}
    constraints = source.get("market_constraints") if isinstance(source.get("market_constraints"), dict) else {}
    suitability = execution_suitability if isinstance(execution_suitability, dict) else {}

    atr_by_timeframe: dict[str, float | None] = {}
    for timeframe in ("5m", "15m", "1h"):
        packet = features.get(timeframe) if isinstance(features.get(timeframe), dict) else {}
        atr_by_timeframe[timeframe] = _finite_number(packet.get("atr_pct"))

    available = suitability.get("available")
    return {
        "entry_readiness": _canonical_readiness(source),
        "position_setup": {
            "status": str(position.get("status") or "").upper(),
            "entry_price": _finite_number(position.get("entry_price")),
            "stop_loss": _finite_number(position.get("stop_loss")),
        },
        "microstructure": {
            "spread_pct": _finite_number(micro.get("spread_pct")),
            "slippage_pct": _finite_number(micro.get("slippage_pct")),
            "exit_slippage_present": "exit_slippage_pct" in micro,
            "exit_slippage_pct": _finite_number(micro.get("exit_slippage_pct")),
        },
        "candle_atr_pct": atr_by_timeframe,
        "market_constraints": {
            "maximum_leverage": _finite_number(constraints.get("maximum_leverage")),
        },
        "execution_suitability": {
            "available": available if isinstance(available, bool) else None,
            "status": str(suitability.get("status") or "UNKNOWN").upper(),
            "maximum_leverage": _finite_number(suitability.get("maximum_leverage")),
        },
    }


def recommend_signal_leverage(
    metrics: Dict[str, Any],
    execution_suitability: Dict[str, Any] | None = None,
) -> int:
    """Return an evidence-bound SIGNAL_ONLY leverage recommendation from 4x to 18x.

    The recommendation is the minimum of independent score, structural-stop,
    volatility, execution-friction, and execution-suitability ceilings. It is
    intentionally symbol-agnostic. A bound below 4x means leveraged exposure is
    not recommended; it is not a canonical signal/lifecycle gate.
    """
    if not isinstance(metrics, dict):
        raise LeverageUnavailableError("signal metrics unavailable for leverage")

    readiness = _canonical_readiness(metrics)
    position = metrics.get("position_setup") if isinstance(metrics.get("position_setup"), dict) else {}
    entry = _finite_number(position.get("entry_price"))
    stop = _finite_number(position.get("stop_loss"))
    micro = metrics.get("microstructure") if isinstance(metrics.get("microstructure"), dict) else {}
    spread = _finite_number(micro.get("spread_pct"))
    slippage = _finite_number(micro.get("slippage_pct"))
    has_exit_slippage = "exit_slippage_pct" in micro
    raw_exit_slippage = micro.get("exit_slippage_pct")
    exit_slippage = _finite_number(raw_exit_slippage)

    if readiness is None or readiness < 0.0 or readiness > 100.0:
        raise LeverageUnavailableError("canonical entry readiness required for leverage")
    if entry is None or stop is None or entry <= 0 or stop <= entry:
        raise LeverageUnavailableError("valid short entry and structural stop required for leverage")
    if (
        spread is None
        or slippage is None
        or spread < 0
        or slippage < 0
        or (
            has_exit_slippage
            and (exit_slippage is None or exit_slippage < 0)
        )
    ):
        raise LeverageUnavailableError("finite execution friction required for leverage")

    features = metrics.get("candle_features") if isinstance(metrics.get("candle_features"), dict) else {}
    atr_values = []
    for timeframe in ("5m", "15m", "1h"):
        packet = features.get(timeframe) if isinstance(features.get(timeframe), dict) else {}
        value = _finite_number(packet.get("atr_pct"))
        if value is not None and value > 0:
            atr_values.append(value)
    if not atr_values:
        raise LeverageUnavailableError("finite ATR evidence required for leverage")

    stop_distance_pct = (stop - entry) / entry * 100.0
    atr_pct = max(atr_values)
    friction_pct = max(spread, slippage, exit_slippage or slippage)

    readiness_bound = _readiness_bound(readiness)
    stop_bound = math.floor(36.0 / stop_distance_pct)
    volatility_bound = math.floor(18.0 / (1.0 + max(atr_pct - 0.5, 0.0) / 2.5))

    if friction_pct <= 0.05:
        execution_bound = 18
    elif friction_pct <= 0.10:
        execution_bound = 15
    elif friction_pct <= 0.15:
        execution_bound = 12
    elif friction_pct <= 0.22:
        execution_bound = 9
    elif friction_pct <= 0.30:
        execution_bound = 6
    else:
        execution_bound = 3

    suitability = execution_suitability if isinstance(execution_suitability, dict) else {}
    suitability_status = str(suitability.get("status") or "UNKNOWN").upper()
    if suitability.get("available") is False or suitability_status == "UNKNOWN":
        raise LeverageUnavailableError("execution suitability evidence unavailable for leverage")
    suitability_bounds = {"SUITABLE": 18, "MARGINAL": 10, "POOR": 4}
    if suitability_status not in suitability_bounds:
        raise LeverageUnavailableError("execution suitability status invalid for leverage")
    suitability_bound = suitability_bounds[suitability_status]

    position_status = str(position.get("status") or "").upper()
    if position_status.startswith("REJECTED"):
        raise LeverageNotRecommendedError("position setup rejected by execution constraints")
    if readiness < READINESS_FLOOR:
        raise LeverageNotRecommendedError(
            f"entry readiness {readiness:.1f} is below the actionable floor {READINESS_FLOOR:.0f}"
        )

    constraints = (
        metrics.get("market_constraints")
        if isinstance(metrics.get("market_constraints"), dict)
        else {}
    )
    exchange_max = _finite_number(constraints.get("maximum_leverage"))
    if exchange_max is None:
        exchange_max = _finite_number(suitability.get("maximum_leverage"))
    exchange_bound = math.floor(exchange_max) if exchange_max is not None and exchange_max > 0 else 18

    raw = min(LEVERAGE_MAX, readiness_bound, stop_bound, volatility_bound, execution_bound, suitability_bound, exchange_bound)
    if raw < LEVERAGE_MIN:
        raise LeverageNotRecommendedError("independent risk bound requires leverage below 4x")
    return int(raw)


def build_signal_leverage_advisory(
    metrics: Dict[str, Any],
    execution_suitability: Dict[str, Any] | None = None,
    *,
    decision_status: str | None = None,
) -> dict[str, Any]:
    """Return the canonical live leverage advisory without fabricating a fallback."""
    execution_input = (
        copy.deepcopy(execution_suitability)
        if isinstance(execution_suitability, dict)
        else {}
    )
    normalized_decision = str(decision_status or "").upper() or None
    base = {
        "policy_version": LEVERAGE_POLICY_VERSION,
        "minimum": LEVERAGE_MIN,
        "maximum": LEVERAGE_MAX,
        "margin_mode": "isolated",
        "symbol_agnostic": True,
        "signal_only": True,
        "advisory_only": True,
        "decision_status": normalized_decision,
        "execution_suitability_input": execution_input,
        "causal_input": _normalized_leverage_causal_input(metrics, execution_suitability),
    }
    if normalized_decision in {"FORMING", "LATE", "NO_TRADE", "INVALIDATED", "EXPIRED"}:
        return {
            **base,
            "status": "NOT_RECOMMENDED",
            "leverage": None,
            "reason": f"canonical entry decision is not actionable: {normalized_decision}",
        }
    try:
        leverage = recommend_signal_leverage(metrics, execution_suitability)
    except LeverageNotRecommendedError as exc:
        return {**base, "status": "NOT_RECOMMENDED", "leverage": None, "reason": str(exc)}
    except LeverageUnavailableError as exc:
        return {**base, "status": "UNAVAILABLE", "leverage": None, "reason": str(exc)}
    except Exception as exc:
        logger.warning("Adaptive leverage advisory unavailable: %s", exc)
        return {
            **base,
            "status": "UNAVAILABLE",
            "leverage": None,
            "reason": "adaptive leverage calculation unavailable",
            "error_type": type(exc).__name__,
        }
    return {**base, "status": "AVAILABLE", "leverage": leverage, "reason": None}

class LiquidityRiskManager:
    def __init__(self):
        # تنظیمات بر اساس مستندات Production
        self.notional_trade_size_usdt = 100.0  # حجم فرضی معامله برای محاسبه Slippage
        self.max_allowed_spread_pct = 0.5      # حداکثر اسپرد مجاز: نیم درصد
        self.max_allowed_slippage_pct = 0.3    # حداکثر لغزش قیمت برای ورود: ۰.۳ درصد

    def analyze_orderbook_liquidity(self, orderbook: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        بررسی اسپرد، محاسبه VWAP برای یک حجم خاص و تخمین لغزش قیمت (Slippage)
        برمی‌گرداند: (آیا تایید شد؟, جزئیات محاسبات)
        """
        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])

        # اگر اوردربوک خالی باشد، رد می‌شود
        if not bids or not asks:
            return False, {"error": "Empty orderbook"}

        best_bid = bids[0][0]
        best_ask = asks[0][0]

        # 1. محاسبه Spread
        spread = best_ask - best_bid
        spread_pct = (spread / best_ask) * 100

        # اگر اسپرد از حد مجاز بیشتر بود، فوراً رد می‌شود
        if spread_pct > self.max_allowed_spread_pct:
            return False, {
                "rejected_reason": "high_spread",
                "spread_pct": round(spread_pct, 4)
            }

        # 2. محاسبه VWAP سمت Bid (چون ما می‌خواهیم SHORT کنیم، مارکت ما روی بیدها پر می‌شود)
        # هدف: فروختن حجم self.notional_trade_size_usdt به مارکت
        remaining_usdt_to_sell = self.notional_trade_size_usdt
        total_coins_sold = 0.0
        weighted_sum = 0.0

        for price, amount_coins in bids:
            if remaining_usdt_to_sell <= 0:
                break

            level_usdt_capacity = price * amount_coins

            if level_usdt_capacity >= remaining_usdt_to_sell:
                # این سطح می‌تواند تمام حجم باقی‌مانده ما را پر کند
                coins_to_sell_here = remaining_usdt_to_sell / price
                weighted_sum += price * coins_to_sell_here
                total_coins_sold += coins_to_sell_here
                remaining_usdt_to_sell = 0
            else:
                # این سطح تمام حجم را مصرف می‌کند اما هنوز باید پایین‌تر برویم
                weighted_sum += price * amount_coins
                total_coins_sold += amount_coins
                remaining_usdt_to_sell -= level_usdt_capacity

        # اگر تمام اوردربوک (۲۰ لول) را گشتیم و هنوز حجم ۱۰۰ دلار ما پر نشد:
        if remaining_usdt_to_sell > 0:
            return False, {"rejected_reason": "insufficient_liquidity", "spread_pct": round(spread_pct, 4)}

        # محاسبه نهایی میانگین قیمت (VWAP)
        vwap_execution_price = weighted_sum / total_coins_sold

        # 3. محاسبه لغزش (Slippage)
        # لغزش یعنی چقدر قیمت واقعیِ پر شدنِ ما، از بهترین قیمتِ روی تابلو (Best Bid) بدتر است
        slippage_pct = ((best_bid - vwap_execution_price) / best_bid) * 100

        is_approved = slippage_pct <= self.max_allowed_slippage_pct

        details = {
            "spread_pct": round(spread_pct, 4),
            "estimated_vwap": vwap_execution_price,
            "estimated_slippage_pct": round(slippage_pct, 4),
            "notional_size_usdt": self.notional_trade_size_usdt,
            "liquidity_approved": is_approved
        }

        if not is_approved:
            details["rejected_reason"] = "high_slippage"

        return is_approved, details
