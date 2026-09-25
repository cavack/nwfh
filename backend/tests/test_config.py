from waterfallhunter.config import (
    Settings,
)


def test_settings_exposes_the_signal_only_and_typesafe_configuration_fields():
    configured = Settings(
        _env_file=None,
        live_trading_enabled=False,
        typesafe_api_key="test-key",
    )

    assert (
        configured.live_trading_enabled
        is False
    )

    assert (
        configured.typesafe_api_key
        == "test-key"
    )

    assert (
        configured.typesafe_model
        == "jev-latest"
    )


def test_lbank_execution_shadow_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SOURCE_REVISION", raising=False)
    configured = Settings(
        _env_file=None,
    )

    assert (
        configured.lbank_execution_shadow_enabled
        is False
    )
    assert configured.source_revision is None


def test_exact_build_revision_is_available_to_forward_capture(monkeypatch):
    monkeypatch.setenv("SOURCE_REVISION", "a" * 40)
    configured = Settings(_env_file=None)

    assert configured.source_revision == "a" * 40


def test_lbank_execution_shadow_defaults_are_bounded_operational_values():
    configured = Settings(
        _env_file=None,
    )

    assert (
        configured.lbank_execution_shadow_batch_size
        == 8
    )

    assert (
        configured.lbank_execution_shadow_interval_seconds
        == 60.0
    )

    assert (
        configured.lbank_execution_shadow_success_recheck_seconds
        == 1800.0
    )

    assert (
        configured.lbank_execution_shadow_failure_recheck_seconds
        == 600.0
    )


def test_lbank_execution_shadow_configuration_can_be_overridden():
    configured = Settings(
        _env_file=None,
        lbank_execution_shadow_enabled=True,
        lbank_execution_shadow_batch_size=4,
        lbank_execution_shadow_interval_seconds=120.0,
        lbank_execution_shadow_success_recheck_seconds=900.0,
        lbank_execution_shadow_failure_recheck_seconds=300.0,
    )

    assert (
        configured.lbank_execution_shadow_enabled
        is True
    )

    assert (
        configured.lbank_execution_shadow_batch_size
        == 4
    )

    assert (
        configured.lbank_execution_shadow_interval_seconds
        == 120.0
    )

    assert (
        configured.lbank_execution_shadow_success_recheck_seconds
        == 900.0
    )

    assert (
        configured.lbank_execution_shadow_failure_recheck_seconds
        == 300.0
    )
