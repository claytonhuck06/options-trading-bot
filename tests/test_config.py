import pytest

from core.config import LIVE_AUTH_PHRASE, ConfigError, Environment, load_settings

SAFE_ENVS = ["development", "backtest", "observation", "paper"]


@pytest.mark.parametrize("env", SAFE_ENVS)
def test_every_safe_environment_loads_on_a_paper_account(env):
    s = load_settings(env)
    assert s.environment == Environment(env)
    assert s.broker.account_type == "paper"
    if env != "paper":                       # only the paper environment may ever send orders
        assert s.trading.enabled is False and s.trading.allow_order_submission is False
        assert s.can_submit_orders is False


def test_default_environment_is_development():
    assert load_settings().environment is Environment.DEVELOPMENT


def test_environment_variable_selects_environment(monkeypatch):
    monkeypatch.setenv("OPTIONS_QUANT_ENV", "paper")
    assert load_settings().environment is Environment.PAPER


def test_unknown_environment_rejected():
    with pytest.raises(ConfigError, match="unknown environment"):
        load_settings("production")


def test_initial_risk_values_match_plan():
    r = load_settings().risk
    assert (r.reference_equity, r.per_trade_risk_pct, r.aggregate_planned_risk_pct) == (20000, 0.005, 0.025)
    assert (r.daily_loss_limit_pct, r.daily_profit_stop_pct) == (0.015, 0.05)
    assert (r.drawdown_warning_pct, r.drawdown_shutdown_pct) == (0.05, 0.10)
    assert r.max_concurrent_positions == 3
    assert r.take_profit_pct_of_risk == 0.20
    assert not r.allow_uncovered_short_options and not r.allow_overnight_positions


def test_live_requires_authorization_phrase():
    with pytest.raises(ConfigError, match="authorization phrase"):
        load_settings("live")


def test_live_refused_even_with_phrase_in_this_build(monkeypatch):
    monkeypatch.setenv("OPTIONS_QUANT_LIVE_AUTHORIZATION", LIVE_AUTH_PHRASE)
    with pytest.raises(ConfigError, match="not supported"):
        load_settings("live")


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"per_trade_risk_pct": 0.03}, "per_trade_risk_pct"),
        ({"aggregate_planned_risk_pct": 0.001}, "aggregate_planned_risk_pct"),
        ({"drawdown_warning_pct": 0.2}, "drawdown_warning_pct"),
        ({"allow_uncovered_short_options": True}, "prohibited"),
        ({"allow_auto_risk_increase": True}, "prohibited"),
        ({"allow_autonomous_strategy_changes": True}, "prohibited"),
        ({"allow_overnight_positions": True}, "overnight"),
        ({"preferred_concurrent_positions": 5}, "preferred"),
        ({"reference_equity": -1}, "reference_equity"),
        ({"surprise_setting": 1}, "surprise_setting"),
    ],
)
def test_invalid_risk_limits_rejected(config_dir, edit_yaml, changes, message):
    edit_yaml(config_dir / "risk_limits.yaml", **changes)
    with pytest.raises(ConfigError, match=message):
        load_settings("development", config_dir=config_dir)


def test_environment_files_cannot_override_risk(config_dir, edit_yaml):
    edit_yaml(config_dir / "environments" / "paper.yaml", risk__per_trade_risk_pct=0.02)
    with pytest.raises(ConfigError, match="risk_limits.yaml"):
        load_settings("paper", config_dir=config_dir)


@pytest.mark.parametrize("env", ["development", "backtest", "observation"])
def test_trading_cannot_be_enabled_in_non_trading_envs(config_dir, edit_yaml, env):
    edit_yaml(config_dir / "environments" / f"{env}.yaml", trading__enabled=True)
    with pytest.raises(ConfigError, match="trading cannot be enabled"):
        load_settings(env, config_dir=config_dir)


def test_paper_env_cannot_point_at_live_account(config_dir, edit_yaml):
    edit_yaml(config_dir / "environments" / "paper.yaml", broker__account_type="live")
    with pytest.raises(ConfigError, match="may not use a live broker"):
        load_settings("paper", config_dir=config_dir)


def test_order_submission_requires_trading_enabled(config_dir, edit_yaml):
    edit_yaml(config_dir / "environments" / "paper.yaml", trading__enabled=False, trading__allow_order_submission=True)
    with pytest.raises(ConfigError, match="requires trading.enabled"):
        load_settings("paper", config_dir=config_dir)


def test_orders_only_possible_on_paper_with_both_switches(config_dir, edit_yaml):
    edit_yaml(config_dir / "environments" / "paper.yaml", trading__enabled=False, trading__allow_order_submission=False)
    assert load_settings("paper", config_dir=config_dir).can_submit_orders is False          # both off
    edit_yaml(config_dir / "environments" / "paper.yaml", trading__enabled=True)
    assert load_settings("paper", config_dir=config_dir).can_submit_orders is False          # one switch is not enough
    edit_yaml(config_dir / "environments" / "paper.yaml", trading__allow_order_submission=True)
    assert load_settings("paper", config_dir=config_dir).can_submit_orders is True
    for env in ("development", "backtest", "observation"):
        assert load_settings(env, config_dir=config_dir).can_submit_orders is False


@pytest.mark.parametrize(
    "changes",
    [
        {"schedule__target_flat_by": "16:05"},
        {"schedule__begin_closing": "14:00"},
        {"schedule__scan_start": "9:35"},
        {"market_data__min_days_to_expiry": 40},
        {"market_data__option_feed": "free"},
        {"logging__level": "LOUD"},
    ],
)
def test_invalid_settings_rejected(config_dir, edit_yaml, changes):
    edit_yaml(config_dir / "settings.yaml", **changes)
    with pytest.raises(ConfigError):
        load_settings("paper", config_dir=config_dir)  # paper.yaml overrides none of these keys


def test_missing_file_rejected(config_dir):
    (config_dir / "risk_limits.yaml").unlink()
    with pytest.raises(ConfigError, match="missing config file"):
        load_settings("development", config_dir=config_dir)


def test_settings_are_immutable():
    s = load_settings()
    with pytest.raises(Exception):
        s.trading.enabled = True
