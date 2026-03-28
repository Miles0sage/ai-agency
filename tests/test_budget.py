# tests/test_budget.py
"""Tests for reserve-commit budget enforcement."""
import pytest
from budget import BudgetEnforcer, BudgetExhaustedError


def test_initial_remaining():
    b = BudgetEnforcer(0.10)
    assert b.remaining == 0.10


def test_reserve_reduces_remaining():
    b = BudgetEnforcer(0.10)
    assert b.reserve(0.03) is True
    assert b.remaining == pytest.approx(0.07)


def test_reserve_fails_when_over_budget():
    b = BudgetEnforcer(0.10)
    b.reserve(0.08)
    assert b.reserve(0.05) is False


def test_commit_moves_from_reserved_to_spent():
    b = BudgetEnforcer(0.10)
    b.reserve(0.05)
    b.commit(actual=0.03, reservation=0.05)
    assert b.spent == pytest.approx(0.03)
    assert b.remaining == pytest.approx(0.07)


def test_check_budget_raises_when_exhausted():
    b = BudgetEnforcer(0.01)
    b.commit(actual=0.02, reservation=0.0)
    with pytest.raises(BudgetExhaustedError):
        b.check_budget()


def test_check_budget_passes_when_ok():
    b = BudgetEnforcer(0.10)
    b.commit(actual=0.02, reservation=0.0)
    b.check_budget()  # should not raise
