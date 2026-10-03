"""Modeled cost basis and net-R attribution.

context: net_r was hardcoded None on every resolved outcome because
_entry_outcome_costs_complete required four cost components that are
structurally unknowable at decision time. The trade plan's levels are already
cost-adjusted, so a modeled net R was available all along.
"""
from __future__ import annotations

from waterfallhunter import main


def _costs(**overrides):
    base = {
        name: {"available": False, "classification": "UNAVAILABLE", "value": None}
        for name in ("fees", "entry_slippage", "exit_slippage", "funding")
    }
    base.update(overrides)
    return base


def test_a_capture_without_a_basis_declaration_is_not_modeled() -> None:
    assert main._entry_outcome_costs_modeled_complete(_costs()) is False
    assert main._entry_outcome_costs_modeled_complete({}) is False
    assert main._entry_outcome_costs_modeled_complete(None) is False


def test_a_basis_declaration_alone_is_not_enough() -> None:
    only_basis = _costs(basis=main._ENTRY_OUTCOME_COST_BASIS)
    assert main._entry_outcome_costs_modeled_complete(only_basis) is False


def test_the_full_declaration_is_recognised_as_modeled() -> None:
    declared = _costs(
        basis=main._ENTRY_OUTCOME_COST_BASIS,
        plan_levels_net_of_costs=True,
        modeled_constants_pct=dict(main._ENTRY_OUTCOME_MODELED_COST_PCT),
    )
    assert main._entry_outcome_costs_modeled_complete(declared) is True


def test_modeled_costs_never_satisfy_the_realized_bar() -> None:
    """A modeled basis must not be promoted to the scientific tier."""
    declared = _costs(
        basis=main._ENTRY_OUTCOME_COST_BASIS,
        plan_levels_net_of_costs=True,
    )
    assert main._entry_outcome_costs_complete(declared) is False


def test_the_modeled_constants_match_position_calculator() -> None:
    """The declaration must state the rates the plan was actually built with."""
    from waterfallhunter.core.position_calculator import PositionCalculator

    calculator = PositionCalculator()
    assert main._ENTRY_OUTCOME_MODELED_COST_PCT["entry_fee_pct"] == calculator.fee_pct
    assert (
        main._ENTRY_OUTCOME_MODELED_COST_PCT["funding_pct"]
        == calculator.funding_pct
    )
