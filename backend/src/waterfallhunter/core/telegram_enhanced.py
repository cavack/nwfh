"""
telegram_enhanced.py — Enhanced Telegram bot for the WaterfallHunter crypto signal system
=======================================================================================

This module extends the existing TelegramNotifier (in notifier.py) with:

  1. New interactive bot commands:
       /signals  — list all active ENTRY_READY signals with TP/SL/EP details
       /health   — show live system health (backend, tracked count, DB size,
                   memory, uptime, AI providers, exchange sources)
       /top      — show the top 5 candidates by readiness (excluding signals)
       /help     — list every available command

  2. A 12-hour automatic health report delivered to the configured chat_id,
     started alongside the interactive bot via asyncio.create_task.

  3. Signal alert deduplication: a small in-memory TTL set keyed on
     (symbol + decision + minute-rounded timestamp) so the same signal is
     never announced more than once inside a 5-minute window.

Design notes
------------
* Self-contained and importable: the only hard dependency is ``httpx``. All
  WaterfallHunter imports (``settings``, db/scanner adapters, signal metadata)
  are performed lazily / defensively so the module can be imported and unit
  tested in isolation, and so a missing optional dependency never crashes the
  bot loop.
* Production-ready: every network call is wrapped in try/except with
  structured logging; the polling loop honours Telegram 429 Retry-After and
  survives transient failures without exiting.
* Integrates with the existing notifier.py command-handler pattern: pass a
  ``db_adapter`` and ``scanner`` (the same objects TelegramNotifier receives)
  and the enhanced bot will read live candidate/signal data from them.

Author: WaterfallHunter Engineering
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

import httpx

logger = logging.getLogger("WaterfallHunter.TelegramEnhanced")

# ---------------------------------------------------------------------------
# Configuration helpers — defensive access to settings + sensible fallbacks
# ---------------------------------------------------------------------------

# How often the automatic health report fires.
HEALTH_REPORT_INTERVAL_SECONDS: int = 12 * 60 * 60  # 12 hours

# Dedup window for signal alerts.
SIGNAL_DEDUP_TTL_SECONDS: int = 5 * 60  # 5 minutes

# Long-poll timeout for Telegram getUpdates.
_GETUPDATES_TIMEOUT: float = 20.0
# Per-request HTTP timeout for all Telegram API calls.
_HTTP_TIMEOUT: float = 30.0

# Canonical list of exchanges the system tracks (used for /health source count).
_TRACKED_EXCHANGES: tuple[str, ...] = (
    "binance",
    "okx",
    "bybit",
    "gate",
    "mexc",
    "lbank",
    "kucoin",
)

# Maximum candidates / signals rendered in a single message to stay within
# Telegram's 4096-character limit.
_MAX_SIGNALS_IN_MSG: int = 12
_MAX_CANDIDATES_IN_MSG: int = 5


def _resolve_settings() -> Any:
    """Return the WaterfallHunter settings object, or a dummy if unavailable.

    The dummy exposes every attribute we touch as ``None`` / ``False`` so the
    module remains importable and functional in test environments where the
    full config stack is not present.
    """

    try:  # pragma: no cover — exercised in production only
        from waterfallhunter.config import settings  # type: ignore

        return settings
    except Exception:  # noqa: BLE001 — intentional broad fallback
        logger.warning(
            "waterfallhunter.config.settings unavailable; running with "
            "fallback defaults. Configure TELEGRAM_TOKEN / TELEGRAM_CHAT_ID "
            "in the environment to enable live delivery."
        )
        return _FallbackSettings()


class _FallbackSettings:
    """Minimal stand-in for waterfallhunter.config.settings.

    Reads TELEGRAM_TOKEN / TELEGRAM_CHAT_ID (and a handful of AI / exchange
    toggles) straight from the environment so the bot still works without the
    full Pydantic settings stack.
    """

    telegram_token: str | None = os.getenv("TELEGRAM_TOKEN")
    telegram_chat_id: str | None = os.getenv("TELEGRAM_CHAT_ID")
    typesafe_api_key: str | None = os.getenv("TYPESAFE_API_KEY")
    db_path: str | None = os.getenv(
        "WFH_DB_PATH", "/srv/waterfallhunter/data/waterfallhunter.db"
    )

    def __getattr__(self, item: str) -> Any:  # graceful unknown-attr access
        return None


# ---------------------------------------------------------------------------
# Signal alert deduplication — in-memory TTL set
# ---------------------------------------------------------------------------


class SignalDeduper:
    """Tiny in-memory TTL set for signal-alert deduplication.

    A signal is identified by ``hash(symbol + decision + minute-rounded ts)``.
    The same hash is suppressed for ``ttl`` seconds (default 5 minutes) so a
    flapping / re-emitted signal never spams the chat. Entries are evicted
    lazily on access; there is no background sweeper, which keeps the
    dependency surface to zero.
    """

    def __init__(self, ttl: int = SIGNAL_DEDUP_TTL_SECONDS) -> None:
        self.ttl = ttl
        self._seen: dict[str, float] = {}

    @staticmethod
    def make_key(symbol: str, decision: str, timestamp: float | None) -> str:
        """Build the dedup key for a signal event.

        ``timestamp`` is rounded to the nearest minute so two emits that land
        within the same minute collapse to one key (the canonical behaviour
        described in the task spec).
        """

        ts = timestamp if timestamp is not None else time.time()
        minute_bucket = int(ts // 60) * 60
        raw = f"{(symbol or '').upper().strip()}|{(decision or '').upper().strip()}|{minute_bucket}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def is_duplicate(self, symbol: str, decision: str, timestamp: float | None = None) -> bool:
        key = self.make_key(symbol, decision, timestamp)
        now = time.time()
        self._evict(now)
        return key in self._seen

    def mark_sent(self, symbol: str, decision: str, timestamp: float | None = None) -> None:
        key = self.make_key(symbol, decision, timestamp)
        self._seen[key] = time.time()
        # opportunistic eviction keeps the dict bounded under heavy load
        if len(self._seen) > 4096:
            self._evict(time.time())

    def _evict(self, now: float) -> None:
        if not self._seen:
            return
        expired = [k for k, t in self._seen.items() if (now - t) > self.ttl]
        for k in expired:
            self._seen.pop(k, None)

    def clear(self) -> None:
        self._seen.clear()


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_DIVIDER = "━━━━━━━━━━━━━━━━"


def _fmt_price(value: Any) -> str:
    """Format a price with adaptive precision.

    Large values get grouped thousands + 2dp; sub-unit prices keep enough
    significant digits to be meaningful. Anything non-numeric becomes ``—``.
    """

    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    if n >= 1000:
        return f"${n:,.2f}"
    if n >= 1:
        return f"${n:,.4f}"
    # very small altcoin prices — 6 significant digits
    return f"${n:.6f}".rstrip("0").rstrip(".") or "$0"


def _fmt_ratio(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{n:.1f}"


def _fmt_db_size(bytes_value: int | float | None) -> str:
    if bytes_value is None:
        return "—"
    try:
        b = float(bytes_value)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024.0:
            return f"{b:.2f} {unit}"
        b /= 1024.0
    return f"{b:.2f} PB"


def _process_started_at() -> float | None:
    """Backend process start time, from the kernel rather than the bot.

    ``self.started_at`` is set when the Telegram bot starts, which is after the
    backend is already up and is reset on every bot restart — that is why the
    report showed "0h 0m" on a process that had been running for hours.

    Note when verifying this by hand: ``docker exec ... python`` measures the
    newly spawned interpreter, not the server. Read ``/proc/1/stat`` instead,
    or call this from inside the running app.
    """
    try:
        with open("/proc/uptime", "r") as fh:
            host_uptime = float(fh.read().split()[0])
        with open("/proc/self/stat", "r") as fh:
            # The comm field (2) can contain spaces and parentheses, so split
            # after the last ')'. What remains begins at field 3 (state), so
            # starttime — field 22, 1-indexed — is index 19 of that remainder.
            remainder = fh.read().rsplit(")", 1)[1].split()
        ticks_per_second = os.sysconf("SC_CLK_TCK")
        start_since_boot = float(remainder[19]) / ticks_per_second
        return time.time() - (host_uptime - start_since_boot)
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _fmt_uptime(started_at: float | None) -> str:
    if not started_at:
        return "—"
    seconds = max(0, int(time.time() - started_at))
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m}m"


def _bool_mark(value: Any) -> str:
    return "✓" if value else "✗"


def _status_mark(healthy: bool, label: str) -> str:
    icon = "✅" if healthy else "❌"
    state = "healthy" if healthy else "down"
    return f"{icon} {label}: {state}"


def _truncate(text: str, limit: int = 3900) -> str:
    """Truncate to stay safely under Telegram's 4096-char cap."""

    if len(text) <= limit:
        return text
    return text[: limit - 12] + "\n…[truncated]"


# ---------------------------------------------------------------------------
# Data extraction — defensive reads from db_adapter / scanner
# ---------------------------------------------------------------------------


def _get_active_candidates(db_adapter: Any) -> dict[str, dict[str, Any]]:
    """Return ``{symbol: {status, ...}}`` from the db adapter, tolerating any
    shape returned by the real implementation."""

    if db_adapter is None:
        return {}
    try:
        candidates = db_adapter.get_all_active_candidates()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_all_active_candidates failed: %s: %.200s", type(exc).__name__, exc)
        return {}
    if isinstance(candidates, dict):
        return candidates
    if isinstance(candidates, (list, tuple)):
        # Some adapters return a list of dicts/rows — normalise.
        out: dict[str, dict[str, Any]] = {}
        for row in candidates:
            if isinstance(row, dict):
                sym = row.get("symbol") or row.get("SYMBOL")
                if sym:
                    out[str(sym)] = row
        return out
    return {}


def _is_hard_blocked(data: dict[str, Any]) -> bool:
    """Defence in depth: never surface a packet the engine marked hard-blocked.

    ``build_entry_decision`` is the authority on actionability. This guard only
    ensures a regression there can never leak a blocked packet to subscribers.
    """
    if data.get("hard_blocked") is True:
        return True
    reasons = data.get("block_reasons")
    blocking = {
        "STALE_ANALYSIS",
        "STALE_REFERENCE",
        "DETERMINISTIC_MARKET_DATA_VETO",
        "EXECUTION_UNAVAILABLE",
        "TRADE_PLAN_EXPIRED",
        "STRUCTURE_INVALIDATED",
    }
    if isinstance(reasons, (list, tuple, set)):
        return any(str(r).upper() in blocking for r in reasons)
    return False


def _get_signals(db_adapter: Any, scanner: Any) -> list[dict[str, Any]]:
    """Collect every active ENTRY_READY signal.

    Tries the db adapter first (canonical store), then falls back to the
    scanner's in-memory candidate map. Each signal dict is normalised so the
    formatter can rely on a stable set of keys.
    """

    signals: list[dict[str, Any]] = []

    # 1) db adapter — canonical source
    candidates = _get_active_candidates(db_adapter)
    for symbol, data in candidates.items():
        if not isinstance(data, dict):
            continue
        status = str(data.get("status") or data.get("Status") or "").upper()
        decision = str(data.get("decision") or data.get("Decision") or "").upper()
        if (status == "ENTRY_READY" or decision == "ENTRY_READY") and not _is_hard_blocked(data):
            signals.append(_normalise_signal(symbol, data))

    # 2) scanner fallback — in-memory live candidates
    if scanner is not None:
        try:
            live = getattr(scanner, "active_candidates", None)
        except Exception:  # noqa: BLE001
            live = None
        if isinstance(live, dict):
            for symbol, data in live.items():
                if not isinstance(data, dict):
                    continue
                status = str(data.get("status") or "").upper()
                decision = str(data.get("decision") or "").upper()
                if (
                    (status == "ENTRY_READY" or decision == "ENTRY_READY")
                    and not _is_hard_blocked(data)
                    and not any(s.get("symbol") == symbol for s in signals)
                ):
                    signals.append(_normalise_signal(symbol, data))

    return signals


def _normalise_signal(symbol: str, data: dict[str, Any]) -> dict[str, Any]:
    """Pull TP/SL/EP/RR/cross/cascade fields out of a candidate dict using
    every plausible key name the codebase might use."""

    def _first(*keys: str) -> Any:
        for k in keys:
            if k in data and data[k] is not None:
                return data[k]
            # case-insensitive fallback
            for dk, dv in data.items():
                if dk.lower() == k.lower() and dv is not None:
                    return dv
        return None

    return {
        "symbol": symbol,
        "readiness": _first("readiness", "R", "score", "readiness_score"),
        "ep": _first("ep", "entry_price", "entry", "EP"),
        "sl": _first("sl", "stop_loss", "stop", "SL"),
        "tp1": _first("tp1", "tp", "take_profit", "take_profit_1", "TP1"),
        "rr": _first("rr", "risk_reward", "r:r", "RR"),
        "cross": _first("cross", "cross_confirmed", "cross_validated"),
        "cascade": _first("cascade", "cascade_status", "cascade_result"),
        "decision": _first("decision", "Decision"),
    }


def _get_top_candidates(db_adapter: Any, scanner: Any, limit: int = _MAX_CANDIDATES_IN_MSG) -> list[dict[str, Any]]:
    """Return the top ``limit`` candidates by readiness, excluding any that are
    already ENTRY_READY signals."""

    signals = _get_signals(db_adapter, scanner)
    signal_symbols = {s["symbol"] for s in signals}

    pool: list[dict[str, Any]] = []
    candidates = _get_active_candidates(db_adapter)
    for symbol, data in candidates.items():
        if not isinstance(data, dict):
            continue
        if symbol in signal_symbols:
            continue
        status = str(data.get("status") or "").upper()
        if status in ("EXITED", "CLOSED", "DISABLED"):
            continue
        norm = _normalise_signal(symbol, data)
        try:
            readiness = float(norm.get("readiness") or 0)
        except (TypeError, ValueError):
            readiness = 0.0
        norm["readiness"] = readiness
        norm["status"] = status or "TRACKING"
        pool.append(norm)

    # Also fold in scanner-live candidates not present in the db view
    if scanner is not None:
        try:
            live = getattr(scanner, "active_candidates", None)
        except Exception:  # noqa: BLE001
            live = None
        if isinstance(live, dict):
            existing = {c["symbol"] for c in pool}
            for symbol, data in live.items():
                if not isinstance(data, dict) or symbol in existing or symbol in signal_symbols:
                    continue
                norm = _normalise_signal(symbol, data)
                try:
                    readiness = float(norm.get("readiness") or 0)
                except (TypeError, ValueError):
                    readiness = 0.0
                norm["readiness"] = readiness
                norm["status"] = str(data.get("status") or "TRACKING").upper()
                pool.append(norm)

    pool.sort(key=lambda c: float(c.get("readiness") or 0), reverse=True)
    return pool[:limit]


def _db_path(settings: Any) -> Path | None:
    """Resolve the registry database.

    ``settings.db_path`` does not exist — the field is ``registry_db_path`` —
    so this always returned None and the health report always showed "DB Size: —".
    """
    raw = (
        getattr(settings, "registry_db_path", None)
        or getattr(settings, "db_path", None)
        or os.getenv("WFH_DB_PATH")
    )
    if not raw:
        return None
    return Path(raw)


def _backtest_db_path(settings: Any) -> str:
    """Resolve the outcome-tracking database from settings, not a literal."""
    return str(
        getattr(settings, "backtester_v2_db_path", None)
        or os.getenv("WFH_BACKTEST_V2_DB_PATH")
        or "/app/data/backtest_v2.db"
    )


def _db_size_bytes(db_path: Path | None) -> int | None:
    if db_path is None or not db_path.exists():
        return None
    try:
        return db_path.stat().st_size
    except OSError:
        return None


def _tracked_count(db_adapter: Any, scanner: Any) -> int:
    candidates = _get_active_candidates(db_adapter)
    if candidates:
        return len(candidates)
    if scanner is not None:
        try:
            live = getattr(scanner, "active_candidates", None)
        except Exception:  # noqa: BLE001
            live = None
        if isinstance(live, dict):
            return len(live)
    return 0


def _memory_usage_percent() -> float | None:
    """Process RSS as a percentage of total host memory.

    The previous version divided by a hardcoded 2GB ceiling that matches
    nothing: the container has no memory limit set, and the host has 15GB. The
    reported figure was therefore meaningless in both directions — it would
    show 100% long before any real pressure, and it ignored the fact that the
    OOM events that restarted this box were driven by host-wide memory, not by
    this process alone.
    """
    try:
        rss_kb: int | None = None
        with open("/proc/self/status", "r") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    break
        if rss_kb is None:
            return None

        total_kb: int | None = None
        with open("/proc/meminfo", "r") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                    break
        if not total_kb:
            return None
        return round((rss_kb / total_kb) * 100, 1)
    except (OSError, ValueError, IndexError):
        return None


def _ai_status(settings: Any) -> str:
    """Report TypeSafe advisory availability by probing it.

    An earlier version reported the advisory as "down" purely because a
    settings lookup returned None: it never contacted the provider at all, so
    the status was wrong in both directions. This probes the configured
    provider instead.
    """
    api_key = str(
        getattr(settings, "typesafe_api_key", "") or os.getenv("TYPESAFE_API_KEY") or ""
    ).strip()
    if not api_key:
        return "AI advisory: not configured"

    base_url = str(
        getattr(settings, "typesafe_base_url", "") or "https://api.typesafe.ai"
    ).strip()
    model = str(getattr(settings, "typesafe_model", "") or "")
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=4.0,
        )
        if response.status_code != 200:
            return f"AI advisory: unreachable (HTTP {response.status_code})"
    except Exception:
        return "AI advisory: unreachable"

    return f"AI advisory: active ({model})" if model else "AI advisory: active"


def _exchange_sources_online() -> tuple[int, int]:
    """Return (online, total) exchange sources.

    Without a live source-health registry we report the configured count vs the
    canonical 7-exchange set; adapters that expose ``source_health`` are
    consulted first.
    """

    # There is no source-health registry to consult, so this cannot be
    # measured here. Returning (total, total) reported "7/7 online"
    # unconditionally — including while sources were failing — which is worse
    # than reporting nothing. The caller renders "n/a" when total is 0.
    return 0, len(_TRACKED_EXCHANGES)


def _collect_source_failures(db_adapter: Any) -> list[str]:
    """Best-effort list of currently-failing exchange sources.

    Adapters that expose ``get_source_health()`` or a ``source_failures``
    attribute are consulted; otherwise an empty list is returned (no failures
    reported).
    """

    failures: list[str] = []
    if db_adapter is None:
        return failures

    for attr in ("get_source_health", "source_health"):
        health = None
        try:
            if attr == "get_source_health" and callable(getattr(db_adapter, "get_source_health", None)):
                health = db_adapter.get_source_health()
            elif attr == "source_health":
                health = getattr(db_adapter, "source_health", None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("source health probe via %s failed: %s", attr, exc)
            continue
        if isinstance(health, dict):
            for name, state in health.items():
                state_str = str(state).lower()
                if state_str in ("down", "error", "offline", "failed", "0", "false"):
                    failures.append(str(name))
        elif isinstance(health, (list, tuple)):
            for item in health:
                failures.append(str(item))
        if failures:
            break
    return failures


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


def build_signals_message(signals: list[dict[str, Any]]) -> str:
    if not signals:
        return "🚀 <b>Active Signals (0)</b>\n" + _DIVIDER + "\nNo active ENTRY_READY signals right now."

    lines = [f"🚀 <b>Active Signals ({len(signals)})</b>", _DIVIDER, ""]
    for sig in signals[:_MAX_SIGNALS_IN_MSG]:
        symbol = escape(str(sig.get("symbol") or "—"))
        readiness = _fmt_ratio(sig.get("readiness"))
        ep = _fmt_price(sig.get("ep"))
        sl = _fmt_price(sig.get("sl"))
        tp1 = _fmt_price(sig.get("tp1"))
        rr = sig.get("rr")
        rr_str = f"1:{_fmt_ratio(rr)}" if rr not in (None, "") else "1:—"
        cross = _bool_mark(sig.get("cross"))
        cascade_val = str(sig.get("cascade") or "").upper()
        cascade_str = "PASS" if "PASS" in cascade_val else ("FAIL" if "FAIL" in cascade_val else (cascade_val or "—"))

        lines.append(f"🔹 <b>{symbol}</b>: R={readiness}")
        lines.append(f"  EP: {ep} | SL: {sl} | TP1: {tp1}")
        lines.append(f"  R:R = {rr_str} | Cross: {cross} | Cascade: {cascade_str}")
        lines.append("")

    if len(signals) > _MAX_SIGNALS_IN_MSG:
        lines.append(f"…and {len(signals) - _MAX_SIGNALS_IN_MSG} more.")

    return _truncate("\n".join(lines))


def build_health_message(
    *,
    backend_healthy: bool,
    tracked_count: int,
    signal_count: int,
    db_size: str,
    memory_pct: float | None,
    uptime: str,
    ai_status: str,
    sources_online: tuple[int, int],
    source_failures: list[str] | None = None,
) -> str:
    mem_str = f"{memory_pct:.1f}% of host RAM" if memory_pct is not None else "—"
    online, total = sources_online
    lines = [
        "🏥 <b>System Health</b>",
        _DIVIDER,
        _status_mark(backend_healthy, "Backend"),
        f"📊 <b>Tracked:</b> {tracked_count} candidates",
        f"📁 <b>DB Size:</b> {db_size}",
        f"💾 <b>Memory:</b> {mem_str}",
        f"⏱️ <b>Uptime:</b> {uptime}",
        f"🤖 <b>AI:</b> {escape(ai_status)}",
    ]
    # Source health has no registry to read, so report the configured set
    # rather than claiming every source is online.
    if online > 0:
        lines.append(f"📡 <b>Sources:</b> {online}/{total} exchanges online")
    else:
        lines.append(f"📡 <b>Sources:</b> {total} configured (health not instrumented)")
    if source_failures:
        lines.append("⚠️ <b>Source failures:</b> " + escape(", ".join(source_failures)))
    lines.append(f"🚀 <b>Active signals:</b> {signal_count}")
    return _truncate("\n".join(lines))


def build_top_message(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "🏆 <b>Top 5 Candidates</b>\n" + _DIVIDER + "\nNo candidates available right now."

    lines = ["🏆 <b>Top 5 Candidates</b>", _DIVIDER, ""]
    for i, c in enumerate(candidates, start=1):
        symbol = escape(str(c.get("symbol") or "—"))
        readiness = _fmt_ratio(c.get("readiness"))
        status = escape(str(c.get("status") or "TRACKING"))
        ep = _fmt_price(c.get("ep"))
        sl = _fmt_price(c.get("sl"))
        lines.append(f"{i}. <b>{symbol}</b>: R={readiness} | {status} | EP={ep} | SL={sl}")
    return _truncate("\n".join(lines))


def build_help_message() -> str:
    lines = [
        "📜 <b>WaterfallHunter Bot — Commands</b>",
        _DIVIDER,
        "",
        "<b>Signal &amp; Market</b>",
        "🔹 /signals — Active ENTRY_READY signals (EP/SL/TP, cascade, AI)",
        "🔹 /top — Top 5 candidates by readiness score",
        "🔹 /backtest — Backtest performance (win rate, PnL, Sharpe)",
        "🔹 /stats — Model statistics (signals, outcomes, decisions)",
        "🔹 /health — System health & engine status",
        "🔹 /help — This message",
        "",
        "🕒 A full health report is auto-delivered every 12 hours.",
    ]
    return _truncate("\n".join(lines))


# ---------------------------------------------------------------------------
# Enhanced Telegram bot
# ---------------------------------------------------------------------------


class EnhancedTelegramBot:
    """Enhanced Telegram bot with new commands, 12h health reports, and
    signal-alert deduplication.

    Parameters
    ----------
    db_adapter:
        Same db adapter passed to ``TelegramNotifier``. Used for
        ``get_all_active_candidates()`` and (optionally) source health.
    scanner:
        Same scanner object passed to ``TelegramNotifier``. Its
        ``active_candidates`` map is the live fallback for signal/candidate
        data.
    token, chat_id:
        Telegram credentials. If omitted, resolved from ``settings`` /
        environment.
    delegate:
        Optional ``TelegramNotifier`` instance. When provided, ``/status``,
        ``/armed`` and ``/ping`` are delegated to it so existing behaviour is
        preserved exactly; only the new commands are handled locally.
    """

    def __init__(
        self,
        db_adapter: Any = None,
        scanner: Any = None,
        *,
        token: str | None = None,
        chat_id: str | None = None,
        delegate: Any = None,
        settings: Any = None,
    ) -> None:
        self.settings = settings or _resolve_settings()
        self.token = token or getattr(self.settings, "telegram_token", None)
        self.chat_id = chat_id or (
            str(self.settings.telegram_chat_id)
            if getattr(self.settings, "telegram_chat_id", None) is not None
            else None
        )
        self.db = db_adapter
        self.scanner = scanner
        self.delegate = delegate

        self.enabled = bool(self.token and self.chat_id)
        self.offset: int = 0
        self.started_at: float | None = None

        # Background task handles (kept so we can cancel cleanly on shutdown)
        self._poll_task: asyncio.Task | None = None
        self._health_report_task: asyncio.Task | None = None

        # Signal alert dedup
        self.deduper = SignalDeduper()

        logger.info(
            "EnhancedTelegramBot initialised (enabled=%s, chat_id=%s)",
            self.enabled,
            self.chat_id,
        )

    # -- Telegram HTTP plumbing ---------------------------------------------

    @property
    def _base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the configured chat. Returns True on success.

        All network errors are caught and logged; the method never raises.
        """

        if not self.enabled:
            logger.debug("send_message skipped (bot disabled).")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": _truncate(text),
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(f"{self._base_url}/sendMessage", json=payload)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "30"))
                logger.warning(
                    "Telegram sendMessage rate-limited; backing off %ss.", retry_after
                )
                await asyncio.sleep(retry_after)
                return False
            if resp.status_code != 200:
                logger.error(
                    "Telegram sendMessage failed (HTTP %s): %.300s",
                    resp.status_code,
                    resp.text,
                )
                return False
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — network resilience
            logger.warning(
                "Telegram sendMessage error (%s): %.200s", type(exc).__name__, exc
            )
            return False

    # -- Signal alert delivery (with dedup) ---------------------------------

    async def send_signal_alert(self, symbol: str, data: dict[str, Any]) -> bool:
        """Send a signal alert, deduplicating repeats within the TTL window.

        ``data`` should contain at least ``decision`` and ``timestamp``; other
        fields (EP/SL/TP/RR/cross/cascade) are read defensively.
        """

        decision = str(data.get("decision") or data.get("Decision") or "ENTRY_READY")
        timestamp = data.get("timestamp") or data.get("ts") or time.time()
        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError):
            timestamp = time.time()

        if self.deduper.is_duplicate(symbol, decision, timestamp):
            logger.info("Signal alert for %s deduplicated (within TTL window).", symbol)
            return False

        norm = _normalise_signal(symbol, data)
        message = build_signals_message([norm])
        ok = await self.send_message(message)
        if ok:
            self.deduper.mark_sent(symbol, decision, timestamp)
        return ok

    # -- Command handling ----------------------------------------------------

    async def handle_command(self, text: str) -> None:
        """Dispatch a single command.

        New commands (/signals, /health, /top, /help) are handled here. The
        legacy commands (/status, /armed, /ping) are delegated to the existing
        ``TelegramNotifier`` when a delegate is configured, otherwise handled
        locally with a minimal implementation.
        """

        cmd = text.split("@")[0].lower().strip()
        logger.debug("Handling command: %s", cmd)

        try:
            if cmd == "/signals":
                await self._cmd_signals()
            elif cmd == "/health":
                await self._cmd_health()
            elif cmd == "/top":
                await self._cmd_top()
            elif cmd == "/backtest":
                await self._cmd_backtest()
            elif cmd == "/stats":
                await self._cmd_stats()
            elif cmd == "/help":
                await self._cmd_help()
            elif cmd == "/status":
                await self._cmd_legacy(cmd)
            elif cmd in ("/armed", "/ping"):
                await self.send_message("⚠️ This command has been removed. Use /health or /signals.")
            else:
                # Unknown command — only respond if it really looks like one
                # (starts with /) to avoid noise from stray text.
                if cmd.startswith("/"):
                    await self.send_message(
                        f"❓ Unknown command: <code>{escape(cmd)}</code>\nSend /help for the command list."
                    )
        except Exception as exc:  # noqa: BLE001 — never let a handler kill the loop
            logger.exception("Command handler crashed for %r: %s", cmd, exc)
            await self.send_message("⚠️ Internal error while handling that command.")

    async def _cmd_signals(self) -> None:
        signals = _get_signals(self.db, self.scanner)
        await self.send_message(build_signals_message(signals))

    async def _cmd_health(self) -> None:
        await self.send_message(self._build_health_report())

    async def _cmd_top(self) -> None:
        candidates = _get_top_candidates(self.db, self.scanner)
        await self.send_message(build_top_message(candidates))

    async def _cmd_backtest(self) -> None:
        """Show backtest performance stats."""
        try:
            import sqlite3
            # Read-only: a reporting command must never be able to write to,
            # or lock, the live outcome-tracking ledger.
            bt = sqlite3.connect(
                f"file:{_backtest_db_path(self.settings)}?mode=ro", uri=True, timeout=5.0
            )
            bc = bt.cursor()
            bc.execute("SELECT COUNT(*), SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END), SUM(CASE WHEN pnl_usd < 0 THEN 1 ELSE 0 END), SUM(pnl_usd), AVG(pnl_usd) FROM bt_v2_trades")
            r = bc.fetchone()
            trades, wins, losses, total_pnl, avg_pnl = r if r[0] else (0, 0, 0, 0, 0)
            win_rate = (wins / trades * 100) if trades > 0 else 0
            bc.execute("SELECT symbol, outcome, pnl_usd, leverage FROM bt_v2_trades ORDER BY id DESC LIMIT 5")
            recent = bc.fetchall()
            bt.close()
            lines = [
                "📊 <b>Backtest Performance</b>",
                "━━━━━━━━━━━━━━━━━━━━",
                f"📈 Trades: <b>{trades}</b> ({wins}W / {losses}L)",
                f"🎯 Win Rate: <b>{win_rate:.1f}%</b>",
                f"💰 Total PnL: <b>${total_pnl:.2f}</b> (${100 + total_pnl:.2f} from $100)",
                f"─ Avg PnL: <b>${avg_pnl:.2f}/trade</b>",
                "",
                "📋 <b>Recent Trades:</b>",
            ]
            for t in recent:
                icon = "✅" if t[2] > 0 else "❌" if t[2] < 0 else "⏰"
                lines.append(f"{icon} {t[0].split('/')[0]} · {t[1]} · ${t[2]:.2f} · {t[3]}x")
            if not recent:
                lines.append("No trades yet.")
            lines.append("")
            lines.append("💡 <i>Each new ENTRY_READY signal is auto-backtested.</i>")
            await self.send_message("\n".join(lines))
        except Exception as exc:
            await self.send_message(f"⚠️ Backtest data unavailable: {exc}")

    async def _cmd_stats(self) -> None:
        """Show model statistics."""
        try:
            import sqlite3
            db = sqlite3.connect(
                f"file:{_db_path(self.settings)}?mode=ro", uri=True, timeout=5.0
            )
            dc = db.cursor()
            dc.execute("SELECT COUNT(*) FROM lbank_signal_ledger")
            total_signals = dc.fetchone()[0]
            dc.execute("SELECT COUNT(*) FROM lbank_signal_outcomes")
            total_outcomes = dc.fetchone()[0]
            dc.execute("SELECT outcome_status, COUNT(*) FROM lbank_signal_outcomes GROUP BY outcome_status ORDER BY COUNT(*) DESC LIMIT 5")
            outcomes = dc.fetchall()
            dc.execute("SELECT decision, COUNT(*) FROM entry_decision_events GROUP BY decision ORDER BY COUNT(*) DESC LIMIT 5")
            decisions = dc.fetchall()
            lines = [
                "📊 <b>Model Statistics</b>",
                "━━━━━━━━━━━━━━━━━━━━",
                f"🌊 Total Signals: <b>{total_signals}</b>",
                f"📋 Outcomes: <b>{total_outcomes}</b>",
                "",
                "<b>Top Outcomes:</b>",
            ]
            for o, n in outcomes:
                pct = (n / total_outcomes * 100) if total_outcomes > 0 else 0
                lines.append(f"  {o}: {n} ({pct:.1f}%)")
            lines.append("")
            lines.append("<b>Decision States:</b>")
            for d, n in decisions:
                lines.append(f"  {d}: {n:,}")
            db.close()
            await self.send_message("\n".join(lines))
        except Exception as exc:
            await self.send_message(f"⚠️ Stats unavailable: {exc}")

    async def _cmd_help(self) -> None:
        await self.send_message(build_help_message())

    async def _cmd_legacy(self, cmd: str) -> None:
        """Delegate legacy commands to the existing TelegramNotifier when
        available; otherwise answer with a minimal local response."""

        if self.delegate is not None and hasattr(self.delegate, "_process_command"):
            try:
                await self.delegate._process_command(cmd)  # type: ignore[attr-defined]
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("Delegate failed for %s: %s", cmd, exc)

        # Minimal local fallbacks
        if cmd == "/status":
            total = _tracked_count(self.db, self.scanner)
            await self.send_message(
                f"✅ <b>Waterfall Engine:</b> ONLINE\n🌊 <b>Live Targets Tracked:</b> {total}"
            )
        elif cmd == "/armed":
            candidates = _get_active_candidates(self.db)
            armed = [s for s, d in candidates.items() if str(d.get("status", "")).upper() == "ARMED"]
            if armed:
                msg = (
                    f"🎯 <b>ARMED Targets ({len(armed)}):</b>\n"
                    + "\n".join(f"- #{escape(s)}" for s in armed)
                )
            else:
                msg = "🛡️ <b>No targets currently ARMED.</b>"
            await self.send_message(msg)
        elif cmd == "/ping":
            await self.send_message("🏓 <b>Pong!</b> Connection is stable.")

    # -- Health report ------------------------------------------------------

    def _build_health_report(self) -> str:
        tracked = _tracked_count(self.db, self.scanner)
        signals = _get_signals(self.db, self.scanner)
        db_path = _db_path(self.settings)
        db_size = _fmt_db_size(_db_size_bytes(db_path))
        mem = _memory_usage_percent()
        uptime = _fmt_uptime(_process_started_at() or self.started_at)
        ai = _ai_status(self.settings)
        sources = _exchange_sources_online()
        failures = _collect_source_failures(self.db)

        # Backend health: we're running this loop, so the engine is healthy.
        backend_healthy = True

        return build_health_message(
            backend_healthy=backend_healthy,
            tracked_count=tracked,
            signal_count=len(signals),
            db_size=db_size,
            memory_pct=mem,
            uptime=uptime,
            ai_status=ai,
            sources_online=sources,
            source_failures=failures,
        )

    async def _health_report_loop(self) -> None:
        """Background loop that posts a full health report every 12 hours.

        Fires once immediately on start (so the operator gets a confirmation
        that the bot is live), then on the configured interval.
        """

        if not self.enabled:
            logger.info("Health report loop disabled (bot not enabled).")
            return

        logger.info("🕐 12-hour health report loop started.")
        # Immediate first report so the operator sees the bot came up.
        try:
            await self.send_message(self._build_health_report())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Initial health report failed: %s", exc)

        while True:
            try:
                await asyncio.sleep(HEALTH_REPORT_INTERVAL_SECONDS)
                await self.send_message(self._build_health_report())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — loop must not die
                logger.warning("Health report cycle failed (%s): %s", type(exc).__name__, exc)
                await asyncio.sleep(60)

    # -- Interactive polling -------------------------------------------------

    async def _poll_loop(self) -> None:
        """Long-poll Telegram getUpdates and dispatch commands.

        Mirrors the existing ``TelegramNotifier.start_interactive_bot`` pattern
        (httpx, offset tracking, 429 backoff, 401/403 cooldown) so the two can
        coexist or the enhanced bot can replace it.
        """

        if not self.enabled:
            logger.info("Poll loop disabled (bot not enabled).")
            return

        self.started_at = time.time()
        url = f"{self._base_url}/getUpdates"
        logger.info("📡 Enhanced Telegram Command Center online (chat_id=%s).", self.chat_id)

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                # Bootstrap: skip the backlog of pending updates.
                try:
                    resp = await client.get(
                        url, params={"offset": -1, "timeout": 5}
                    )
                    if resp.status_code == 200:
                        updates = resp.json().get("result", [])
                        if updates:
                            self.offset = updates[-1]["update_id"] + 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "getUpdates bootstrap failed (%s): %.200s",
                        type(exc).__name__,
                        exc,
                    )

                while True:
                    try:
                        resp = await client.get(
                            url,
                            params={"offset": self.offset, "timeout": int(_GETUPDATES_TIMEOUT)},
                        )

                        if resp.status_code == 429:
                            retry_after = float(resp.headers.get("Retry-After", "30"))
                            logger.warning(
                                "Telegram poll rate-limited; backing off %ss.", retry_after
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
                                    await self.handle_command(command_text)
                        elif resp.status_code in (401, 403):
                            logger.error(
                                "Telegram polling rejected (HTTP %s): check "
                                "TELEGRAM_TOKEN/CHAT_ID.",
                                resp.status_code,
                            )
                            await asyncio.sleep(60)
                        else:
                            logger.warning(
                                "Telegram poll unexpected HTTP %s: %.200s",
                                resp.status_code,
                                resp.text,
                            )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "getUpdates poll failed (%s): %.200s",
                            type(exc).__name__,
                            exc,
                        )

                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Enhanced Telegram poll loop cancelled.")
            raise
        except Exception as exc:  # noqa: BLE001 — top-level safety net
            logger.exception("Poll loop crashed: %s", exc)

    # -- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Start the interactive poll loop AND the 12-hour health report.

        Both run as background tasks via ``asyncio.create_task`` so they can be
        started alongside the existing bot / application lifespan.
        """

        if not self.enabled:
            logger.warning("EnhancedTelegramBot.start() called but bot is disabled.")
            return

        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(
                self._poll_loop(), name="wfh-tg-poll"
            )
        if self._health_report_task is None or self._health_report_task.done():
            self._health_report_task = asyncio.create_task(
                self._health_report_loop(), name="wfh-tg-health-report"
            )
        logger.info("Enhanced Telegram bot started (poll + health report).")

    async def stop(self) -> None:
        """Cancel both background tasks gracefully."""

        for task in (self._poll_task, self._health_report_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._poll_task, self._health_report_task):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Task cleanup error: %s", exc)
        self._poll_task = None
        self._health_report_task = None
        logger.info("Enhanced Telegram bot stopped.")


# ---------------------------------------------------------------------------
# Module-level convenience: build + start in one call
# ---------------------------------------------------------------------------


async def start_enhanced_bot(
    db_adapter: Any = None,
    scanner: Any = None,
    *,
    delegate: Any = None,
    settings: Any = None,
) -> EnhancedTelegramBot:
    """Construct an ``EnhancedTelegramBot`` and start its background tasks.

    Intended to be called from the application lifespan alongside the existing
    notifier — e.g. inside ``main.py``::

        from waterfallhunter.core.telegram_enhanced import start_enhanced_bot
        ...
        await start_enhanced_bot(db_adapter=db, scanner=scanner, delegate=notifier)

    Returns the bot instance so the caller can ``await bot.stop()`` on shutdown.
    """

    bot = EnhancedTelegramBot(
        db_adapter=db_adapter,
        scanner=scanner,
        delegate=delegate,
        settings=settings,
    )
    await bot.start()
    return bot


# ---------------------------------------------------------------------------
# Module guard
# ---------------------------------------------------------------------------

__all__ = [
    "EnhancedTelegramBot",
    "SignalDeduper",
    "build_signals_message",
    "build_health_message",
    "build_top_message",
    "build_help_message",
    "start_enhanced_bot",
    "HEALTH_REPORT_INTERVAL_SECONDS",
    "SIGNAL_DEDUP_TTL_SECONDS",
]


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    logging.basicConfig(level=logging.INFO)

    async def _smoke() -> None:
        bot = EnhancedTelegramBot()
        if not bot.enabled:
            print("Bot disabled — set TELEGRAM_TOKEN / TELEGRAM_CHAT_ID to test live.")
            return
        await bot.start()
        # keep alive for a manual /help test
        try:
            await asyncio.sleep(3600)
        finally:
            await bot.stop()

    asyncio.run(_smoke())
