from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import time
from contextlib import closing
from html import escape
from pathlib import Path
from typing import Any

import httpx

from waterfallhunter.config import settings
from waterfallhunter.core.notification_delivery import (
    DeliveryDisposition,
    DeliveryResult,
    DurableNotificationWorker,
)
from waterfallhunter.core.signal_metadata import canonical_sha256

logger = logging.getLogger("WaterfallHunter.Telegram")


class TelegramNotifier:
    def __init__(self, db_adapter=None, scanner=None):
        self.token = settings.telegram_token
        self.chat_id = (
            str(settings.telegram_chat_id)
            if settings.telegram_chat_id is not None
            else None
        )
        self.enabled = bool(self.token and self.chat_id)
        configured_cutover = settings.telegram_signal_delivery_cutover_at
        self.signal_delivery_cutover_at = (
            configured_cutover
            if (
                isinstance(configured_cutover, int)
                and not isinstance(configured_cutover, bool)
                and configured_cutover > 0
            )
            else None
        )
        self.signal_delivery_enabled = bool(
            self.enabled
            and settings.telegram_signal_delivery_enabled
            and self.signal_delivery_cutover_at is not None
        )
        self.db = db_adapter
        self.scanner = scanner
        self.offset = 0
        self.delivery_wakeup = asyncio.Event()
        db_path = getattr(db_adapter, "db_path", None)
        self.delivery_worker = (
            DurableNotificationWorker(
                db_path,
                self,
                worker_id=f"telegram-{os.getpid()}",
                transport_timeout_seconds=12.0,
                verify_schema=False,
            )
            if db_path
            else None
        )

    @staticmethod
    def _delivery_result_from_response(response: httpx.Response) -> DeliveryResult:
        payload: dict[str, Any] = {}
        try:
            decoded = response.json()
            if isinstance(decoded, dict):
                payload = decoded
        except (ValueError, json.JSONDecodeError):
            payload = {}

        status = int(response.status_code)
        telegram_error = payload.get("error_code")
        if status == 429 or telegram_error == 429:
            retry_after = None
            parameters = payload.get("parameters")
            if isinstance(parameters, dict):
                candidate = parameters.get("retry_after")
                if (
                    isinstance(candidate, int)
                    and not isinstance(candidate, bool)
                    and candidate > 0
                ):
                    retry_after = candidate
            if retry_after is None:
                header = response.headers.get("Retry-After")
                try:
                    parsed = int(header) if header is not None else None
                except ValueError:
                    parsed = None
                if parsed is not None and parsed > 0:
                    retry_after = parsed
            return DeliveryResult(
                DeliveryDisposition.RATE_LIMITED,
                error_code="HTTP_429",
                retry_after_seconds=retry_after,
            )

        if 200 <= status < 300 and payload.get("ok") is True:
            return DeliveryResult(DeliveryDisposition.DELIVERED)

        error_code = f"HTTP_{status}"
        if status in {408, 425} or status >= 500:
            return DeliveryResult(
                DeliveryDisposition.TRANSIENT_FAILURE,
                error_code=error_code,
            )
        if 400 <= status < 500:
            return DeliveryResult(
                DeliveryDisposition.PERMANENT_FAILURE,
                error_code=error_code,
            )
        return DeliveryResult(
            DeliveryDisposition.TRANSIENT_FAILURE,
            error_code="INVALID_TELEGRAM_RESPONSE",
        )

    async def _send_text_result(self, text: str) -> DeliveryResult:
        if not self.enabled:
            return DeliveryResult(
                DeliveryDisposition.PERMANENT_FAILURE,
                error_code="TELEGRAM_DISABLED",
            )
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
        return self._delivery_result_from_response(response)

    async def send_message(self, text: str):
        if not self.enabled:
            return
        try:
            result = await self._send_text_result(text)
        except Exception:
            logger.exception("Failed to send Telegram message")
            return
        if result.disposition is not DeliveryDisposition.DELIVERED:
            logger.error(
                "Telegram message was not delivered: %s",
                result.error_code or result.disposition.value,
            )

    async def send_signal_alert(self, symbol: str, data: dict):
        # The signal transaction already persisted the immutable outbox event.
        # Never post directly here: waking the durable worker preserves retry,
        # lease and dead-letter semantics if Telegram is unavailable.
        del symbol, data
        if self.signal_delivery_enabled:
            self.delivery_wakeup.set()

    def _load_strict_signal_material(
        self,
        signal_id: int,
    ) -> tuple[str, dict[str, Any]] | None:
        db_path = getattr(self.db, "db_path", None)
        if not db_path:
            return None
        path = Path(str(db_path))
        if not path.is_file():
            return None
        try:
            with closing(
                sqlite3.connect(
                    f"{path.resolve().as_uri()}?mode=ro",
                    uri=True,
                    timeout=5.0,
                )
            ) as conn:
                row = conn.execute(
                    """
                    SELECT
                        ledger.symbol,
                        ledger.score,
                        ledger.trigger_metrics_json,
                        metadata.signal_class,
                        metadata.strategy_profile
                    FROM lbank_signal_ledger AS ledger
                    JOIN signal_metadata AS metadata
                        ON metadata.signal_id = ledger.id
                    WHERE ledger.id = ?
                    """,
                    (signal_id,),
                ).fetchone()
        except sqlite3.Error:
            logger.exception(
                "Failed to load immutable Telegram signal material for %s",
                signal_id,
            )
            return None
        if row is None:
            return None
        try:
            metrics = json.loads(str(row[2]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(metrics, dict):
            return None
        if str(row[3]) != "STRICT":
            return None
        return (
            str(row[0]),
            {
                "score": float(row[1]),
                "metrics": metrics,
                "signal_class": str(row[3]),
                "strategy_profile": str(row[4]),
            },
        )

    async def deliver(self, event: dict[str, Any]) -> DeliveryResult:
        if (
            event.get("event_type") != "SIGNAL_CONFIRMED"
            or event.get("payload_contract_version")
            != "signal_confirmed_event_v1"
        ):
            return DeliveryResult(
                DeliveryDisposition.PERMANENT_FAILURE,
                error_code="UNSUPPORTED_OUTBOX_EVENT",
            )

        try:
            payload = json.loads(str(event.get("payload_json") or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return DeliveryResult(
                DeliveryDisposition.PERMANENT_FAILURE,
                error_code="INVALID_EVENT_PAYLOAD",
            )
        if not isinstance(payload, dict):
            return DeliveryResult(
                DeliveryDisposition.PERMANENT_FAILURE,
                error_code="INVALID_EVENT_PAYLOAD",
            )

        if str(event.get("payload_hash") or "") != canonical_sha256(payload):
            return DeliveryResult(
                DeliveryDisposition.PERMANENT_FAILURE,
                error_code="EVENT_PAYLOAD_HASH_MISMATCH",
            )

        signal_class = str(payload.get("signal_class") or "")
        if signal_class == "EXPERIMENTAL":
            # Experimental observations are intentionally never Telegram alerts.
            return DeliveryResult(DeliveryDisposition.DELIVERED)
        if signal_class != "STRICT":
            return DeliveryResult(
                DeliveryDisposition.PERMANENT_FAILURE,
                error_code="UNSUPPORTED_SIGNAL_CLASS",
            )

        cutover_at = self.signal_delivery_cutover_at
        if not self.signal_delivery_enabled or cutover_at is None:
            return DeliveryResult(
                DeliveryDisposition.PERMANENT_FAILURE,
                error_code="SIGNAL_DELIVERY_DISABLED",
            )

        created_at = payload.get("created_at")
        if (
            isinstance(created_at, bool)
            or not isinstance(created_at, int)
            or created_at < 0
        ):
            return DeliveryResult(
                DeliveryDisposition.PERMANENT_FAILURE,
                error_code="INVALID_EVENT_CREATED_AT",
            )

        if created_at < cutover_at:
            logger.info(
                "Suppressing pre-cutover STRICT Telegram event %s "
                "(created_at=%s cutover_at=%s)",
                event.get("event_id"),
                created_at,
                cutover_at,
            )
            return DeliveryResult(DeliveryDisposition.DELIVERED)

        raw_signal_id = payload.get("signal_id")
        if (
            isinstance(raw_signal_id, bool)
            or not isinstance(raw_signal_id, int)
            or raw_signal_id < 1
        ):
            return DeliveryResult(
                DeliveryDisposition.PERMANENT_FAILURE,
                error_code="INVALID_SIGNAL_ID",
            )

        material = await asyncio.to_thread(
            self._load_strict_signal_material,
            raw_signal_id,
        )
        if material is None:
            return DeliveryResult(
                DeliveryDisposition.TRANSIENT_FAILURE,
                error_code="SIGNAL_MATERIAL_UNAVAILABLE",
            )
        symbol, data = material
        if (
            symbol != str(payload.get("symbol") or "")
            or data["signal_class"] != signal_class
            or data["strategy_profile"]
            != str(payload.get("strategy_profile") or "")
        ):
            return DeliveryResult(
                DeliveryDisposition.PERMANENT_FAILURE,
                error_code="EVENT_LEDGER_IDENTITY_MISMATCH",
            )

        return await self._send_text_result(
            self.build_signal_message(symbol, data)
        )

    async def _delivery_loop(self) -> None:
        if (
            not self.signal_delivery_enabled
            or self.delivery_worker is None
        ):
            return
        while True:
            try:
                self.delivery_wakeup.clear()
                while True:
                    outcome = await self.delivery_worker.dispatch_once(
                        now=int(time.time())
                    )
                    if outcome is None:
                        break
                    if (
                        outcome.state == "RETRY_WAIT"
                        and outcome.error_code == "HTTP_429"
                    ):
                        break
                try:
                    await asyncio.wait_for(
                        self.delivery_wakeup.wait(),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Durable Telegram delivery loop failed")
                await asyncio.sleep(5.0)

    @staticmethod
    def _number(value, digits: int = 4) -> str:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            return "—"
        return f"{value:.{digits}f}"

    @classmethod
    def build_signal_message(cls, symbol: str, data: dict) -> str:
        metrics = data.get("metrics") or {}
        pos_setup = metrics.get("position_setup") or {}
        ai_data = metrics.get("ai_advisory") or {}
        dex_context = metrics.get("dex_context") or {}
        onchain_context = metrics.get("onchain_context") or {}
        microstructure = metrics.get("microstructure") or {}
        base_symbol = escape(symbol.split("/")[0])
        leverage = cls._number(metrics.get("applied_leverage"), 0)
        ai_advice = escape(str(ai_data.get("ai_advice", "UNKNOWN")))
        ai_confidence = cls._number(ai_data.get("ai_confidence"), 0)
        ai_reason = escape(
            str(ai_data.get("ai_reasoning", "No advisory available"))
        )

        lines = [
            "🚨 <b>WATERFALL SIGNAL — SIGNAL_ONLY ALERT</b>",
            f"🪙 <b>Symbol:</b> #{base_symbol}",
            f"📊 <b>Score:</b> {cls._number(data.get('score'), 2)}/100",
            f"⚖️ <b>Recommended leverage:</b> {leverage}×",
            "",
            f"🧠 <b>AI advisory ({escape(str(ai_data.get('ai_provider', 'none')))}):</b>",
            f"├ <b>Advice:</b> {ai_advice} ({ai_confidence}%)",
            f"└ <i>{ai_reason}</i>",
            "",
            "<b>Risk-managed setup</b>",
            f"🎯 Entry: <b>${cls._number(pos_setup.get('entry_price'), 8)}</b>",
            f"🛑 Stop: <b>${cls._number(pos_setup.get('stop_loss'), 8)}</b> ({cls._number(pos_setup.get('risk_pct'), 2)}%)",
            f"💰 TP1 / TP2: <b>${cls._number(pos_setup.get('take_profit_1'), 8)}</b> / <b>${cls._number(pos_setup.get('take_profit_2'), 8)}</b>",
            f"📐 Reward:risk: <b>{cls._number(pos_setup.get('reward_to_risk'), 2)}</b>",
            f"📚 Spread / slippage: <b>{cls._number(microstructure.get('spread_pct'), 3)}%</b> / <b>{cls._number(microstructure.get('slippage_pct'), 3)}%</b>",
        ]
        if dex_context:
            lines.append(
                f"🔗 DEX: {escape(str(dex_context.get('chain_id', '—')))} · liquidity ${cls._number(dex_context.get('liquidity_usd'), 0)}"
            )
        if onchain_context:
            lines.append(
                f"🐋 On-chain sample: {cls._number(onchain_context.get('large_transfer_sample_count'), 0)} large transfers · max ${cls._number(onchain_context.get('largest_transfer_usd'), 0)}"
            )
        lines.extend(
            ["", "<i>Triggered and logged. No live order is placed.</i>"]
        )
        return "\n".join(lines)

    @classmethod
    def build_entry_ready_message(cls, payload: dict) -> str:
        symbol = escape(str(payload.get("symbol") or "UNKNOWN").split("/")[0])
        packet = payload.get("decision_packet") if isinstance(payload.get("decision_packet"), dict) else {}
        plan = packet.get("trade_plan") if isinstance(packet.get("trade_plan"), dict) else {}
        leverage_advisory = (
            packet.get("leverage_advisory")
            if isinstance(packet.get("leverage_advisory"), dict)
            else {}
        )
        leverage_status = str(leverage_advisory.get("status") or "")
        raw_leverage = plan.get("leverage")
        leverage_value = (
            float(raw_leverage)
            if isinstance(raw_leverage, (int, float))
            and not isinstance(raw_leverage, bool)
            and math.isfinite(raw_leverage)
            else None
        )
        if leverage_status == "AVAILABLE" and leverage_value is not None:
            margin_mode = str(leverage_advisory.get("margin_mode") or plan.get("margin_mode") or "isolated")
            leverage_text = f"{leverage_value:.0f}× {margin_mode}"
        elif leverage_status in {"UNAVAILABLE", "NOT_RECOMMENDED"}:
            leverage_text = leverage_status.replace("_", " ")
        elif leverage_value is not None:
            leverage_text = f"{leverage_value:.0f}×"
        else:
            leverage_text = "UNAVAILABLE"
        evidence = packet.get("evidence_summary") if isinstance(packet.get("evidence_summary"), dict) else {}
        derivatives = evidence.get("derivatives") if isinstance(evidence.get("derivatives"), dict) else {}
        flow = evidence.get("order_flow") if isinstance(evidence.get("order_flow"), dict) else {}
        cascade = evidence.get("cascade") if isinstance(evidence.get("cascade"), dict) else {}
        reasons = [escape(str(item)) for item in packet.get("reason_codes", [])][:6]
        lines = [
            "🌊 <b>WATERFALL SHORT — ENTRY READY</b>",
            f"🪙 <b>#{symbol}</b> · readiness <b>{cls._number(packet.get('entry_readiness'), 1)}/100</b>",
            f"🧭 Lifecycle: <b>{escape(str(packet.get('lifecycle_state') or 'UNKNOWN'))}</b>",
            f"📦 Evidence coverage: <b>{cls._number(packet.get('evidence_coverage_pct'), 1)}%</b>",
            "",
            f"🎯 Entry: <b>${cls._number(plan.get('entry_price'), 8)}</b>",
            f"🛑 SL: <b>${cls._number(plan.get('stop_loss'), 8)}</b> · {escape(str(plan.get('stop_basis') or 'structural'))}",
            f"💰 TP1 / TP2 / TP3: <b>${cls._number(plan.get('take_profit_1'), 8)}</b> / <b>${cls._number(plan.get('take_profit_2'), 8)}</b> / <b>${cls._number(plan.get('take_profit_3'), 8)}</b>",
            f"⚖️ Leverage: <b>{escape(leverage_text)}</b>",
            "",
            f"📉 OI 1h: <b>{cls._number(derivatives.get('oi_change_1h_pct'), 3)}%</b> · Funding: <b>{cls._number(derivatives.get('funding_rate_pct'), 4)}%</b>",
            f"🔻 Taker B/S: <b>{cls._number(flow.get('taker_buy_sell_ratio'), 3)}</b> · Sell share: <b>{cls._number(flow.get('sell_share_pct'), 1)}%</b>",
            f"💥 Cascade: <b>{escape(str(cascade.get('status') or 'UNAVAILABLE'))}</b> · {cls._number(cascade.get('readiness_points'), 1)}/10",
            f"📚 Spread: <b>{cls._number((evidence.get('execution') or {}).get('spread_pct'), 3)}%</b> · LBank divergence: <b>{cls._number(plan.get('reference_divergence_pct'), 3)}%</b>",
        ]
        advisory = payload.get("ai_advisory") if isinstance(payload.get("ai_advisory"), dict) else {}
        if advisory.get("ai_status") == "AVAILABLE":
            answer_yes = advisory.get("ai_answer_yes")
            legacy_advice = str(advisory.get("ai_advice") or "")
            if answer_yes is True or legacy_advice in {"SHORT", "NEUTRAL", "SUPPORTS_SHORT"}:
                answer = "YES"
            elif answer_yes is False or legacy_advice in {"AVOID", "DOES_NOT_SUPPORT_SHORT"}:
                answer = "NO"
            else:
                answer = "—"
            lines.extend((
                "🧠 <b>Jev evidence check — observational only</b>",
                f"Q: {escape(str(advisory.get('ai_question') or 'Does the supplied evidence support a valid short setup?'))}",
                f"A: <b>{answer} — {escape(str(advisory.get('ai_advice') or 'UNAVAILABLE'))}</b>",
                f"Strength: <b>{cls._number(advisory.get('ai_confidence'), 0)}%</b> · {escape(str(advisory.get('ai_confidence_label') or 'Unlabelled'))}",
                f"Basis: {escape(str(advisory.get('ai_reasoning') or 'No canonical basis available'))}",
                f"Model: {escape(str(advisory.get('ai_provider') or 'typesafe'))} · {escape(str(advisory.get('ai_model') or 'unknown'))}",
            ))
        else:
            lines.append("🧠 <b>Jev evidence check unavailable</b> — engine decision unchanged")
        if reasons:
            lines.append("🧾 " + " · ".join(reasons))
        lines.extend(["", "<i>Signal only. No live order is placed.</i>"])
        return "\n".join(lines)

    async def start_interactive_bot(self):
        if not self.enabled:
            return

        # This notifier instance is module-scoped. Recreate the wake event for
        # every application lifespan so it is never bound to a previous loop.
        self.delivery_wakeup = asyncio.Event()

        # Proactive signal delivery is owned exclusively by the canonical
        # ENTRY_READY outbox worker in main.py.  Keep the legacy worker
        # implementation for historical/replay compatibility, but never
        # activate it from the interactive command bot.
        delivery_task = None

        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        logger.info("📡 Interactive Telegram Command Center Online.")
        logger.info(
            "Legacy TRIGGERED Telegram delivery is disabled; "
            "canonical signal delivery is managed by the runtime."
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    resp = await client.get(
                        url,
                        params={"offset": -1, "timeout": 5},
                    )
                    if resp.status_code == 200:
                        updates = resp.json().get("result", [])
                        if updates:
                            self.offset = updates[-1]["update_id"] + 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Telegram getUpdates bootstrap failed (%s): %.200s",
                        type(exc).__name__,
                        str(exc),
                    )

                while True:
                    try:
                        resp = await client.get(
                            url,
                            params={"offset": self.offset, "timeout": 20},
                        )

                        if resp.status_code == 429:
                            retry_after = float(
                                resp.headers.get("Retry-After", "30")
                            )
                            logger.warning(
                                "Telegram poll rate-limited; backing off %ss.",
                                retry_after,
                            )
                            await asyncio.sleep(retry_after)
                            continue

                        if resp.status_code == 200:
                            updates = resp.json().get("result", [])
                            for update in updates:
                                self.offset = update["update_id"] + 1
                                message = update.get("message", {})
                                chat = message.get("chat", {})
                                command_text = message.get("text", "")
                                if (
                                    str(chat.get("id")) == self.chat_id
                                    and command_text.startswith("/")
                                ):
                                    await self._process_command(command_text)
                        elif resp.status_code in (401, 403):
                            logger.error(
                                "Telegram polling rejected (HTTP %s): check "
                                "TELEGRAM_TOKEN/CHAT_ID.",
                                resp.status_code,
                            )
                            await asyncio.sleep(60)

                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "Telegram getUpdates poll failed (%s): %.200s",
                            type(exc).__name__,
                            str(exc),
                        )

                    await asyncio.sleep(1)
        finally:
            if delivery_task is not None:
                delivery_task.cancel()
                await asyncio.gather(
                    delivery_task,
                    return_exceptions=True,
                )

    async def _process_command(self, text: str):
        cmd = text.split("@")[0].lower().strip()

        if cmd == "/status":
            total = len(self.scanner.active_candidates) if self.scanner else 0
            await self.send_message(
                f"✅ <b>Waterfall Engine:</b> ONLINE\n🌊 <b>Live Targets Tracked:</b> {total}"
            )

        elif cmd == "/armed":
            if self.db is None:
                return
            candidates = self.db.get_all_active_candidates()
            armed = [
                s.split(":")[0]
                for s, d in candidates.items()
                if d["status"] == "ARMED"
            ]
            if armed:
                msg = (
                    f"🎯 <b>ARMED Targets ({len(armed)}):</b>\n"
                    + "\n".join([f"- #{s}" for s in armed])
                )
            else:
                msg = "🛡️ <b>No targets currently ARMED.</b>"
            await self.send_message(msg)

        elif cmd == "/ping":
            await self.send_message("🏓 <b>Pong!</b> Connection is stable.")


class TelegramSignalTransport:
    """Durable Telegram transport with explicit HTTP outcome classification."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        cutover_at: int | None = None,
        decision_db_path: str | Path | None = None,
        max_entry_age_seconds: int = 180,
        http_transport=None,
    ):
        self.token = str(token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        if not self.token or not self.chat_id:
            raise ValueError("Telegram token and chat id are required")
        self.cutover_at = (
            cutover_at
            if (
                isinstance(cutover_at, int)
                and not isinstance(cutover_at, bool)
                and cutover_at > 0
            )
            else None
        )
        self.decision_db_path = (
            str(Path(decision_db_path)) if decision_db_path is not None else None
        )
        if (
            isinstance(max_entry_age_seconds, bool)
            or not isinstance(max_entry_age_seconds, int)
            or max_entry_age_seconds < 1
        ):
            raise ValueError("max_entry_age_seconds must be a positive integer")
        self.max_entry_age_seconds = max_entry_age_seconds
        self.http_transport = http_transport

    def _current_entry_ready_is_deliverable(
        self, payload: dict[str, Any], *, now: int
    ) -> tuple[bool, str | None]:
        packet = payload.get("decision_packet")
        if not isinstance(packet, dict):
            return False, "INVALID_DECISION_PACKET"
        event_at = packet.get("evaluated_at")
        if isinstance(event_at, bool) or not isinstance(event_at, int) or event_at < 0:
            return False, "INVALID_EVENT_TIMESTAMP"
        if event_at > now:
            return False, "FUTURE_DATED_SIGNAL"
        expires_at = (
            packet.get("trade_plan", {}).get("expires_at")
            if isinstance(packet.get("trade_plan"), dict)
            else None
        )
        if (
            isinstance(expires_at, int)
            and not isinstance(expires_at, bool)
            and expires_at >= 0
            and now >= expires_at
        ):
            return False, "SIGNAL_EXPIRED"
        if now - event_at > self.max_entry_age_seconds:
            return False, "SIGNAL_STALE"
        if self.decision_db_path is None:
            return True, None
        decision_event_id = payload.get("decision_event_id")
        symbol = str(payload.get("symbol") or "").strip()
        if (
            isinstance(decision_event_id, bool)
            or not isinstance(decision_event_id, int)
            or decision_event_id <= 0
            or not symbol
        ):
            return False, "INVALID_DECISION_IDENTITY"
        try:
            db_path = Path(self.decision_db_path).resolve()
            with closing(
                sqlite3.connect(
                    f"{db_path.as_uri()}?mode=ro", uri=True, timeout=5.0
                )
            ) as conn:
                row = conn.execute(
                    "SELECT id, decision FROM entry_decision_events "
                    "WHERE symbol=? ORDER BY id DESC LIMIT 1",
                    (symbol,),
                ).fetchone()
        except (OSError, sqlite3.Error):
            return False, "DECISION_STATE_UNAVAILABLE"
        if row is None:
            return False, "DECISION_NOT_FOUND"
        latest_event_id, latest_decision = int(row[0]), str(row[1] or "")
        if (
            latest_event_id != decision_event_id
            or latest_decision != "ENTRY_READY"
        ):
            return False, "SUPERSEDED_DECISION"
        return True, None

    def _load_advisory_for_decision(self, decision_event_id: int) -> dict[str, Any] | None:
        if self.decision_db_path is None:
            return None
        try:
            db_path = Path(self.decision_db_path).resolve()
            with closing(
                sqlite3.connect(
                    f"{db_path.as_uri()}?mode=ro", uri=True, timeout=5.0
                )
            ) as conn:
                row = conn.execute(
                    "SELECT advisory_json, advisory_hash "
                    "FROM entry_decision_advisories "
                    "WHERE decision_event_id=? ORDER BY id DESC LIMIT 1",
                    (decision_event_id,),
                ).fetchone()
        except (OSError, sqlite3.Error):
            return None
        if row is None:
            return None
        try:
            advisory = json.loads(str(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(advisory, dict):
            return None
        if str(row[1] or "") != canonical_sha256(advisory):
            return None
        return advisory

    async def deliver(self, event: dict) -> DeliveryResult:
        try:
            payload = json.loads(str(event.get("payload_json") or "{}"))
        except json.JSONDecodeError:
            return DeliveryResult(DeliveryDisposition.PERMANENT_FAILURE, "INVALID_PAYLOAD_JSON")
        if payload.get("contract_version") != "entry_ready_notification_v1":
            return DeliveryResult(DeliveryDisposition.PERMANENT_FAILURE, "UNSUPPORTED_PAYLOAD")
        expected_hash = event.get("payload_hash")
        if expected_hash is not None and str(expected_hash) != canonical_sha256(payload):
            return DeliveryResult(
                DeliveryDisposition.PERMANENT_FAILURE, "PAYLOAD_HASH_MISMATCH"
            )

        packet = payload.get("decision_packet")
        event_at = packet.get("evaluated_at") if isinstance(packet, dict) else None
        now = int(time.time())
        deliverable, suppression_reason = await asyncio.to_thread(
            self._current_entry_ready_is_deliverable, payload, now=now
        )
        if not deliverable:
            if suppression_reason == "DECISION_STATE_UNAVAILABLE":
                return DeliveryResult(
                    DeliveryDisposition.TRANSIENT_FAILURE, suppression_reason
                )
            logger.info(
                "Suppressing ENTRY_READY event %s: %s",
                event.get("event_id"), suppression_reason,
            )
            return DeliveryResult(DeliveryDisposition.DELIVERED)
        if self.cutover_at is not None and event_at < self.cutover_at:
            logger.info(
                "Suppressing pre-cutover ENTRY_READY event %s "
                "(evaluated_at=%s cutover_at=%s)",
                event.get("event_id"), event_at, self.cutover_at,
            )
            return DeliveryResult(DeliveryDisposition.DELIVERED)

        decision_event_id = payload.get("decision_event_id")
        advisory = (
            await asyncio.to_thread(self._load_advisory_for_decision, decision_event_id)
            if isinstance(decision_event_id, int)
            and not isinstance(decision_event_id, bool)
            and decision_event_id > 0
            else None
        )
        payload["ai_advisory"] = advisory or {
            "ai_status": "UNAVAILABLE",
            "ai_provider": "none",
            "ai_advice": "UNAVAILABLE",
            "ai_confidence": 0,
        }
        text = TelegramNotifier.build_entry_ready_message(payload)
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self.http_transport) as client:
                response = await client.post(url, json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"})
        except httpx.ConnectTimeout:
            return DeliveryResult(
                DeliveryDisposition.TRANSIENT_FAILURE,
                "TELEGRAM_CONNECT_TIMEOUT",
            )
        except httpx.PoolTimeout:
            return DeliveryResult(
                DeliveryDisposition.TRANSIENT_FAILURE,
                "TELEGRAM_POOL_TIMEOUT",
            )
        except (httpx.ReadTimeout, httpx.WriteTimeout, TimeoutError):
            return DeliveryResult(
                DeliveryDisposition.DELIVERY_UNCERTAIN,
                "TELEGRAM_READ_TIMEOUT_AFTER_SEND_MAY_HAVE_STARTED",
            )
        except httpx.TimeoutException:
            return DeliveryResult(
                DeliveryDisposition.DELIVERY_UNCERTAIN,
                "TELEGRAM_TIMEOUT_AFTER_SEND_MAY_HAVE_STARTED",
            )
        except httpx.HTTPError:
            return DeliveryResult(DeliveryDisposition.TRANSIENT_FAILURE, "TELEGRAM_HTTP_ERROR")
        if 200 <= response.status_code < 300:
            try:
                response_payload = response.json()
            except (ValueError, json.JSONDecodeError):
                response_payload = None
            if isinstance(response_payload, dict) and response_payload.get("ok") is True:
                return DeliveryResult(DeliveryDisposition.DELIVERED)
            return DeliveryResult(
                DeliveryDisposition.TRANSIENT_FAILURE,
                "INVALID_TELEGRAM_RESPONSE",
            )
        if response.status_code == 429:
            retry_after = None
            try:
                retry_after = int((response.json().get("parameters") or {}).get("retry_after"))
            except (TypeError, ValueError, json.JSONDecodeError):
                retry_after = None
            return DeliveryResult(DeliveryDisposition.RATE_LIMITED, "HTTP_429", retry_after)
        if response.status_code in {400, 401, 403, 404}:
            return DeliveryResult(DeliveryDisposition.PERMANENT_FAILURE, f"HTTP_{response.status_code}")
        return DeliveryResult(DeliveryDisposition.TRANSIENT_FAILURE, f"HTTP_{response.status_code}")

    async def probe(self) -> dict:
        bot_url = f"https://api.telegram.org/bot{self.token}/getMe"
        chat_url = f"https://api.telegram.org/bot{self.token}/getChat"
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self.http_transport) as client:
                bot_response = await client.get(bot_url)
                try:
                    bot_payload = bot_response.json()
                except (ValueError, json.JSONDecodeError):
                    bot_payload = None
                bot_reachable = bool(
                    bot_response.status_code == 200
                    and isinstance(bot_payload, dict)
                    and bot_payload.get("ok") is True
                )
                if not bot_reachable:
                    return {
                        "configured": True,
                        "reachable": False,
                        "bot_reachable": False,
                        "chat_reachable": False,
                        "status_code": bot_response.status_code,
                        "bot_status_code": bot_response.status_code,
                        "chat_status_code": None,
                    }

                chat_response = await client.get(
                    chat_url,
                    params={"chat_id": self.chat_id},
                )
                try:
                    chat_payload = chat_response.json()
                except (ValueError, json.JSONDecodeError):
                    chat_payload = None
                chat_reachable = bool(
                    chat_response.status_code == 200
                    and isinstance(chat_payload, dict)
                    and chat_payload.get("ok") is True
                )
                return {
                    "configured": True,
                    "reachable": bool(bot_reachable and chat_reachable),
                    "bot_reachable": bot_reachable,
                    "chat_reachable": chat_reachable,
                    "status_code": chat_response.status_code,
                    "bot_status_code": bot_response.status_code,
                    "chat_status_code": chat_response.status_code,
                }
        except httpx.HTTPError:
            return {
                "configured": True,
                "reachable": False,
                "bot_reachable": False,
                "chat_reachable": False,
                "status_code": None,
                "bot_status_code": None,
                "chat_status_code": None,
            }
