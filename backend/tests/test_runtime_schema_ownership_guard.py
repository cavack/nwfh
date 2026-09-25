from __future__ import annotations

import re
from pathlib import Path


_RUNTIME_ROOT = Path(__file__).parents[1] / "src" / "waterfallhunter"
_ALLOWED_SCHEMA_MUTATION_FILES = {
    _RUNTIME_ROOT / "core" / "migrations.py",
    # Legacy isolated backtest stores and runtime bootstrap stores are tracked
    # explicitly until their independent schema lifecycles are migrated.
    _RUNTIME_ROOT / "core" / "backtester.py",
    _RUNTIME_ROOT / "core" / "backtester_v2.py",
    _RUNTIME_ROOT / "core" / "fundamental_observation_store.py",
    _RUNTIME_ROOT / "core" / "runtime_settings.py",
}
_DDL = re.compile(
    r"\b(?:CREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX|TRIGGER)|ALTER\s+TABLE|DROP\s+(?:TABLE|INDEX|TRIGGER))\b",
    re.IGNORECASE,
)


def test_runtime_python_has_no_schema_ddl_outside_migration_owner():
    violations: list[str] = []

    for path in sorted(_RUNTIME_ROOT.rglob("*.py")):
        if path in _ALLOWED_SCHEMA_MUTATION_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if _DDL.search(text):
            violations.append(str(path.relative_to(_RUNTIME_ROOT)))

    assert violations == []
