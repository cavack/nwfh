"""AI advisory integration for WaterfallHunter.

TypeSafe System One (Jev) integration. The advisory is observational only: it
never vetoes, never mutates a decision, and is never on the critical path.

Jev returns typed judgements (a probability, a rubric position) rather than
free text, so the verdict is read from structured fields instead of parsing a
sentence out of a completion. The human-readable ``note`` is therefore
synthesised in code from the same canonical metrics that were sent as state.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from waterfallhunter.config import settings

logger = logging.getLogger("WaterfallHunter.AICascade")

# ─── Judgement constants ─────────────────────────────────────────────────
# These, together with the question text in ``_build_questions``, are the only
# knobs that turn a typed judgement into an advisory verdict. Keep them in one
# place so they stay reviewable.
VERIFIED_PROBABILITY_THRESHOLD = 0.5
CONFIDENCE_LEVELS = (
    "No support for the short",
    "Weak support",
    "Mixed support",
    "Solid support",
    "Strong support",
)
MAX_CONCURRENT_REQUESTS = 2
RATE_LIMIT_BACKOFF_SECONDS = 1.0
SYSTEMONE_PATH = "/v1/systemone"

CANONICAL_ADVISORY_DELIVERY_GRACE_SECONDS = 300  # 5 minutes


@dataclass(frozen=True)
class AICascadeOpinion:
    """Validated AI advisory output."""

    verified: bool
    note: str
    score: int
    provider: str  # "typesafe" or "none"
    model: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "note": self.note,
            "score": self.score,
            "provider": self.provider,
            "model": self.model,
            "raw": self.raw,
        }

    def to_observational_advisory(self) -> dict[str, Any]:
        """Project the opinion onto the observational advisory contract.

        Consumers (``EntryDecisionStore.append_advisory``,
        ``dashboard_projection``, ``notifier``) read this dictionary shape, so
        the opinion is never handed over as a dataclass instance. Both the
        canonical flags (``observational_only``/``decision_mutated``) and the
        legacy flags (``ai_observational_only``/``ai_decision_critical``) are
        emitted so that every consumer keeps working.
        """
        available = self.provider == "typesafe"
        confidence_index = min(
            len(CONFIDENCE_LEVELS) - 1,
            max(0, round(int(self.score) / 100 * (len(CONFIDENCE_LEVELS) - 1))),
        )
        return {
            "observational_only": True,
            "decision_mutated": False,
            "ai_observational_only": True,
            "ai_decision_critical": False,
            "ai_status": "AVAILABLE" if available else "UNAVAILABLE",
            "ai_question": "Does the supplied evidence support a valid short setup?",
            "ai_advice": ("SUPPORTS_SHORT" if self.verified else "DOES_NOT_SUPPORT_SHORT")
            if available
            else "UNAVAILABLE",
            "ai_answer_yes": bool(self.verified) if available else None,
            "ai_confidence": int(self.score) if available else 0,
            "ai_confidence_label": CONFIDENCE_LEVELS[confidence_index] if available else "Unavailable",
            "ai_reasoning": str(self.note),
            "ai_provider": self.provider if available else "none",
            "ai_model": self.model if available else "none",
        }


class AICascadeIntelligence:
    """Fetch a typed advisory from the TypeSafe System One API."""

    def __init__(self) -> None:
        self.base_url = str(
            getattr(settings, "typesafe_base_url", "") or "https://api.typesafe.ai"
        ).rstrip("/")
        self.model = str(getattr(settings, "typesafe_model", "") or "jev-latest")
        self.api_key = str(getattr(settings, "typesafe_api_key", "") or "")
        # TypeSafe answers in seconds; the old CPU-bound 120s budget only
        # existed because inference ran locally.
        self.timeout = float(getattr(settings, "typesafe_timeout_seconds", 30.0) or 30.0)
        self._request_gate: asyncio.Semaphore | None = None
        logger.info(
            "AICascadeIntelligence initialised: provider=typesafe model=%s configured=%s",
            self.model,
            bool(self.api_key),
        )

    @staticmethod
    def _unavailable_advisory(reason: str) -> AICascadeOpinion:
        return AICascadeOpinion(
            verified=False,
            note=f"AI advisory unavailable: {reason}",
            score=0,
            provider="none",
            model="none",
            raw={"error": reason},
        )

    # ── State and questions ──────────────────────────────────────────────

    @staticmethod
    def _rec(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _build_state(
        self,
        metrics: dict[str, Any],
        decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the evaluation state from the canonical decision packet.

        The previous prompt read ``readiness_score`` / ``coverage_score`` /
        ``structure_status`` / ``cascade_status`` / ``signal_summary``. None of
        those keys are produced anywhere, so every request told the model
        "Readiness 0/100, Cascade FAIL, Entry N/A" and it correctly answered
        that the signal carried no information. This reads the fields
        ``build_entry_decision`` and the validator actually emit.
        """
        packet = decision if isinstance(decision, dict) else {}
        if not packet:
            candidate = metrics.get("entry_decision")
            packet = candidate if isinstance(candidate, dict) else {}

        rec = self._rec
        reasons = packet.get("reason_codes")
        blocks = packet.get("block_reasons")
        cascade = rec(metrics.get("cascade_intelligence"))
        evidence = rec(packet.get("evidence_summary"))
        ev_deriv = rec(evidence.get("derivatives"))
        ev_flow = rec(evidence.get("order_flow"))
        ev_exec = rec(evidence.get("execution"))
        plan = rec(packet.get("trade_plan"))
        candles = rec(metrics.get("candle_features"))

        return {
            "objective": (
                "Second opinion on a SHORT setup. Advisory only; the engine "
                "keeps full authority and this never overrides it."
            ),
            "symbol": str(metrics.get("symbol") or packet.get("symbol") or "UNKNOWN"),
            "decision": str(packet.get("decision") or "UNAVAILABLE"),
            "lifecycle_state": str(
                packet.get("lifecycle_state") or metrics.get("status") or "n/a"
            ),
            "entry_readiness": packet.get("entry_readiness"),
            "evidence_coverage_pct": packet.get("evidence_coverage_pct"),
            "reason_codes": reasons[:10] if isinstance(reasons, list) else [],
            "block_reasons": blocks if isinstance(blocks, list) else [],
            "cascade": {
                "status": cascade.get("status"),
                "readiness_points": cascade.get("readiness_points"),
                "maximum_available": cascade.get("maximum_available"),
            },
            "order_flow": {
                "taker_buy_sell_ratio": ev_flow.get("taker_buy_sell_ratio"),
                "sell_share_pct": ev_flow.get("sell_share_pct"),
            },
            "derivatives": {
                "oi_change_1h_pct": ev_deriv.get("oi_change_1h_pct"),
                "funding_rate_pct": ev_deriv.get("funding_rate_pct"),
            },
            "execution": {
                "spread_pct": ev_exec.get("spread_pct"),
            },
            "evidence": {
                "cross_exchange_confirmed": evidence.get("cross_exchange_confirmed"),
                "anti_chase_extension_atr": evidence.get("anti_chase_extension_atr"),
            },
            "candles_4h": {
                "lower_high": rec(candles.get("4h")).get("lower_high"),
                "failed_pullback": rec(candles.get("4h")).get("setup") == "FAILED_PULLBACK",
                "bearish_close": rec(candles.get("4h")).get("bearish_close"),
            },
            "candles_1h": {
                "lower_high": rec(candles.get("1h")).get("lower_high"),
                "rsi_rollover": rec(candles.get("1h")).get("rsi_rollover"),
                "bearish_close": rec(candles.get("1h")).get("bearish_close"),
            },
            "trade_plan": {
                "entry_price": plan.get("entry_price"),
                "stop_loss": plan.get("stop_loss"),
                "take_profit_1": plan.get("take_profit_1"),
                "take_profit_2": plan.get("take_profit_2"),
                "reward_to_risk": plan.get("reward_to_risk"),
            },
        }

    def _build_questions(self) -> dict[str, Any]:
        """The typed judgements asked about every signal.

        Two independent questions over the same state, answered in one call.
        """
        return {
            "setup_verified": {
                "type": "noul",
                "instructions": (
                    "Does the supplied evidence support a valid short setup?"
                ),
                "criteria": {
                    "true": "Bearish structure and/or sell-side flow support the short",
                    "false": "Evidence is mixed, stale, or contradicts the short",
                },
            },
            "confidence": {
                "type": "score",
                "instructions": "How strong is the evidence for the short?",
                "criteria": list(CONFIDENCE_LEVELS),
            },
        }

    # ── Note synthesis ───────────────────────────────────────────────────

    @staticmethod
    def _synthesize_note(state: dict[str, Any], verified: bool) -> str:
        """Build the human-readable note from the canonical state.

        Jev returns judgements, not prose, so the sentence is composed here
        from the same values that were evaluated.
        """
        factors: list[str] = []
        status = str((state.get("cascade") or {}).get("status") or "").strip()
        if status:
            factors.append(f"cascade {status.lower()}")

        taker = (state.get("order_flow") or {}).get("taker_buy_sell_ratio")
        if isinstance(taker, (int, float)) and not isinstance(taker, bool):
            factors.append(f"taker {taker:.2f}")

        h4 = state.get("candles_4h") or {}
        if h4.get("lower_high"):
            factors.append("4h lower high")
        elif h4.get("bearish_close"):
            factors.append("bearish 4h close")
        else:
            h1 = state.get("candles_1h") or {}
            if h1.get("rsi_rollover"):
                factors.append("1h rsi rollover")

        if not factors:
            return "Insufficient canonical evidence for a second opinion"
        verdict = "support" if verified else "do not support"
        return f"{', '.join(factors[:3])} {verdict} the short"[:500]

    # ── Evaluation ───────────────────────────────────────────────────────

    def _coerce_opinion(
        self,
        answers: dict[str, Any],
        model: str,
    ) -> AICascadeOpinion:
        """Validate the typed answers into an advisory opinion."""
        try:
            verified_answer = self._rec(answers.get("setup_verified"))
            confidence_answer = self._rec(answers.get("confidence"))
            probability_value = verified_answer.get("noul")
            score_value = confidence_answer.get("score")
            if isinstance(probability_value, bool) or isinstance(score_value, bool):
                raise ValueError("boolean TypeSafe answer")
            if not isinstance(probability_value, (int, float)) or not isinstance(score_value, (int, float)):
                raise ValueError("missing or invalid TypeSafe answer")

            probability = float(probability_value)
            probability = max(0.0, min(1.0, probability))
            verified = probability >= VERIFIED_PROBABILITY_THRESHOLD

            raw_score = float(score_value)
            span = max(1, len(CONFIDENCE_LEVELS) - 1)
            score = int(round(max(0.0, min(float(span), raw_score)) / span * 100))
        except (TypeError, ValueError):
            return self._unavailable_advisory("Invalid TypeSafe advisory payload.")

        return AICascadeOpinion(
            verified=verified,
            note="",
            score=score,
            provider="typesafe",
            model=model or self.model,
            raw={
                "setup_verified": verified_answer,
                "confidence": confidence_answer,
                "verified_probability": probability,
            },
        )

    async def get_advisory(
        self,
        metrics: dict[str, Any],
        decision: dict[str, Any] | None = None,
    ) -> AICascadeOpinion:
        """Fetch a typed advisory from TypeSafe."""
        if not self.api_key:
            return self._unavailable_advisory(
                "TYPESAFE_API_KEY is not configured."
            )
        try:
            state = self._build_state(metrics, decision)
            payload = {
                "state": state,
                "model": self.model,
                "questions": self._build_questions(),
            }
            response = await self._request_typesafe(payload)
            if response is None:
                return self._unavailable_advisory("TypeSafe request failed.")

            answers = response.get("answers")
            if not isinstance(answers, dict):
                return self._unavailable_advisory(
                    "TypeSafe response contained no answers."
                )

            opinion = self._coerce_opinion(answers, str(response.get("model") or ""))
            if opinion.provider == "none":
                return opinion
            return AICascadeOpinion(
                verified=opinion.verified,
                note=self._synthesize_note(state, opinion.verified),
                score=opinion.score,
                provider=opinion.provider,
                model=opinion.model,
                raw=opinion.raw,
            )
        except Exception as exc:
            logger.exception("AI advisory error: %s", exc)
            return self._unavailable_advisory(
                f"TypeSafe unavailable ({type(exc).__name__})."
            )

    async def _request_typesafe(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """POST to the System One endpoint, backing off once on rate limits."""
        url = f"{self.base_url}{SYSTEMONE_PATH}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self._request_gate is None:
            self._request_gate = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        try:
            await asyncio.wait_for(
                self._request_gate.acquire(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                "TypeSafe advisory queue saturated; skipping request rather "
                "than queueing behind an unbounded backlog."
            )
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                for attempt in range(2):
                    response = await client.post(url, json=payload, headers=headers)
                    if response.status_code in (429, 529) and attempt == 0:
                        logger.warning(
                            "TypeSafe rate limited (HTTP %s); backing off %.1fs.",
                            response.status_code,
                            RATE_LIMIT_BACKOFF_SECONDS,
                        )
                        await asyncio.sleep(RATE_LIMIT_BACKOFF_SECONDS)
                        continue
                    break
                if response.status_code != 200:
                    logger.warning(
                        "TypeSafe API error (HTTP %s): %s",
                        response.status_code,
                        response.text[:200],
                    )
                    return None
                body = response.json()
                return body if isinstance(body, dict) else None
        except httpx.TimeoutException as exc:
            logger.warning(
                "TypeSafe request timed out after %ss (%s); advisory marked "
                "unavailable.",
                self.timeout,
                type(exc).__name__,
            )
            return None
        except Exception as exc:
            logger.warning("TypeSafe request failed: %s", exc)
            return None
        finally:
            self._request_gate.release()

    def status(self) -> dict[str, str]:
        """Return AI status for health checks."""
        return {
            "ai_model": self.model,
            "ai_status": "AVAILABLE" if self.api_key else "UNAVAILABLE",
            "provider": "typesafe",
        }


_ai_intel: AICascadeIntelligence | None = None


def get_ai_intelligence() -> AICascadeIntelligence:
    global _ai_intel
    if _ai_intel is None:
        _ai_intel = AICascadeIntelligence()
    return _ai_intel


# ─── AIVetoEngine: deterministic veto + TypeSafe advisory ────────────────


class AIVetoEngine:
    """Deterministic veto engine with a TypeSafe AI advisory.

    Provides:
    - evaluate_deterministic: provider-free market-data veto, no AI call
    - advisory_for_decision: async AI advisory from TypeSafe
    - get_observational_advisory: async advisory without veto authority
    - evaluate_symbol: compatibility API combining both
    """

    def __init__(self) -> None:
        self._intel = get_ai_intelligence()
        self.max_bid_ask_ratio = 3.0

    @staticmethod
    def _observational_placeholder(reason: str) -> dict[str, Any]:
        """Provider-free placeholder that satisfies the advisory contract."""
        return {
            "observational_only": True,
            "decision_mutated": False,
            "ai_observational_only": True,
            "ai_decision_critical": False,
            "deterministic_veto": False,
            "deterministic_reason": None,
            "ai_status": "UNAVAILABLE",
            "ai_advice": "PENDING",
            "ai_confidence": 0,
            "ai_reasoning": reason,
            "ai_provider": "none",
            "ai_model": "none",
        }

    def evaluate_deterministic(
        self,
        symbol: str,
        orderbook: dict[str, Any],
        ticker: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Provider-free deterministic market-data veto.

        Returns (vetoed, advisory) — AI is never consulted here. The advisory
        is the observational dictionary contract so that
        ``metrics["ai_advisory"]`` stays consumable by the entry-decision
        reasons and the dashboard projection.
        """
        if not orderbook or not ticker:
            logger.warning(
                "SOFT WARNING [%s]: Missing real market data, but not vetoing.",
                symbol,
            )
            return False, {
                **self._observational_placeholder(
                    "Insufficient market data for AI advisory"
                ),
                "deterministic_reason": "Missing real data (soft warning)",
            }

        bids = orderbook.get("bids", [])[:10]
        asks = orderbook.get("asks", [])[:10]
        bid_vol = sum(row[1] for row in bids) if bids else 0
        ask_vol = sum(row[1] for row in asks) if asks else 0

        deterministic_veto = False
        veto_reason = "Approved by Deterministic Math"

        if ask_vol == 0:
            deterministic_veto = True
            veto_reason = "No Ask liquidity available."
        elif (bid_vol / ask_vol) > self.max_bid_ask_ratio:
            deterministic_veto = True
            veto_reason = (
                f"Bid wall is {(bid_vol / ask_vol):.1f}x larger than Ask wall. "
                "Long squeeze risk."
            )

        if deterministic_veto:
            logger.warning("HARD VETO APPLIED for %s: %s", symbol, veto_reason)

        return deterministic_veto, {
            **self._observational_placeholder(
                "AI advisory pending — runs asynchronously after signal persistence."
            ),
            "deterministic_veto": deterministic_veto,
            "deterministic_reason": veto_reason,
        }

    async def advisory_for_decision(
        self,
        symbol: str,
        metrics: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        """Get the AI advisory from TypeSafe for the given metrics."""
        opinion = await self._intel.get_advisory(
            {**metrics, "symbol": symbol},
            decision,
        )
        return opinion.to_observational_advisory()

    async def get_observational_advisory(
        self,
        symbol: str,
        orderbook: dict[str, Any],
        ticker: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch the TypeSafe advisory without granting it veto authority."""
        opinion = await self._intel.get_advisory(
            {
                "symbol": symbol,
                "orderbook": orderbook,
                "ticker": ticker,
            }
        )
        advisory = opinion.to_observational_advisory()
        logger.info(
            "TypeSafe Advisory [%s]: %s (Conf: %s%%) | Reason: %s",
            symbol,
            advisory["ai_advice"],
            advisory["ai_confidence"],
            advisory["ai_reasoning"],
        )
        return advisory

    async def evaluate_symbol(
        self,
        symbol: str,
        orderbook: dict[str, Any],
        ticker: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Compatibility API for callers that want the full advisory."""
        deterministic_veto, advisory_data = self.evaluate_deterministic(
            symbol,
            orderbook,
            ticker,
        )
        if not orderbook or not ticker:
            return deterministic_veto, advisory_data

        advisory_data.update(
            await self.get_observational_advisory(
                symbol,
                orderbook,
                ticker,
            )
        )
        return deterministic_veto, advisory_data

    def status(self) -> dict[str, str]:
        """Return AI status for health checks."""
        return self._intel.status()


# Singleton instance
ai_veto = AIVetoEngine()
