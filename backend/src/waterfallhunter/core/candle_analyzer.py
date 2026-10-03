import asyncio
import copy
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from waterfallhunter.core.channel_strategy import channel_stages


class MultiTimeframeAnalyzer:
    timeframes = ("5m", "15m", "1h", "4h")
    timeframe_ms = {
        "5m": 300_000,
        "15m": 900_000,
        "1h": 3_600_000,
        "4h": 14_400_000,
    }
    candle_limit = 120
    max_closed_candle_age_intervals = 2
    cache_max_entries = 1024

    def __init__(self) -> None:
        self._closed_series_cache: OrderedDict[tuple[str, str, str, int], dict[str, Any]] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_evictions = 0

    @staticmethod
    def _exchange_cache_id(exchange: Any) -> str:
        exchange_id = getattr(exchange, "id", None)
        if isinstance(exchange_id, str) and exchange_id:
            return exchange_id
        return f"{type(exchange).__module__}.{type(exchange).__qualname__}:{id(exchange)}"

    def _expected_closed_start(self, timeframe: str, *, now_ms: int) -> int:
        gap = self.timeframe_ms[timeframe]
        return (int(now_ms) // gap) * gap - gap

    def cache_diagnostics(self) -> Dict[str, int]:
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "evictions": self._cache_evictions,
            "entries": len(self._closed_series_cache),
        }

    async def _load_closed_series(
        self,
        exchange: Any,
        symbol: str,
        timeframe: str,
        *,
        now_ms: int | None = None,
    ) -> Optional[List[List[float]]]:
        observed_now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        expected_closed_start = self._expected_closed_start(
            timeframe, now_ms=observed_now_ms
        )
        key = (
            self._exchange_cache_id(exchange),
            symbol,
            timeframe,
            self.candle_limit,
        )
        cached = self._closed_series_cache.get(key)
        if (
            isinstance(cached, dict)
            and cached.get("expected_closed_start") == expected_closed_start
        ):
            self._cache_hits += 1
            self._closed_series_cache.move_to_end(key)
            return copy.deepcopy(cached["candles"])

        self._cache_misses += 1
        rows = await exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, limit=self.candle_limit
        )
        candles = self._closed_candles(rows, timeframe, now_ms=observed_now_ms)
        if candles is not None and candles[-1][0] >= expected_closed_start:
            self._closed_series_cache[key] = {
                "expected_closed_start": expected_closed_start,
                "candles": copy.deepcopy(candles),
            }
            self._closed_series_cache.move_to_end(key)
            while len(self._closed_series_cache) > max(1, int(self.cache_max_entries)):
                self._closed_series_cache.popitem(last=False)
                self._cache_evictions += 1
        return candles

    def _closed_candles(
        self,
        rows: List[List[float]],
        timeframe: str,
        *,
        now_ms: int | None = None,
    ) -> Optional[List[List[float]]]:
        if not isinstance(rows, list) or len(rows) < 20:
            return None

        gap = self.timeframe_ms[timeframe]
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        candles = []

        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                return None

            try:
                ts, opening, high, low, close, volume = (
                    float(value) for value in row[:6]
                )
            except (TypeError, ValueError):
                return None

            if (
                ts <= 0
                or min(opening, high, low, close) <= 0
                or volume < 0
                or high < max(opening, close)
                or low > min(opening, close)
            ):
                return None

            if now < ts + gap:
                if row is not rows[-1]:
                    return None
                continue

            candles.append(
                [int(ts), opening, high, low, close, volume]
            )

        if len(candles) < 20:
            return None

        for previous, current in zip(candles, candles[1:]):
            if current[0] - previous[0] != gap:
                return None

        if (
            now - (candles[-1][0] + gap)
            > gap * self.max_closed_candle_age_intervals
        ):
            return None

        return candles

    @staticmethod
    def _atr(
        candles: List[List[float]],
        period: int = 14,
    ) -> Optional[float]:
        if len(candles) < period + 1:
            return None

        true_ranges: List[float] = []
        for previous, current in zip(
            candles[-period - 1 : -1],
            candles[-period:],
        ):
            previous_close = float(previous[4])
            high = float(current[2])
            low = float(current[3])
            true_ranges.append(
                max(
                    high - low,
                    abs(high - previous_close),
                    abs(low - previous_close),
                )
            )

        if not true_ranges:
            return None
        atr = sum(true_ranges) / len(true_ranges)
        return atr if atr > 0 else None

    @staticmethod
    def _return_pct(
        candles: List[List[float]],
        bars: int,
    ) -> Optional[float]:
        if bars <= 0 or len(candles) < bars + 1:
            return None
        earlier_close = float(candles[-bars - 1][4])
        latest_close = float(candles[-1][4])
        if earlier_close <= 0:
            return None
        return (latest_close / earlier_close - 1.0) * 100.0

    @staticmethod
    def _rsi(
        closes: List[float],
        period: int = 14,
    ) -> Optional[float]:
        if len(closes) < period + 1:
            return None

        gains = 0.0
        losses = 0.0
        for earlier, later in zip(
            closes[-period - 1 : -1],
            closes[-period:],
        ):
            change = later - earlier
            gains += max(change, 0.0)
            losses += max(-change, 0.0)

        if losses == 0:
            return 100.0 if gains else 50.0
        rs = (gains / period) / (losses / period)
        return 100 - (100 / (1 + rs))

    @classmethod
    def _precrash_observations(
        cls,
        candles: List[List[float]],
        *,
        return_3bars_pct: float | None,
    ) -> Dict[str, Any]:
        peak_index = max(
            range(len(candles)),
            key=lambda index: float(candles[index][2]),
        )
        peak = candles[peak_index]
        bars_since_peak = len(candles) - 1 - peak_index

        baseline_rows = candles[-23:-3]
        baseline_volume = (
            sum(float(row[5]) for row in baseline_rows) / len(baseline_rows)
            if baseline_rows
            else None
        )
        volume_ratio = (
            float(candles[-1][5]) / baseline_volume
            if baseline_volume is not None and baseline_volume > 0
            else None
        )

        recent_ranges = [float(row[2]) - float(row[3]) for row in candles[-3:]]
        baseline_ranges = [float(row[2]) - float(row[3]) for row in baseline_rows]
        recent_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else None
        baseline_range = (
            sum(baseline_ranges) / len(baseline_ranges)
            if baseline_ranges
            else None
        )
        volatility_expansion = (
            recent_range / baseline_range
            if recent_range is not None
            and baseline_range is not None
            and baseline_range > 0
            else None
        )

        return {
            "contract_version": "precrash_candle_observation_v1",
            "observational_only": True,
            "hard_gating_allowed": False,
            "promotion_allowed": False,
            "peak_at": int(peak[0]),
            "peak_price": round(float(peak[2]), 10),
            "bars_since_peak": bars_since_peak,
            "price_return_3bars_pct": (
                round(return_3bars_pct, 4)
                if return_3bars_pct is not None
                else None
            ),
            "volume_ratio_to_baseline": (
                round(volume_ratio, 4)
                if volume_ratio is not None
                else None
            ),
            "volatility_expansion_ratio": (
                round(volatility_expansion, 4)
                if volatility_expansion is not None
                else None
            ),
        }

    def _evaluate(
        self,
        candles: List[List[float]],
    ) -> Dict[str, Any]:
        previous, reclaim_bar, latest = candles[-3:]
        closes = [row[4] for row in candles]
        prior_rsi = self._rsi(closes[:-1])
        current_rsi = self._rsi(closes)
        baseline_volume = sum(row[5] for row in candles[-13:-3]) / 10

        two_closed = (
            reclaim_bar[4] < reclaim_bar[1]
            and latest[4] < latest[1]
        )
        lower_high = latest[2] < reclaim_bar[2]
        volume_acceleration = (
            latest[5] > baseline_volume
            and latest[5] > reclaim_bar[5]
        )
        rsi_rollover = (
            prior_rsi is not None
            and current_rsi is not None
            and prior_rsi > current_rsi
            and current_rsi <= 55
        )
        reclaim = (
            previous[4] < candles[-4][3]
            and reclaim_bar[2] >= candles[-4][3]
            and latest[4] < candles[-4][3]
        )
        repump = (
            reclaim_bar[2] > previous[2]
            and reclaim_bar[4] > previous[4]
            and latest[4] < reclaim_bar[4]
        )
        bearish_close = latest[4] < latest[1]
        support = min(row[3] for row in candles[-23:-3])
        atr_14 = self._atr(candles, period=14)
        latest_close = float(latest[4])
        recent_high = max(float(row[2]) for row in candles[-23:])
        distance_to_support = latest_close - support
        distance_to_support_pct = (
            distance_to_support / latest_close * 100.0
            if latest_close > 0
            else None
        )
        distance_to_support_atr = (
            distance_to_support / atr_14
            if atr_14 is not None and atr_14 > 0
            else None
        )
        atr_pct = (
            atr_14 / latest_close * 100.0
            if atr_14 is not None and latest_close > 0
            else None
        )
        distance_from_recent_high_pct = (
            (recent_high - latest_close) / recent_high * 100.0
            if recent_high > 0
            else None
        )
        extension_from_support_atr = (
            abs(distance_to_support) / atr_14
            if atr_14 is not None and atr_14 > 0
            else None
        )
        return_3bars_pct = self._return_pct(candles, 3)
        return_6bars_pct = self._return_pct(candles, 6)
        return_12bars_pct = self._return_pct(candles, 12)

        support_broken = previous[4] < support and latest[4] < support
        failed_pullback = (
            support_broken
            and reclaim_bar[2] >= support
            and reclaim_bar[4] < support
            and lower_high
        )
        strong_breakdown = support_broken and volume_acceleration and bearish_close
        continuation = latest[3] < reclaim_bar[3] and lower_high and bearish_close

        if failed_pullback:
            setup = "FAILED_PULLBACK"
        elif strong_breakdown:
            setup = "BREAKDOWN"
        elif continuation:
            setup = "CONTINUATION"
        else:
            setup = None

        regime_bearish = support_broken and lower_high
        trigger_ready = (
            two_closed
            and lower_high
            and volume_acceleration
            and bearish_close
            and (reclaim or repump)
        )
        is_bearish = all(
            (
                two_closed,
                lower_high,
                volume_acceleration,
                rsi_rollover,
                bearish_close,
                reclaim or repump,
            )
        )

        pre_pump_base = (
            min(row[3] for row in candles[-100:-40])
            if len(candles) >= 100
            else None
        )
        pump_peak = max(row[2] for row in candles[-80:-3])
        pump_pct = (
            (pump_peak / pre_pump_base - 1.0) * 100.0
            if pre_pump_base
            else None
        )
        pre_pump_volume = (
            sum(row[5] for row in candles[-100:-40]) / 60
            if len(candles) >= 100
            else None
        )
        pump_volume = max(row[5] for row in candles[-80:-3])
        volume_climax = bool(
            pre_pump_volume
            and pump_volume >= pre_pump_volume * 1.8
        )

        return {
            "valid": True,
            "two_closed_candles": two_closed,
            "reclaim": reclaim,
            "repump": repump,
            "rsi_rollover": rsi_rollover,
            "rsi": round(current_rsi, 2) if current_rsi is not None else None,
            "lower_high": lower_high,
            "volume_acceleration": volume_acceleration,
            "is_bearish": is_bearish,
            "dynamic_support": support,
            "support_broken": support_broken,
            "setup": setup,
            "regime_bearish": regime_bearish,
            "trigger_ready": trigger_ready,
            "bearish_close": bearish_close,
            "pump_pct": round(pump_pct, 4) if pump_pct is not None else None,
            "volume_climax": volume_climax,
            "atr_14": round(atr_14, 10) if atr_14 is not None else None,
            "atr_pct": round(atr_pct, 4) if atr_pct is not None else None,
            "distance_to_support_pct": (
                round(distance_to_support_pct, 4)
                if distance_to_support_pct is not None
                else None
            ),
            "distance_to_support_atr": (
                round(distance_to_support_atr, 4)
                if distance_to_support_atr is not None
                else None
            ),
            "distance_from_recent_high_pct": (
                round(distance_from_recent_high_pct, 4)
                if distance_from_recent_high_pct is not None
                else None
            ),
            "extension_from_support_atr": (
                round(extension_from_support_atr, 4)
                if extension_from_support_atr is not None
                else None
            ),
            "return_3bars_pct": (
                round(return_3bars_pct, 4)
                if return_3bars_pct is not None
                else None
            ),
            "return_6bars_pct": (
                round(return_6bars_pct, 4)
                if return_6bars_pct is not None
                else None
            ),
            "return_12bars_pct": (
                round(return_12bars_pct, 4)
                if return_12bars_pct is not None
                else None
            ),
            "hype_context": bool(
                pump_pct is not None
                and pump_pct >= 20.0
                and volume_climax
            ),
            "precrash_observations": self._precrash_observations(
                candles,
                return_3bars_pct=return_3bars_pct,
            ),
        }

    @staticmethod
    def channel_stages(
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        required = ("5m", "15m", "1h", "4h")
        if not all(details.get(timeframe, {}).get("valid") for timeframe in required):
            return {
                "hype": False,
                "damage": False,
                "setup": False,
                "setup_type": None,
                "trigger": False,
                "passed": False,
            }

        checks = {
            timeframe: {
                "hype_context": details[timeframe].get("hype_context", False),
                "support_broken": details[timeframe].get("support_broken", False),
                "failed_pullback": details[timeframe].get("setup") == "FAILED_PULLBACK",
                "flags": {
                    "two_bearish": details[timeframe].get("two_closed_candles", False),
                    "lower_high": details[timeframe].get("lower_high", False),
                    "bearish_close": details[timeframe].get("bearish_close", False),
                    "volume_acceleration": details[timeframe].get("volume_acceleration", False),
                },
            }
            for timeframe in required
        }

        stages = channel_stages(checks)
        stages["passed"] = bool(
            stages["hype"]
            and stages["damage"]
            and stages["setup"]
            and stages["trigger"]
        )
        return stages

    async def analyze_hype_context(
        self,
        exchange: Any,
        symbol: str,
    ) -> Dict[str, Any]:
        """Evaluate only the closed 4h series for observational WATCH hype.

        This is deliberately not a promotion or trade gate. Missing, stale,
        gapped, or provider-failed OHLCV is inconclusive and callers must fall
        back to the canonical full validator.
        """
        observed_now_ms = int(time.time() * 1000)
        try:
            candles = await self._load_closed_series(
                exchange, symbol, "4h", now_ms=observed_now_ms
            )
        except Exception as exc:
            return {
                "valid": False,
                "hype_context": False,
                "reason": f"4h OHLCV unavailable: {type(exc).__name__}",
                "details": None,
                "source_capture": {},
            }
        if not candles:
            return {
                "valid": False,
                "hype_context": False,
                "reason": "missing, open, duplicate, gapped, stale, or invalid 4h OHLCV",
                "details": None,
                "source_capture": {},
            }
        details = self._evaluate(candles)
        if not isinstance(details, dict) or details.get("valid") is not True:
            return {
                "valid": False,
                "hype_context": False,
                "reason": "invalid 4h candle evaluation",
                "details": details if isinstance(details, dict) else None,
                "source_capture": {},
            }
        return {
            "valid": True,
            "hype_context": bool(details.get("hype_context")),
            "details": details,
            "source_capture": {
                "primary_closed_ohlcv": {"4h": candles},
                "raw_ohlcv_captured": False,
                "confirmation_ohlcv_captured": False,
            },
        }

    async def analyze_candles(
        self,
        exchange: Any,
        symbol: str,
        confirmation_exchange: Any = None,
        confirmation_symbol: str | None = None,
    ) -> Dict[str, Any]:
        observed_now_ms = int(time.time() * 1000)
        primary = await asyncio.gather(
            *(
                self._load_closed_series(
                    exchange, symbol, tf, now_ms=observed_now_ms
                )
                for tf in self.timeframes
            ),
            return_exceptions=True,
        )

        source_ohlcv: Dict[str, List[List[float]]] = {}
        for tf, candles in zip(self.timeframes, primary):
            if isinstance(candles, Exception) or candles is None:
                continue
            source_ohlcv[tf] = candles

        confirmation_ohlcv = None
        if confirmation_exchange and confirmation_symbol:
            try:
                confirmation_ohlcv = await self._load_closed_series(
                    confirmation_exchange,
                    confirmation_symbol,
                    "15m",
                    now_ms=observed_now_ms,
                )
            except Exception:
                confirmation_ohlcv = None

        return self.evaluate_closed_sources(source_ohlcv, confirmation_ohlcv)

    def evaluate_closed_sources(
        self,
        source_ohlcv: Dict[str, List[List[float]]],
        confirmation_ohlcv: List[List[float]] | None,
    ) -> Dict[str, Any]:
        details: Dict[str, Any] = {}
        confirmed = 0
        for timeframe in self.timeframes:
            candles = source_ohlcv.get(timeframe)
            if not candles:
                details[timeframe] = {
                    "valid": False,
                    "reason": "missing, open, duplicate, gapped, or invalid OHLCV",
                }
                continue
            result = self._evaluate(candles)
            details[timeframe] = result
            confirmed += int(result["is_bearish"])

        confirmation = self._evaluate(confirmation_ohlcv) if confirmation_ohlcv else None
        cross_exchange = bool(
            confirmation
            and all(
                confirmation[name]
                for name in ("two_closed_candles", "lower_high", "bearish_close")
            )
        )
        return {
            "is_breakdown_confirmed": confirmed >= 2 and cross_exchange,
            "breakdown_score": confirmed,
            "cross_exchange_confirmed": cross_exchange,
            "details": details,
            "source_capture": {
                "primary_closed_ohlcv": source_ohlcv,
                "confirmation_closed_ohlcv_15m": confirmation_ohlcv,
                "raw_ohlcv_captured": bool(
                    len(source_ohlcv) == len(self.timeframes)
                    and all(source_ohlcv.get(tf) for tf in self.timeframes)
                ),
                "confirmation_ohlcv_captured": bool(confirmation_ohlcv),
            },
        }


# ---------------------------------------------------------------------------
# Deterministic 15-minute bearish structure gate (Change D) and absorption
# detection (Change E).
#
# These are standalone, pure, deterministic functions intended to be
# imported and consumed by entry_decision.py's short-entry gate. They are
# NOT wired into entry_decision.py here -- that wiring is done separately.
# They only ever look at already-CLOSED candles (no open/incomplete candle,
# no repainting, no look-ahead) and fail closed (available=False) whenever
# the input data is insufficient, gapped, or otherwise untrustworthy.
# ---------------------------------------------------------------------------

_STRUCTURE_TIMEFRAME_MS_15M = 900_000
_STRUCTURE_MIN_CANDLES = 25
_STRUCTURE_PIVOT_WING = 2
# Candles within this trailing window are reserved for break/retest
# confirmation and are never themselves eligible as pivot-low
# candidates -- otherwise a breakdown candle could masquerade as a
# new "higher low" and mask its own break.
_STRUCTURE_RESERVED_TAIL = 10


def _bearish_structure_unavailable(reason_codes: List[str]) -> Dict[str, Any]:
    """Fail-closed result shared by all early-exit paths below."""
    return {
        "available": False,
        "bearish_15m_structure": False,
        "support_break_close": False,
        "retest_rejected": None,
        "last_higher_low": None,
        "broken_support": None,
        "reason_codes": list(reason_codes),
    }


def evaluate_bearish_structure(candles_15m: List[List[float]]) -> Dict[str, Any]:
    """Deterministic, pure, look-ahead-free 15m bearish structure gate.

    Meant to be consumed by entry_decision.py's short-entry gate as a hard
    structural confirmation input. It is intentionally independent of the
    below_vwap location feature: below_vwap must never, by itself, be
    treated as sufficient bearish structural confirmation here or by any
    caller of this function.

    Input contract: ``candles_15m`` must be a list of already-CLOSED 15m
    OHLCV rows ``[timestamp_ms, open, high, low, close, volume]`` sorted
    oldest-to-newest. No open/incomplete candle may be included by the
    caller -- this function does not attempt to detect an in-progress bar,
    it only validates timestamp spacing and basic OHLC sanity.

    Structure definition used (documented per the Change D requirement):
    a "higher low" is a confirmed pivot low (a candle whose low is lower
    than the low of the two candles on each side of it) that sits above
    the immediately preceding confirmed pivot low, establishing a local
    uptrend support level. ``bearish_15m_structure`` is True only when a
    COMPLETED 15m candle closes below that most recent confirmed
    higher-low level (this is the "support_break_close" event) -- i.e.
    bearish_15m_structure and support_break_close share the exact same
    definition in this implementation: a deterministic close-based
    break of the last confirmed higher-low. A wick below the level
    without a close below it never sets either flag.

    Returns a dict:
        {
            "available": bool,
            "bearish_15m_structure": bool,
            "support_break_close": bool,
            "retest_rejected": bool | None,
            "last_higher_low": float | None,
            "broken_support": float | None,
            "reason_codes": list[str],
        }
    """
    if not isinstance(candles_15m, list) or len(candles_15m) < _STRUCTURE_MIN_CANDLES:
        return _bearish_structure_unavailable(["INSUFFICIENT_CANDLE_HISTORY"])

    normalized: List[List[float]] = []
    for row in candles_15m:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            return _bearish_structure_unavailable(["STRUCTURE_DATA_UNAVAILABLE"])
        try:
            ts, opening, high, low, close, volume = (float(value) for value in row[:6])
        except (TypeError, ValueError):
            return _bearish_structure_unavailable(["STRUCTURE_DATA_UNAVAILABLE"])
        if (
            ts <= 0
            or min(opening, high, low, close) <= 0
            or volume < 0
            or high < max(opening, close)
            or low > min(opening, close)
        ):
            return _bearish_structure_unavailable(["STRUCTURE_DATA_UNAVAILABLE"])
        normalized.append([ts, opening, high, low, close, volume])

    for previous, current in zip(normalized, normalized[1:]):
        if current[0] - previous[0] != _STRUCTURE_TIMEFRAME_MS_15M:
            return _bearish_structure_unavailable(["STRUCTURE_DATA_GAPPED"])

    wing = _STRUCTURE_PIVOT_WING
    pivot_search_end = len(normalized) - _STRUCTURE_RESERVED_TAIL
    pivot_lows: List[tuple] = []
    for index in range(wing, max(wing, pivot_search_end - wing)):
        candidate_low = normalized[index][3]
        neighbors = [
            normalized[index - offset][3] for offset in range(1, wing + 1)
        ] + [
            normalized[index + offset][3] for offset in range(1, wing + 1)
        ]
        if all(candidate_low < neighbor for neighbor in neighbors):
            pivot_lows.append((index, candidate_low))

    if len(pivot_lows) < 2:
        return _bearish_structure_unavailable(["INSUFFICIENT_CANDLE_HISTORY"])

    (_prev_index, prev_low), (last_index, last_low) = pivot_lows[-2], pivot_lows[-1]
    if not (last_low > prev_low):
        return _bearish_structure_unavailable(["NO_CONFIRMED_HIGHER_LOW"])

    support = last_low
    post_pivot = normalized[last_index + 1 :]

    break_offset = None
    for offset, row in enumerate(post_pivot):
        if row[4] < support:
            break_offset = offset
            break

    support_break_close = break_offset is not None
    wick_only = any(
        row[3] < support and row[4] >= support for row in post_pivot
    )

    reason_codes: List[str] = []
    retest_rejected: Optional[bool] = None
    broken_support: Optional[float] = None

    if support_break_close:
        broken_support = support
        reason_codes.append("SUPPORT_BREAK_CLOSE_CONFIRMED")
        after_break = post_pivot[break_offset + 1 :]
        retest_index = next(
            (i for i, row in enumerate(after_break) if row[2] >= support),
            None,
        )
        if retest_index is None:
            retest_rejected = None
            reason_codes.append("RETEST_DATA_INSUFFICIENT")
        else:
            remaining = after_break[retest_index:]
            retest_rejected = bool(remaining) and all(
                row[4] < support for row in remaining
            )
            reason_codes.append(
                "RETEST_REJECTED" if retest_rejected else "RETEST_NOT_REJECTED"
            )
    else:
        reason_codes.append("NO_CLOSED_SUPPORT_BREAK")
        if wick_only:
            reason_codes.append("WICK_ONLY_NO_CLOSE_BELOW_SUPPORT")

    return {
        "available": True,
        "bearish_15m_structure": support_break_close,
        "support_break_close": support_break_close,
        "retest_rejected": retest_rejected,
        "last_higher_low": support,
        "broken_support": broken_support,
        "reason_codes": reason_codes,
    }


def _absorption_unavailable(reason_codes: List[str]) -> Dict[str, Any]:
    return {
        "available": False,
        "sell_pressure_high": False,
        "downside_displacement_confirmed": False,
        "support_broken": False,
        "absorption_detected": False,
        "reason_codes": list(reason_codes),
    }


def evaluate_absorption(
    *,
    sell_flow_usdt: Optional[float],
    buy_flow_usdt: Optional[float],
    taker_buy_sell_ratio: Optional[float],
    closed_price_return_pct: Optional[float],
    support_break_close: Optional[bool],
    displacement_threshold_pct: float = -0.10,
) -> Dict[str, Any]:
    """Deterministic, pure absorption-risk classifier.

    Meant to be consumed by entry_decision.py's short-entry gate as a hard
    fact input alongside ``evaluate_bearish_structure``'s support-break
    outcome (``support_break_close`` is accepted here as a parameter and is
    never recomputed).

    Only fields that actually exist in this codebase are used:
    ``sell_flow_usdt`` / ``buy_flow_usdt`` (microstructure.py order-flow
    packet) and ``taker_buy_sell_ratio`` (derivatives.py packet). Order-book
    / bid-depth fields are deliberately NOT used here because they are not
    guaranteed to be reliably populated for this purpose.

    "Selling pressure high" is true when either sell-side flow notionally
    exceeds buy-side flow, or the taker buy/sell ratio is below 1.0
    (more takers selling than buying) -- matching the same directional
    convention already used by score_v2.py's bearish scoring. At least one
    of the two signal families must be present, plus a closed-candle price
    return and the Change D support-break outcome, or this function fails
    closed with ``available=False``.

    Returns a dict:
        {
            "available": bool,
            "sell_pressure_high": bool,
            "downside_displacement_confirmed": bool,
            "support_broken": bool,
            "absorption_detected": bool,
            "reason_codes": list[str],
        }
    """
    have_flow_pair = sell_flow_usdt is not None and buy_flow_usdt is not None
    have_taker_ratio = taker_buy_sell_ratio is not None
    if not have_flow_pair and not have_taker_ratio:
        return _absorption_unavailable(["ABSORPTION_DATA_UNAVAILABLE"])
    if closed_price_return_pct is None:
        return _absorption_unavailable(["ABSORPTION_DATA_UNAVAILABLE"])
    if support_break_close is None:
        return _absorption_unavailable(["ABSORPTION_DATA_UNAVAILABLE"])

    sell_pressure_high = bool(
        (have_flow_pair and sell_flow_usdt > buy_flow_usdt)
        or (have_taker_ratio and taker_buy_sell_ratio < 1.0)
    )
    downside_displacement_confirmed = bool(
        closed_price_return_pct <= displacement_threshold_pct
    )
    support_broken = bool(support_break_close)

    reason_codes: List[str] = []
    absorption_detected = False

    if sell_pressure_high and not downside_displacement_confirmed:
        absorption_detected = True
        reason_codes.append("SELL_FLOW_WITHOUT_PRICE_DISPLACEMENT")
    if sell_pressure_high and not support_broken:
        absorption_detected = True
        reason_codes.append("SUPPORT_HOLDING_DESPITE_SELL_FLOW")

    if absorption_detected:
        reason_codes.append("ABSORPTION_RISK")

    return {
        "available": True,
        "sell_pressure_high": sell_pressure_high,
        "downside_displacement_confirmed": downside_displacement_confirmed,
        "support_broken": support_broken,
        "absorption_detected": absorption_detected,
        "reason_codes": reason_codes,
    }
