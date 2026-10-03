import sqlite3

import pytest

from waterfallhunter.core.entry_decision import EntryDecisionPolicy, _base_decision
from waterfallhunter.core.runtime_settings import DEFAULTS, RuntimeSettingsStore


@pytest.fixture()
def store(tmp_path):
    return RuntimeSettingsStore(str(tmp_path / "settings.db"), cache_seconds=0.0)


def test_fresh_store_returns_shipped_defaults(store):
    assert store.current() == DEFAULTS


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"entry_ready_minimum": 5.0}, "must be >="),
        ({"entry_ready_minimum": 500.0}, "must be <="),
        ({"gate_cascade_required": "yes"}, "must be true or false"),
        ({"entry_ready_minimum": "70"}, "must be a number"),
        ({"nonexistent_key": 1}, "unknown settings"),
        ({"forming_minimum": 90.0}, "cannot exceed the ENTRY_READY"),
        ({"leverage_minimum": 19.0}, "cannot exceed maximum leverage"),
        # Both values are individually legal; only their combination is not.
        (
            {"max_reference_age_seconds": 400.0, "max_analysis_age_seconds": 300.0},
            "cannot be looser",
        ),
    ],
)
def test_illegal_changes_are_refused_with_a_readable_reason(store, changes, expected):
    with pytest.raises(ValueError, match=expected):
        store.apply(changes, actor="test")
    assert store.current() == DEFAULTS, "a refused change must not be partially applied"


def test_apply_records_every_field_in_history(store):
    store.apply(
        {"entry_ready_minimum": 62.5, "gate_cross_exchange_required": False},
        actor="operator",
        note="loosening for a test",
    )
    current = store.current()
    assert current["entry_ready_minimum"] == 62.5
    assert current["gate_cross_exchange_required"] is False

    history = store.history()
    assert len(history) == 2
    by_key = {row["setting_key"]: row for row in history}
    assert by_key["entry_ready_minimum"]["previous_value"] == 72.0
    assert by_key["entry_ready_minimum"]["new_value"] == 62.5
    assert by_key["entry_ready_minimum"]["changed_by"] == "operator"
    assert by_key["entry_ready_minimum"]["note"] == "loosening for a test"


def test_unchanged_values_are_not_written_to_history(store):
    store.apply({"entry_ready_minimum": 72.0}, actor="operator")
    assert store.history() == []


def test_settings_change_the_decision_a_packet_receives(store):
    packet = dict(
        block_reasons=[],
        anti_chase_late=False,
        status="FORMING",
        cascade_status="PASS",
        readiness=65.0,
        coverage_pct=60.0,
        direction_ok=True,
        timing_ok=True,
        execution_ok=True,
        cross_ok=False,
        trade_plan_ok=True,
        lifecycle_ok=True,
        ai_gate_ok=True,
    )
    # Stock policy: readiness below 70 and no cross-exchange confirmation.
    assert _base_decision(**packet, policy=EntryDecisionPolicy()) == "FORMING"

    store.apply(
        {"entry_ready_minimum": 62.5, "gate_cross_exchange_required": False},
        actor="operator",
    )
    tuned = EntryDecisionPolicy.from_settings(store.current())
    assert _base_decision(**packet, policy=tuned) == "ENTRY_READY"
    assert tuned.version == "entry_policy_v2_operator_tuned"


def test_disabling_a_gate_stops_it_hard_blocking(store):
    packet = dict(
        block_reasons=["STALE_ANALYSIS"],
        anti_chase_late=False,
        status="FORMING",
        cascade_status="PASS",
        readiness=85.0,
        coverage_pct=90.0,
        direction_ok=True,
        timing_ok=True,
        execution_ok=True,
        cross_ok=True,
        trade_plan_ok=True,
        lifecycle_ok=True,
        ai_gate_ok=True,
    )
    assert _base_decision(**packet, policy=EntryDecisionPolicy()) == "NO_TRADE"

    store.apply({"gate_freshness_required": False}, actor="operator")
    tuned = EntryDecisionPolicy.from_settings(store.current())
    assert _base_decision(**packet, policy=tuned) == "ENTRY_READY"


def test_reset_restores_defaults_and_is_recorded(store):
    store.apply({"entry_ready_minimum": 62.5}, actor="operator")
    store.reset(actor="operator")
    assert store.current() == DEFAULTS
    assert any(row["note"] == "reset to shipped defaults" for row in store.history())


def test_corrupt_row_falls_back_to_defaults_rather_than_failing(store):
    store.apply({"entry_ready_minimum": 62.5}, actor="operator")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE runtime_settings SET settings_json = '{not json'")
        conn.commit()
    store.invalidate()
    assert store.current() == DEFAULTS


def test_out_of_range_stored_value_falls_back_for_that_field_only(store):
    store.apply({"entry_ready_minimum": 62.5}, actor="operator")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE runtime_settings SET settings_json = ?",
            ('{"entry_ready_minimum": 9999, "forming_minimum": 40.0}',),
        )
        conn.commit()
    store.invalidate()
    current = store.current()
    assert current["entry_ready_minimum"] == DEFAULTS["entry_ready_minimum"]
    assert current["forming_minimum"] == 40.0


def test_describe_exposes_schema_and_modified_flags(store):
    store.apply({"entry_ready_minimum": 62.5}, actor="operator")
    described = store.describe()
    fields = {
        field["key"]: field
        for group in described["groups"]
        for field in group["fields"]
    }
    assert fields["entry_ready_minimum"]["modified"] is True
    assert fields["entry_ready_minimum"]["value"] == 62.5
    assert fields["entry_ready_minimum"]["default"] == 72.0
    assert fields["forming_minimum"]["modified"] is False
    assert fields["gate_cascade_required"]["kind"] == "toggle"
