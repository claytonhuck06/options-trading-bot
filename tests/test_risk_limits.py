import pytest

from core.config import load_settings
from risk.limits import compute_daily_budget, take_profit_target


@pytest.fixture
def limits():
    return load_settings().risk


def test_budget_at_20k(limits):
    b = compute_daily_budget(limits, 20_000)
    assert b.max_risk_per_trade == 100
    assert b.max_aggregate_planned_risk == 500
    assert b.daily_loss_trigger == 300
    assert b.daily_profit_ceiling == 1_000
    assert b.drawdown_warning_equity == 19_000
    assert b.drawdown_shutdown_equity == 18_000


def test_budget_scales_with_equity_25k(limits):
    b = compute_daily_budget(limits, 25_000)
    assert (b.max_risk_per_trade, b.max_aggregate_planned_risk, b.daily_loss_trigger, b.daily_profit_ceiling) == (125, 625, 375, 1_250)


def test_drawdown_measured_from_high_water_mark(limits):
    b = compute_daily_budget(limits, 22_000, high_water_mark=25_000)
    assert b.max_risk_per_trade == 110            # daily limits use today's equity
    assert b.drawdown_warning_equity == 23_750    # drawdown uses the high-water mark
    assert b.drawdown_shutdown_equity == 22_500


def test_high_water_mark_never_below_current_equity(limits):
    assert compute_daily_budget(limits, 21_000, high_water_mark=20_000).high_water_mark == 21_000


def test_take_profit_is_fraction_of_capital_at_risk(limits):
    assert take_profit_target(limits, 100) == 20


@pytest.mark.parametrize("bad", [0, -5])
def test_rejects_non_positive_inputs(limits, bad):
    with pytest.raises(ValueError):
        compute_daily_budget(limits, bad)
    with pytest.raises(ValueError):
        take_profit_target(limits, bad)
