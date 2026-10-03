"""Operator-adjustable decision settings with an auditable change history.

Every field here is a knob the dashboard can turn. The design constraints:

* **Bounded.** Each field declares a legal range. A typo cannot set the entry
  threshold to 5 and flood the outbox, or to 500 and silence it forever.
* **Fail-safe on read.** A corrupt or partially written row falls back to the
  shipped defaults rather than refusing to evaluate.
* **Auditable.** Every change is appended with the actor, the previous value
  and the new one. A calibration change that is not in this table did not
  happen through the supported path.
* **New signals only.** Settings are read at decision time, so a change never
  rewrites a decision that was already persisted.

The defaults intentionally match the shipped ``EntryDecisionPolicy`` — turning
the panel on changes nothing until an operator changes something.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from waterfallhunter.core.managed_sqlite import connect_managed_sqlite

SETTINGS_CONTRACT_VERSION = "runtime_settings_v1"

FieldKind = Literal["number", "toggle"]


@dataclass(frozen=True)
class SettingSpec:
    key: str
    kind: FieldKind
    default: Any
    label: str
    help: str
    group: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str = ""

    def coerce(self, value: Any) -> Any:
        """Return a legal value, or raise ValueError explaining why not."""
        if self.kind == "toggle":
            if not isinstance(value, bool):
                raise ValueError(f"{self.key} must be true or false")
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{self.key} must be a number")
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"{self.key} must be finite")
        if self.minimum is not None and number < self.minimum:
            raise ValueError(f"{self.key} must be >= {self.minimum}")
        if self.maximum is not None and number > self.maximum:
            raise ValueError(f"{self.key} must be <= {self.maximum}")
        return number


# ---------------------------------------------------------------------------
# The knobs.
#
# Ranges are deliberately conservative. entry_ready_minimum cannot go below
# forming_minimum's floor, anti-chase cannot be disabled by setting it to 0
# (use its toggle), and freshness cannot exceed one hour — beyond that the
# evidence is not describing the current book at all.
# ---------------------------------------------------------------------------
SPECS: tuple[SettingSpec, ...] = (
    # --- Decision thresholds -------------------------------------------------
    SettingSpec(
        "entry_ready_minimum", "number", 72.0,
        "ENTRY_READY threshold", "Readiness a candidate must reach to become actionable.",
        "thresholds", minimum=40.0, maximum=95.0, step=0.5, unit="pts",
    ),
    SettingSpec(
        "forming_minimum", "number", 55.0,
        "FORMING threshold", "Below this a candidate is NO_TRADE rather than FORMING.",
        "thresholds", minimum=20.0, maximum=90.0, step=0.5, unit="pts",
    ),
    SettingSpec(
        "coverage_minimum", "number", 55.0,
        "Evidence coverage floor", "Percent of scoring evidence that must be measurable.",
        "thresholds", minimum=20.0, maximum=100.0, step=1.0, unit="%",
    ),
    SettingSpec(
        "max_analysis_age_seconds", "number", 600.0,
        "Analysis freshness budget", "Older analysis is a hard block (STALE_ANALYSIS).",
        "thresholds", minimum=60.0, maximum=3600.0, step=30.0, unit="s",
    ),
    SettingSpec(
        "max_reference_age_seconds", "number", 60.0,
        "Reference price freshness", "Older reference price is a hard block (STALE_REFERENCE).",
        "thresholds", minimum=10.0, maximum=600.0, step=5.0, unit="s",
    ),
    SettingSpec(
        "anti_chase_hard_block_atr", "number", 2.5,
        "Anti-chase extension", "Extension from support past which a setup is LATE.",
        "thresholds", minimum=0.5, maximum=6.0, step=0.05, unit="ATR",
    ),

    # --- Gates ---------------------------------------------------------------
    SettingSpec(
        "gate_cascade_required", "toggle", True,
        "Cascade must PASS", "Hard-block anything whose cascade gate is not PASS.",
        "gates",
    ),
    SettingSpec(
        "gate_cross_exchange_required", "toggle", True,
        "Cross-exchange confirmation", "Require a second venue to confirm the breakdown.",
        "gates",
    ),
    SettingSpec(
        "gate_timing_required", "toggle", True,
        "Lower-timeframe timing", "Require at least one confirming lower timeframe.",
        "gates",
    ),
    SettingSpec(
        "gate_execution_required", "toggle", True,
        "Execution viability", "Require spread and slippage inside their ceilings.",
        "gates",
    ),
    SettingSpec(
        "gate_anti_chase_enabled", "toggle", True,
        "Anti-chase", "Reclassify extended moves as LATE instead of actionable.",
        "gates",
    ),
    SettingSpec(
        "gate_freshness_required", "toggle", True,
        "Evidence freshness", "Hard-block stale analysis and stale reference prices.",
        "gates",
    ),

    # --- Execution filters ---------------------------------------------------
    SettingSpec(
        "maximum_spread_pct", "number", 0.30,
        "Maximum spread", "Wider books fail the execution gate.",
        "execution", minimum=0.01, maximum=2.0, step=0.01, unit="%",
    ),
    SettingSpec(
        "maximum_slippage_pct", "number", 0.30,
        "Maximum slippage", "Estimated fill cost ceiling.",
        "execution", minimum=0.01, maximum=2.0, step=0.01, unit="%",
    ),
    SettingSpec(
        "max_reference_divergence_pct", "number", 0.60,
        "LBank divergence limit", "Reject when LBank and the evidence venue disagree by more.",
        "execution", minimum=0.05, maximum=3.0, step=0.05, unit="%",
    ),
    SettingSpec(
        "atr_stop_multiple", "number", 1.2,
        "Stop ATR multiple", "Stop must clear this multiple of the 15m ATR.",
        "execution", minimum=0.0, maximum=4.0, step=0.1, unit="x",
    ),

    # --- Leverage ------------------------------------------------------------
    SettingSpec(
        "leverage_minimum", "number", 4.0,
        "Minimum leverage", "Below this, leverage is reported as not recommended.",
        "leverage", minimum=1.0, maximum=20.0, step=1.0, unit="x",
    ),
    SettingSpec(
        "leverage_maximum", "number", 18.0,
        "Maximum leverage", "Ceiling regardless of what the risk bounds allow.",
        "leverage", minimum=1.0, maximum=50.0, step=1.0, unit="x",
    ),

    # --- Delivery ------------------------------------------------------------
    SettingSpec(
        "telegram_delivery_enabled", "toggle", False,
        "Telegram delivery", "Send ENTRY_READY signals to Telegram.",
        "delivery",
    ),
)

SPEC_BY_KEY: dict[str, SettingSpec] = {s.key: s for s in SPECS}
DEFAULTS: dict[str, Any] = {s.key: s.default for s in SPECS}

_TABLE = """
CREATE TABLE IF NOT EXISTS runtime_settings (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    settings_json   TEXT NOT NULL,
    updated_at      REAL NOT NULL,
    updated_by      TEXT NOT NULL DEFAULT 'system'
);
CREATE TABLE IF NOT EXISTS runtime_settings_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    changed_at      REAL NOT NULL,
    changed_by      TEXT NOT NULL,
    setting_key     TEXT NOT NULL,
    previous_value  TEXT,
    new_value       TEXT NOT NULL,
    note            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runtime_settings_history_at
    ON runtime_settings_history(changed_at DESC);
"""


class RuntimeSettingsStore:
    """Durable settings with an in-process cache.

    The cache matters: settings are read once per candidate evaluation, twelve
    of which run concurrently. Hitting SQLite on each would put a synchronous
    read on the event loop in the hottest path in the system.
    """

    def __init__(self, db_path: str, *, cache_seconds: float = 5.0) -> None:
        self.db_path = db_path
        self._cache_seconds = cache_seconds
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0
        self._schema_ready = False

    def _ensure(self) -> None:
        """Create the tables on first use.

        Deliberately not done in ``__init__``: importing ``waterfallhunter.main``
        constructs this store, and importing a module must never create a
        database. The startup schema gate checks exactly that.
        """
        if self._schema_ready:
            return
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            conn.executescript(_TABLE)
            conn.commit()
        self._schema_ready = True

    def invalidate(self) -> None:
        self._cache = None

    def current(self) -> dict[str, Any]:
        """Return the active settings, falling back to defaults on any fault."""
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_at) < self._cache_seconds:
            return self._cache
        merged = dict(DEFAULTS)
        try:
            self._ensure()
            with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
                row = conn.execute(
                    "SELECT settings_json FROM runtime_settings WHERE id = 1"
                ).fetchone()
            if row and row[0]:
                stored = json.loads(row[0])
                if isinstance(stored, dict):
                    for key, value in stored.items():
                        spec = SPEC_BY_KEY.get(key)
                        if spec is None:
                            continue  # retired knob, ignore rather than fail
                        try:
                            merged[key] = spec.coerce(value)
                        except ValueError:
                            pass  # out-of-range stored value falls back
        except Exception:
            # A settings read must never take the hunter loop down.
            pass
        self._cache = merged
        self._cache_at = now
        return merged

    def apply(
        self,
        changes: dict[str, Any],
        *,
        actor: str,
        note: str = "",
    ) -> dict[str, Any]:
        """Validate and persist changes. Returns the new full settings.

        Raises ValueError with an operator-readable message if any change is
        rejected; in that case nothing is written.
        """
        if not isinstance(changes, dict) or not changes:
            raise ValueError("no changes supplied")

        unknown = sorted(set(changes) - set(SPEC_BY_KEY))
        if unknown:
            raise ValueError(f"unknown settings: {', '.join(unknown)}")

        coerced = {key: SPEC_BY_KEY[key].coerce(value) for key, value in changes.items()}

        current = self.current()
        candidate = {**current, **coerced}

        # Cross-field invariants. Individually legal values can still describe
        # an incoherent policy.
        if candidate["forming_minimum"] > candidate["entry_ready_minimum"]:
            raise ValueError(
                "FORMING threshold cannot exceed the ENTRY_READY threshold"
            )
        if candidate["leverage_minimum"] > candidate["leverage_maximum"]:
            raise ValueError("minimum leverage cannot exceed maximum leverage")
        if candidate["max_reference_age_seconds"] > candidate["max_analysis_age_seconds"]:
            raise ValueError(
                "reference freshness cannot be looser than analysis freshness"
            )

        changed_at = time.time()
        history = [
            (changed_at, actor, key, json.dumps(current.get(key)), json.dumps(value), note)
            for key, value in coerced.items()
            if current.get(key) != value
        ]
        if not history:
            return current

        self._ensure()
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            conn.execute(
                "INSERT INTO runtime_settings (id, settings_json, updated_at, updated_by)"
                " VALUES (1, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET settings_json=excluded.settings_json,"
                " updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (json.dumps(candidate), changed_at, actor),
            )
            conn.executemany(
                "INSERT INTO runtime_settings_history"
                " (changed_at, changed_by, setting_key, previous_value, new_value, note)"
                " VALUES (?,?,?,?,?,?)",
                history,
            )
            conn.commit()

        self.invalidate()
        return candidate

    def reset(self, *, actor: str) -> dict[str, Any]:
        current = self.current()
        drifted = {k: v for k, v in DEFAULTS.items() if current.get(k) != v}
        if not drifted:
            return current
        return self.apply(drifted, actor=actor, note="reset to shipped defaults")

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            self._ensure()
            with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
                rows = conn.execute(
                    "SELECT changed_at, changed_by, setting_key, previous_value,"
                    " new_value, note FROM runtime_settings_history"
                    " ORDER BY changed_at DESC, id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        except Exception:
            return []

        def decode(raw: Any) -> Any:
            if raw is None:
                return None
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return raw

        return [
            {
                "changed_at": row[0],
                "changed_by": row[1],
                "setting_key": row[2],
                "previous_value": decode(row[3]),
                "new_value": decode(row[4]),
                "note": row[5],
            }
            for row in rows
        ]

    def describe(self) -> dict[str, Any]:
        """Full panel payload: schema, current values and defaults."""
        current = self.current()
        return {
            "contract_version": SETTINGS_CONTRACT_VERSION,
            "generated_at": time.time(),
            "groups": [
                {
                    "key": group,
                    "label": label,
                    "fields": [
                        {
                            "key": s.key,
                            "kind": s.kind,
                            "label": s.label,
                            "help": s.help,
                            "unit": s.unit,
                            "minimum": s.minimum,
                            "maximum": s.maximum,
                            "step": s.step,
                            "default": s.default,
                            "value": current[s.key],
                            "modified": current[s.key] != s.default,
                        }
                        for s in SPECS
                        if s.group == group
                    ],
                }
                for group, label in (
                    ("thresholds", "Decision thresholds"),
                    ("gates", "Gates"),
                    ("execution", "Execution filters"),
                    ("leverage", "Leverage"),
                    ("delivery", "Delivery"),
                )
            ],
        }
