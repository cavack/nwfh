import copy
import hashlib
import json
import logging
import math
import sqlite3
import threading
import time
import zlib
from typing import Any

from waterfallhunter.core.managed_sqlite import connect_managed_sqlite
from waterfallhunter.core.schema_contract import require_managed_schema
from waterfallhunter.core.signal_metadata import canonical_sha256


logger = logging.getLogger("WaterfallHunter.ProductionEvidence")


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def causal_age_seconds(decision_at: Any, observed_at: Any) -> float | None:
    """Return a finite non-negative age only for causal timestamps."""
    decision = _finite_number(decision_at)
    observed = _finite_number(observed_at)
    if (
        decision is None
        or observed is None
        or decision < 0
        or observed < 0
        or observed > decision
    ):
        return None
    return decision - observed


def build_production_replay_context(
    *,
    lifecycle_id: int,
    entry_decision: dict[str, Any],
    decision_evaluated_at: int,
    analysis_observed_at: int | float,
    reference_observed_at: int | float | None,
    policy_version: str,
    max_analysis_age_seconds: float,
    max_reference_age_seconds: float,
    trade_plan_feasibility_shadow: dict[str, Any] | None,
    decision_clock_at: int | float | None = None,
) -> dict[str, Any]:
    """Freeze observational replay context without changing decision semantics.

    ``decision_evaluated_at`` stays the integer packet timestamp while
    ``decision_clock_at`` carries the full-precision decision clock used for
    causal freshness comparison, so a fractional reference observation inside
    the same wall-clock second is never misclassified as future.
    """
    normalized_decision_at = _finite_number(decision_evaluated_at)
    clock = _finite_number(decision_clock_at)
    if clock is None:
        clock = normalized_decision_at
    analysis_at = _finite_number(analysis_observed_at)
    reference_at = _finite_number(reference_observed_at)
    analysis_age = causal_age_seconds(clock, analysis_at)
    reference_age = causal_age_seconds(clock, reference_at)
    analysis_limit = _finite_number(max_analysis_age_seconds)
    reference_limit = _finite_number(max_reference_age_seconds)
    analysis_limit = analysis_limit if analysis_limit is not None and analysis_limit >= 0 else None
    reference_limit = reference_limit if reference_limit is not None and reference_limit >= 0 else None
    return {
        "canonical_lifecycle_id": int(lifecycle_id),
        "canonical_entry_decision": copy.deepcopy(entry_decision),
        "canonical_entry_decision_sha256": canonical_sha256(entry_decision),
        "decision_evaluated_at": (
            int(normalized_decision_at)
            if normalized_decision_at is not None
            else None
        ),
        "decision_clock_at": clock,
        "analysis_observed_at": analysis_at,
        "reference_observed_at": reference_at,
        "freshness": {
            "policy_version": str(policy_version),
            "max_analysis_age_seconds": analysis_limit,
            "max_reference_age_seconds": reference_limit,
            "analysis_age_seconds": analysis_age,
            "reference_age_seconds": reference_age,
            "analysis_pass": (
                analysis_age is not None
                and analysis_limit is not None
                and analysis_age <= analysis_limit
            ),
            "reference_pass": (
                reference_age is not None
                and reference_limit is not None
                and reference_age <= reference_limit
            ),
        },
        "trade_plan_feasibility_shadow": copy.deepcopy(
            trade_plan_feasibility_shadow or {}
        ),
    }


class ProductionEvidenceRecorder:
    """Fail-open, immutable recorder for real production decision packets."""

    SCHEMA_VERSION = "production_decision_evidence_v9"
    CAPTURE_MODE = "versioned_experimental_profile_plus_final_events"

    def __init__(
        self,
        db_path: str = "/app/data/waterfall_registry.db",
        *,
        bucket_seconds: int = 300,
        verify_schema: bool = True,
    ):
        self.db_path = db_path
        self.bucket_seconds = max(60, int(bucket_seconds))
        self._write_lock = threading.Lock()
        self.total_recorded = 0
        self.total_deduplicated = 0
        self.total_failed = 0
        if verify_schema:
            require_managed_schema(
                self.db_path,
                required_tables=frozenset({"production_evidence_snapshots"}),
            )

    def _connect(self):
        return connect_managed_sqlite(self.db_path, timeout=20.0)

    @classmethod
    def _safe(cls, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, str)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, dict):
            return {str(key): cls._safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._safe(item) for item in value]
        return str(value)

    @classmethod
    def _orderbook(cls, packet: Any) -> dict | None:
        if not isinstance(packet, dict):
            return None
        result = {
            key: cls._safe(packet.get(key))
            for key in ("symbol", "timestamp", "datetime", "nonce")
            if key in packet
        }
        result["bids"] = cls._safe((packet.get("bids") or [])[:25])
        result["asks"] = cls._safe((packet.get("asks") or [])[:25])
        return result

    @classmethod
    def _ticker(cls, packet: Any) -> dict | None:
        if not isinstance(packet, dict):
            return None
        fields = (
            "symbol", "timestamp", "datetime", "last", "mark", "bid", "ask",
            "vwap", "quoteVolume", "baseVolume", "high", "low", "open", "close",
        )
        return {key: cls._safe(packet.get(key)) for key in fields if key in packet}

    @staticmethod
    def _contract_complete(contract: Any) -> bool:
        if not isinstance(contract, dict):
            return False
        application = contract.get("application")
        required_sections = (
            "strategy", "microstructure", "derivatives", "position",
            "recorder", "runtime_settings",
        )
        return bool(
            contract.get("contract_schema_version") == "production_decision_contract_v2"
            and isinstance(application, dict)
            and isinstance(application.get("source_tree_sha256"), str)
            and len(application["source_tree_sha256"]) == 64
            and all(isinstance(contract.get(name), dict) for name in required_sections)
        )

    @classmethod
    def _replay_status(
        cls,
        replay_context: Any,
        decision_contract: Any,
    ) -> tuple[bool, str | None]:
        if not isinstance(replay_context, dict) or not replay_context:
            return False, "REPLAY_CONTEXT_ABSENT"
        required = (
            "canonical_lifecycle_id",
            "canonical_entry_decision",
            "canonical_entry_decision_sha256",
            "decision_evaluated_at",
            "decision_clock_at",
            "analysis_observed_at",
            "reference_observed_at",
            "freshness",
            "trade_plan_feasibility_shadow",
            "decision_contract_sha256",
        )
        if any(key not in replay_context for key in required):
            return False, "REPLAY_CONTEXT_INCOMPLETE"
        freshness = replay_context.get("freshness")
        shadow = replay_context.get("trade_plan_feasibility_shadow")
        if (
            not isinstance(replay_context.get("canonical_lifecycle_id"), int)
            or isinstance(replay_context.get("canonical_lifecycle_id"), bool)
            or not isinstance(replay_context.get("canonical_entry_decision"), dict)
            or not isinstance(freshness, dict)
            or not isinstance(shadow, dict)
            or not isinstance(freshness.get("policy_version"), str)
            or not isinstance(freshness.get("analysis_pass"), bool)
            or not isinstance(freshness.get("reference_pass"), bool)
            or "max_analysis_age_seconds" not in freshness
            or "max_reference_age_seconds" not in freshness
            or "analysis_age_seconds" not in freshness
            or "reference_age_seconds" not in freshness
            or not isinstance(shadow.get("version"), str)
            or not isinstance(shadow.get("available"), bool)
            or not isinstance(shadow.get("trade_eligible"), bool)
            or not isinstance(shadow.get("status"), str)
            or not shadow.get("status")
            or "feasible" not in shadow
            or any(
                not isinstance(replay_context.get(key), str)
                or len(replay_context[key]) != 64
                for key in (
                    "canonical_entry_decision_sha256",
                    "decision_contract_sha256",
                )
            )
        ):
            return False, "REPLAY_CONTEXT_INCOMPLETE"
        clock = replay_context.get("decision_clock_at")
        if _finite_number(clock) is None:
            clock = replay_context.get("decision_evaluated_at")
        decision_at = replay_context.get("decision_evaluated_at")
        if (
            causal_age_seconds(clock, replay_context.get("analysis_observed_at"))
            is None
            or causal_age_seconds(clock, replay_context.get("reference_observed_at"))
            is None
        ):
            return False, "REPLAY_CONTEXT_INVALID_TIMESTAMPS"
        if (
            not isinstance(decision_at, int)
            or isinstance(decision_at, bool)
            or decision_at < 0
        ):
            return False, "REPLAY_CONTEXT_INVALID_TIMESTAMPS"
        try:
            decision_hash = canonical_sha256(
                replay_context.get("canonical_entry_decision")
            )
            contract_hash = canonical_sha256(decision_contract)
        except (TypeError, ValueError):
            return False, "REPLAY_CONTEXT_HASH_MISMATCH"
        if (
            replay_context["canonical_entry_decision_sha256"] != decision_hash
            or not isinstance(decision_contract, dict)
            or replay_context["decision_contract_sha256"] != contract_hash
        ):
            return False, "REPLAY_CONTEXT_HASH_MISMATCH"
        return True, None

    @classmethod
    def _payload(
        cls,
        *,
        symbol: str,
        observed_at: float,
        candidate_state: str | None,
        reference_source: str | None,
        reference_price: Any,
        result: dict,
        decision_contract: dict | None,
        replay_context: dict | None,
    ) -> dict:
        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        metric_fields = (
            "exchange", "mapped_symbol", "data_sources", "candle_analysis",
            "candle_features", "breakdown_confirmation", "valid_candle_timeframes",
            "microstructure", "derivatives", "benchmark_context", "strategy_stages",
            "quality_gates", "score_version", "score", "score_components",
            "watch_score", "position_setup", "trade_eligible", "analysis_reason",
            "production_decision",
            "strategy_profile", "calibration_status",
            "experimental_trigger_threshold",
            "error", "source_failures", "selected_quote_volume_usdt",
            "relative_weakness_features", "dex_context", "onchain_context",
            "liquidation_flow", "cascade_intelligence", "source_capture",
            "watch_prefilter", "full_analysis_observed_at",
        )
        captured_metrics = {
            key: cls._safe(metrics.get(key))
            for key in metric_fields
            if key in metrics
        }
        for key in ("candle_analysis", "microstructure"):
            packet = captured_metrics.get(key)
            if isinstance(packet, dict):
                packet.pop("source_capture", None)
        captured_metrics["orderbook"] = cls._orderbook(metrics.get("orderbook"))
        captured_metrics["ticker"] = cls._ticker(metrics.get("ticker"))
        candle_source = (
            metrics.get("candle_analysis", {}).get("source_capture")
            if isinstance(metrics.get("candle_analysis"), dict)
            else None
        )
        trade_source = (
            metrics.get("microstructure", {}).get("source_capture")
            if isinstance(metrics.get("microstructure"), dict)
            else None
        )
        position_source = (
            metrics.get("source_capture", {}).get("position")
            if isinstance(metrics.get("source_capture"), dict)
            else None
        )
        derivatives_packet = (
            metrics.get("derivatives")
            if isinstance(metrics.get("derivatives"), dict)
            else None
        )
        selected_derivatives_source = (
            derivatives_packet.get("source_capture")
            if isinstance(derivatives_packet, dict)
            else None
        )
        fallback_derivatives_sources = []
        if isinstance(derivatives_packet, dict):
            for attempt in derivatives_packet.get("fallback_attempts") or []:
                if isinstance(attempt, dict):
                    fallback_derivatives_sources.append(dict(attempt))
            clean_derivatives = captured_metrics.get("derivatives")
            if isinstance(clean_derivatives, dict):
                clean_derivatives.pop("source_capture", None)
                for attempt in clean_derivatives.get("fallback_attempts") or []:
                    if isinstance(attempt, dict):
                        attempt.pop("source_capture", None)
        derivatives_source = {
            "selected": cls._safe(selected_derivatives_source),
            "fallback_attempts": cls._safe(fallback_derivatives_sources),
        }
        captured_metrics["source_capture"] = {
            "candles": cls._safe(candle_source),
            "trades": cls._safe(trade_source),
            "derivatives": derivatives_source,
            "position": cls._safe(position_source),
        }
        raw_ohlcv = bool(
            isinstance(candle_source, dict)
            and candle_source.get("raw_ohlcv_captured") is True
        )
        raw_trades = bool(
            isinstance(trade_source, dict)
            and trade_source.get("raw_trades_captured") is True
        )
        orderbooks = bool(
            isinstance(trade_source, dict)
            and trade_source.get("orderbook_snapshots_captured") is True
        )
        market_filters = bool(
            isinstance(trade_source, dict)
            and trade_source.get("market_filters_captured") is True
        )
        confirmation_ohlcv = bool(
            isinstance(candle_source, dict)
            and candle_source.get("confirmation_ohlcv_captured") is True
            and candle_source.get("confirmation_closed_ohlcv_15m")
        )
        feature_ready = bool(
            raw_ohlcv
            and confirmation_ohlcv
            and raw_trades
            and orderbooks
            and market_filters
        )
        raw_derivatives = bool(
            selected_derivatives_source
            or fallback_derivatives_sources
        )
        derivatives_evidence = bool(
            isinstance(derivatives_packet, dict)
            and (
                raw_derivatives
                or (
                    derivatives_packet.get("available") is False
                    and derivatives_packet.get("reason")
                )
            )
        )
        contract = decision_contract or {}
        provenance_ready = cls._contract_complete(contract)
        position_attempted = bool(
            isinstance(position_source, dict)
            and position_source.get("attempted") is True
        )
        position_ready = bool(
            not position_attempted
            or (
                isinstance(position_source.get("raw_ohlcv"), list)
                and bool(position_source.get("raw_ohlcv"))
                and isinstance(position_source.get("evaluated_at_ms"), int)
            )
        )
        production_evidence_complete = bool(
            feature_ready
            and derivatives_evidence
            and provenance_ready
            and position_ready
        )
        decision_reason = (
            metrics.get("analysis_reason")
            or metrics.get("error")
            or (
                "strict trade gates passed"
                if result.get("is_valid") is True
                else "evaluation rejected without a complete decision packet"
            )
        )
        replay_complete, replay_unavailable_reason = cls._replay_status(
            replay_context,
            contract,
        )
        return {
            "schema_version": cls.SCHEMA_VERSION,
            "capture_mode": cls.CAPTURE_MODE,
            "observational_only": True,
            "hard_gating_allowed": False,
            "symbol": str(symbol),
            "observed_at": float(observed_at),
            "candidate_state": candidate_state,
            "reference_source": reference_source,
            "reference_price": cls._safe(reference_price),
            "result": {
                "is_valid": result.get("is_valid") is True,
                "score": cls._safe(result.get("score")),
                "suggested_status": result.get("suggested_status"),
                "observation_score": cls._safe(result.get("observation_score")),
                "observation_status": result.get("observation_status"),
                "decision_reason": cls._safe(decision_reason),
            },
            "decision_contract": cls._safe(contract),
            "replay_complete": replay_complete,
            "replay_unavailable_reason": replay_unavailable_reason,
            "replay_context": cls._safe(replay_context or {}),
            "metrics": captured_metrics,
            "capture_limitations": {
                "raw_ohlcv_captured": raw_ohlcv,
                "raw_trades_captured": raw_trades,
                "source_replay_ready": raw_ohlcv and raw_trades,
                "orderbook_snapshots_captured": orderbooks,
                "market_filters_captured": market_filters,
                "feature_replay_ready": feature_ready,
                "confirmation_ohlcv_captured": confirmation_ohlcv,
                "raw_derivatives_captured": raw_derivatives,
                "derivatives_decision_evidence_captured": derivatives_evidence,
                "decision_provenance_captured": provenance_ready,
                "production_evidence_complete": production_evidence_complete,
                "triggered_path_attempted": position_attempted,
                "triggered_path_replay_ready": feature_ready and position_ready,
                "reason": (
                    None
                    if raw_ohlcv and raw_trades
                    else "validated closed OHLCV or fresh trade source evidence is incomplete"
                ),
            },
        }

    @staticmethod
    def _finite(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    def record(
        self,
        symbol: str,
        *,
        candidate_state: str | None,
        reference_source: str | None,
        reference_price: Any,
        result: dict,
        decision_contract: dict | None = None,
        observed_at: float | None = None,
        replay_context: dict | None = None,
    ) -> bool:
        try:
            timestamp = float(time.time() if observed_at is None else observed_at)
            safe_result = result if isinstance(result, dict) else {"is_valid": False, "metrics": {"error": "invalid result packet"}}
            safe_metrics = (
                safe_result.get("metrics")
                if isinstance(safe_result.get("metrics"), dict)
                else {}
            )
            production_decision = safe_metrics.get("production_decision")
            final_event = bool(
                isinstance(production_decision, dict)
                and production_decision.get("final") is True
            )
            # The ordinary full-source packet is rate-limited per symbol. Final
            # trigger-path decisions must never be hidden by an earlier packet
            # in the same bucket, so they use an isolated negative millisecond
            # event key while retaining the immutable legacy schema.
            bucket = (
                -max(1, int(timestamp * 1000))
                if final_event
                else int(timestamp) // self.bucket_seconds * self.bucket_seconds
            )
            payload = self._payload(
                symbol=symbol,
                observed_at=timestamp,
                candidate_state=candidate_state,
                reference_source=reference_source,
                reference_price=reference_price,
                result=safe_result,
                decision_contract=decision_contract,
                replay_context=replay_context,
            )
            raw = json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
            compressed = zlib.compress(raw, level=6)
            metrics = payload["metrics"]
            orderbook = metrics.get("orderbook") or {}
            data_sources = metrics.get("data_sources") or {}
            valid_timeframes = metrics.get("valid_candle_timeframes")
            decision_complete = bool(
                metrics.get("candle_analysis")
                and metrics.get("microstructure")
                and metrics.get("derivatives")
                and metrics.get("strategy_stages")
                and metrics.get("quality_gates")
            )
            limitations = payload["capture_limitations"]
            source_ohlcv = limitations["raw_ohlcv_captured"] is True
            source_trades = limitations["raw_trades_captured"] is True
            source_ready = limitations["source_replay_ready"] is True
            feature_ready = limitations["feature_replay_ready"] is True
            triggered_ready = limitations["triggered_path_replay_ready"] is True
            provenance_ready = limitations["decision_provenance_captured"] is True
            raw_derivatives = limitations["raw_derivatives_captured"] is True
            production_complete = limitations["production_evidence_complete"] is True
            confirmation_ohlcv = limitations["confirmation_ohlcv_captured"] is True
            code_sha256 = str(
                payload.get("decision_contract", {})
                .get("application", {})
                .get("source_tree_sha256", "")
            )
            values = (
                bucket, str(symbol), timestamp, candidate_state, reference_source,
                self._finite(reference_price), int(safe_result.get("is_valid") is True),
                safe_result.get("suggested_status"), self._finite(safe_result.get("score")),
                hashlib.sha256(raw).hexdigest(), sqlite3.Binary(compressed), len(raw), len(compressed),
                int(bool(orderbook)), len(orderbook.get("bids") or []), len(orderbook.get("asks") or []),
                int(bool(metrics.get("candle_analysis"))), int(valid_timeframes or 0),
                int(bool(metrics.get("derivatives"))), int(bool(data_sources.get("confirmation"))),
                int(decision_complete), self.SCHEMA_VERSION, self.CAPTURE_MODE,
                int(source_ohlcv), int(source_trades), int(source_ready),
                int(feature_ready),
                int(triggered_ready),
                int(provenance_ready),
                int(raw_derivatives),
                int(production_complete),
                code_sha256,
                int(confirmation_ohlcv),
            )
            with self._write_lock, self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO production_evidence_snapshots (
                        bucket_started_at, symbol, observed_at, candidate_state,
                        reference_source, reference_price, result_valid,
                        suggested_status, score, evidence_sha256, evidence_zlib,
                        uncompressed_bytes, compressed_bytes, has_orderbook,
                        orderbook_bid_levels, orderbook_ask_levels,
                        has_candle_analysis, valid_candle_timeframes,
                        has_derivatives, has_confirmation_source,
                        decision_packet_complete, schema_version, capture_mode,
                        source_ohlcv_captured, source_trades_captured,
                        source_replay_ready_v2,
                        feature_replay_ready_v3,
                        triggered_path_replay_ready_v4,
                        decision_provenance_ready_v5,
                        raw_derivatives_captured_v5,
                        production_evidence_complete_v5,
                        code_sha256_v5,
                        confirmation_ohlcv_captured_v5,
                        observational_only, hard_gating_allowed, trade_eligible
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, NULL)
                    """,
                    values,
                )
            if cursor.rowcount == 1:
                self.total_recorded += 1
                return True
            self.total_deduplicated += 1
            return False
        except Exception as exc:
            self.total_failed += 1
            logger.warning("Production evidence capture failed for %s: %s", symbol, exc)
            return False

    def latest_source_hints(
        self,
        lifecycle_ids: dict[str, int],
        *,
        lookback_rows: int = 10_000,
    ) -> dict[str, dict]:
        """Return fail-closed latest full-source hints for current lifecycles.

        Only the newest evidence row encountered for a requested symbol is
        considered. A sourceless or lifecycle-mismatched newest row suppresses
        any older hint, preventing stale venue resurrection after a failure or
        remove/re-add episode.
        """
        expected: dict[str, int] = {}
        for symbol, lifecycle_id in (lifecycle_ids or {}).items():
            if (
                isinstance(symbol, str)
                and symbol
                and isinstance(lifecycle_id, int)
                and not isinstance(lifecycle_id, bool)
                and lifecycle_id >= 1
            ):
                expected[symbol] = lifecycle_id
        if not expected:
            return {}

        try:
            limit = max(1, min(50_000, int(lookback_rows)))
        except (TypeError, ValueError):
            limit = 10_000

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, symbol, observed_at, evidence_zlib
                FROM production_evidence_snapshots
                WHERE schema_version = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (self.SCHEMA_VERSION, limit),
            ).fetchall()

        hints: dict[str, dict] = {}
        seen: set[str] = set()
        for row in rows:
            symbol = str(row[1])
            if symbol not in expected or symbol in seen:
                continue
            seen.add(symbol)
            try:
                payload = json.loads(zlib.decompress(row[3]))
            except (TypeError, ValueError, zlib.error, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            replay_context = payload.get("replay_context")
            if not isinstance(replay_context, dict):
                continue
            lifecycle_id = replay_context.get("canonical_lifecycle_id")
            if (
                isinstance(lifecycle_id, bool)
                or not isinstance(lifecycle_id, int)
                or lifecycle_id != expected[symbol]
            ):
                continue
            metrics = payload.get("metrics")
            if not isinstance(metrics, dict):
                continue
            exchange = metrics.get("exchange")
            mapped_symbol = metrics.get("mapped_symbol")
            if not isinstance(exchange, str) or not exchange.strip():
                continue
            if not isinstance(mapped_symbol, str) or not mapped_symbol.strip():
                continue
            evidence_observed_at = _finite_number(row[2])
            full_analysis_observed_at = _finite_number(
                metrics.get("full_analysis_observed_at")
            )
            if full_analysis_observed_at is None:
                if isinstance(metrics.get("watch_prefilter"), dict):
                    continue
                full_analysis_observed_at = evidence_observed_at
            if (
                evidence_observed_at is None
                or full_analysis_observed_at is None
                or evidence_observed_at < 0
                or full_analysis_observed_at < 0
                or full_analysis_observed_at > evidence_observed_at + 1e-6
            ):
                continue
            hints[symbol] = {
                "exchange": exchange.strip(),
                "mapped_symbol": mapped_symbol.strip(),
                "lifecycle_id": lifecycle_id,
                "full_analysis_observed_at": float(full_analysis_observed_at),
                "evidence_observed_at": float(evidence_observed_at),
            }
        return hints

    def read_payload(self, snapshot_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT evidence_zlib FROM production_evidence_snapshots WHERE id = ?",
                (int(snapshot_id),),
            ).fetchone()
        return json.loads(zlib.decompress(row[0])) if row else None

    def build_report(self, *, now: float | None = None) -> dict:
        timestamp = float(time.time() if now is None else now)
        since = timestamp - 86_400
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            # The index is pinned deliberately. Left to itself the planner
            # chooses idx_production_evidence_replay_v9 (schema_version, id),
            # which cannot satisfy the observed_at predicate, so it fetches
            # every row of the current generation to re-test the filter:
            # 493,133 rows visited for the 18,289 actually wanted. Measured
            # on the live 37.9 GB registry with a cold page cache, that is
            # 40.5s - long enough that the Next.js rewrite proxy gives up and
            # the dashboard reports an empty production-evidence panel.
            # Searched through idx_production_evidence_time the same aggregate
            # returns in 1.9s cold. A covering index would be faster still but
            # requires a schema-contract and migration-preflight change, so it
            # is deliberately out of scope here.
            totals = conn.execute(
                """
                SELECT COUNT(*) snapshot_count, COUNT(DISTINCT symbol) symbol_count,
                       SUM(decision_packet_complete) complete_count,
                       SUM(has_orderbook) orderbook_count,
                       SUM(has_candle_analysis) candle_count,
                       SUM(has_derivatives) derivatives_count,
                       SUM(has_confirmation_source) confirmation_count,
                       SUM(source_ohlcv_captured) source_ohlcv_count,
                       SUM(source_trades_captured) source_trades_count,
                       SUM(source_replay_ready_v2) source_ready_count,
                       SUM(feature_replay_ready_v3) feature_ready_count,
                       SUM(triggered_path_replay_ready_v4) triggered_ready_count,
                       SUM(decision_provenance_ready_v5) provenance_ready_count,
                       SUM(raw_derivatives_captured_v5) raw_derivatives_count,
                       SUM(production_evidence_complete_v5) production_complete_count,
                       SUM(confirmation_ohlcv_captured_v5) confirmation_ohlcv_count,
                       MAX(observed_at) latest_observed_at,
                       SUM(uncompressed_bytes) uncompressed_bytes,
                       SUM(compressed_bytes) compressed_bytes
                FROM production_evidence_snapshots
                    INDEXED BY idx_production_evidence_time
                WHERE observed_at >= ? AND schema_version = ?
                """,
                (since, self.SCHEMA_VERSION),
            ).fetchone()
            all_count = conn.execute("SELECT COUNT(*) FROM production_evidence_snapshots").fetchone()[0]
        count = int(totals["snapshot_count"] or 0)
        def rate(field: str) -> float | None:
            return round(float(totals[field] or 0) / count, 6) if count else None
        latest = totals["latest_observed_at"]
        return {
            "schema_version": self.SCHEMA_VERSION,
            "operational": True,
            "observational_only": True,
            "hard_gating_allowed": False,
            "capture_mode": self.CAPTURE_MODE,
            "window_seconds": 86_400,
            "snapshot_count_24h": count,
            "total_snapshot_count": int(all_count),
            "report_scope": "current_capture_generation",
            "symbol_count_24h": int(totals["symbol_count"] or 0),
            "latest_observed_at": latest,
            "latest_age_seconds": round(timestamp - latest, 3) if latest is not None else None,
            "coverage": {
                "decision_packet_complete_rate": rate("complete_count"),
                "orderbook_rate": rate("orderbook_count"),
                "candle_analysis_rate": rate("candle_count"),
                "derivatives_rate": rate("derivatives_count"),
                "confirmation_source_rate": rate("confirmation_count"),
                "decision_provenance_rate": rate("provenance_ready_count"),
                "raw_derivatives_rate": rate("raw_derivatives_count"),
                "production_evidence_complete_rate": rate("production_complete_count"),
                "confirmation_ohlcv_capture_rate": rate("confirmation_ohlcv_count"),
            },
            "storage": {
                "uncompressed_bytes_24h": int(totals["uncompressed_bytes"] or 0),
                "compressed_bytes_24h": int(totals["compressed_bytes"] or 0),
            },
            "replay": {
                "decision_packet_replay": True,
                "source_replay_ready": rate("source_ready_count") == 1.0,
                "source_replay_ready_rate": rate("source_ready_count"),
                "raw_ohlcv_captured": rate("source_ohlcv_count") == 1.0,
                "raw_ohlcv_capture_rate": rate("source_ohlcv_count"),
                "raw_trades_captured": rate("source_trades_count") == 1.0,
                "raw_trades_capture_rate": rate("source_trades_count"),
                "feature_replay_ready": rate("feature_ready_count") == 1.0,
                "feature_replay_ready_rate": rate("feature_ready_count"),
                "triggered_path_replay_ready": rate("triggered_ready_count") == 1.0,
                "triggered_path_replay_ready_rate": rate("triggered_ready_count"),
                "decision_provenance_captured": rate("provenance_ready_count") == 1.0,
                "decision_provenance_capture_rate": rate("provenance_ready_count"),
                "raw_derivatives_captured": rate("raw_derivatives_count") == 1.0,
                "raw_derivatives_capture_rate": rate("raw_derivatives_count"),
                "production_evidence_complete": rate("production_complete_count") == 1.0,
                "production_evidence_complete_rate": rate("production_complete_count"),
                "confirmation_ohlcv_captured": rate("confirmation_ohlcv_count") == 1.0,
                "confirmation_ohlcv_capture_rate": rate("confirmation_ohlcv_count"),
            },
            "runtime": {
                "recorded": self.total_recorded,
                "deduplicated": self.total_deduplicated,
                "failed": self.total_failed,
            },
        }
