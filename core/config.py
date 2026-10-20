"""Load, merge and validate the one authoritative configuration.

Order of precedence (later wins):
    config/settings.yaml  ->  config/environments/<env>.yaml
Risk limits come only from config/risk_limits.yaml and cannot be
overridden per environment.

The environment is chosen by the OPTIONS_QUANT_ENV variable and defaults
to "development". Any invalid value or unsafe combination raises
ConfigError and the program refuses to start.
"""

from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

ENV_VAR = "OPTIONS_QUANT_ENV"
LIVE_AUTH_VAR = "OPTIONS_QUANT_LIVE_AUTHORIZATION"
LIVE_AUTH_PHRASE = "I AUTHORIZE REAL-MONEY TRADING"

# Live trading is not built yet. Flip only in the phase that implements and
# tests live execution (Phase 14).
LIVE_TRADING_SUPPORTED = False


class ConfigError(Exception):
    """Raised when configuration is missing, invalid or unsafe."""


class Environment(str, Enum):
    DEVELOPMENT = "development"
    BACKTEST = "backtest"
    OBSERVATION = "observation"
    PAPER = "paper"
    LIVE = "live"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AlertSettings(_Strict):
    daily_summary: bool = True           # one push after the close with the day's trades (routine trades are not pushed one by one)


class AppSettings(_Strict):
    name: str
    timezone: str


class TradingSettings(_Strict):
    enabled: bool = False
    allow_order_submission: bool = False


class BrokerSettings(_Strict):
    name: str
    account_type: str

    @field_validator("account_type")
    @classmethod
    def _account_type(cls, v: str) -> str:
        if v not in ("paper", "live"):
            raise ValueError("account_type must be 'paper' or 'live'")
        return v


class MarketDataSettings(_Strict):
    primary_underlying: str
    additional_underlyings: list[str] = []
    stock_feed: str
    option_feed: str
    poll_interval_seconds: int = Field(ge=1, le=3600)
    min_days_to_expiry: int = Field(ge=0)
    max_days_to_expiry: int = Field(ge=1)
    strike_band_pct: float = Field(gt=0, le=1)
    contracts_refresh_minutes: int = Field(ge=1, le=1440)
    max_consecutive_failures: int = Field(ge=1, le=1000)

    @field_validator("stock_feed")
    @classmethod
    def _stock_feed(cls, v: str) -> str:
        if v not in ("iex", "sip"):
            raise ValueError("stock_feed must be 'iex' or 'sip'")
        return v

    @field_validator("option_feed")
    @classmethod
    def _option_feed(cls, v: str) -> str:
        if v not in ("indicative", "opra"):
            raise ValueError("option_feed must be 'indicative' or 'opra'")
        return v

    @model_validator(mode="after")
    def _expiry_window(self) -> "MarketDataSettings":
        if self.min_days_to_expiry >= self.max_days_to_expiry:
            raise ValueError("min_days_to_expiry must be less than max_days_to_expiry")
        return self


class PricingSettings(_Strict):
    rate_series: list[str]
    fallback_risk_free_rate: float = Field(ge=-0.05, le=0.25)
    max_rate_age_days: int = Field(ge=1)
    snapshot_max_quote_age_seconds: float = Field(gt=0)
    forward_pairs: int = Field(ge=1, le=50)
    surface_poly_degree: int = Field(ge=1, le=6)
    min_points_per_expiry: int = Field(ge=3)
    outlier_mad_threshold: float = Field(gt=1)
    american_crr_steps: int = Field(ge=20, le=5000)
    early_exercise_flag_threshold: float = Field(ge=0)
    min_option_price: float = Field(ge=0)
    parity_check_moneyness: float = Field(gt=0, le=1)
    model_vol_uncertainty_floor: float = Field(ge=0, le=0.05)

    @model_validator(mode="after")
    def _enough_points(self) -> "PricingSettings":
        if self.min_points_per_expiry < self.surface_poly_degree + 3:
            raise ValueError("min_points_per_expiry must be at least surface_poly_degree + 3")
        return self


class StrategySettings(_Strict):
    min_signal_z: float = Field(gt=0)
    spread_widths: list[float] = Field(min_length=1)
    min_edge_z: float = Field(gt=0)
    fee_per_contract: float = Field(ge=0)
    assumed_convergence: float = Field(gt=0, le=1)
    holding_period_minutes: int = Field(ge=1, le=390)
    vol_change_std_per_hour: float = Field(ge=0, le=0.1)
    max_quote_age_seconds: float = Field(gt=0)
    min_quote_size: float = Field(ge=0)
    max_leg_relative_spread: float = Field(gt=0, le=2)
    min_max_profit: float = Field(ge=0)
    min_risk_fraction_of_width: float = Field(ge=0, lt=1)
    max_box_gap: float = Field(default=0.05, ge=0)
    min_target_probability: float = Field(ge=0, lt=1)
    max_candidates_per_scan: int = Field(ge=1, le=1000)

    @field_validator("spread_widths")
    @classmethod
    def _widths(cls, v: list[float]) -> list[float]:
        if any(w <= 0 for w in v):
            raise ValueError("spread_widths must be positive")
        return sorted(set(v))


class WeeklySpreadSettings(_Strict):
    """Weekly SPY bull call spread, no market-timing signal (docs/research/spy_trend_preregistration.md, owner
    decision 2026-10-20 to trade the no-signal version on paper). Off unless enabled."""
    enabled: bool = False
    target_dte: int = Field(default=60, ge=14, le=120)
    dte_tolerance: int = Field(default=7, ge=0, le=21)
    long_delta: float = Field(default=0.50, gt=0, lt=1)
    short_delta: float = Field(default=0.20, gt=0, lt=1)
    time_exit_dte: int = Field(default=14, ge=1, le=60)
    run_at: str = "14:30"                 # Eastern; once a day from this time (before the 15:00 entry cutoff)
    strike_band_low: float = Field(default=0.95, gt=0, le=1)
    strike_band_high: float = Field(default=1.15, ge=1, le=2)

    @model_validator(mode="after")
    def _deltas(self) -> "WeeklySpreadSettings":
        from datetime import time
        time.fromisoformat(self.run_at)
        if self.short_delta >= self.long_delta:
            raise ValueError("short_delta must be below long_delta (a bull call spread)")
        if self.time_exit_dte >= self.target_dte - self.dte_tolerance:
            raise ValueError("time_exit_dte must be below the shortest entry DTE")
        return self


class ExecutionSettings(_Strict):
    require_manual_confirmation: bool = False
    order_timeout_seconds: float = Field(gt=0, le=300)
    max_reprice_attempts: int = Field(ge=1, le=10)
    entry_start_fraction: float = Field(ge=0, le=1)
    max_price_deterioration: float = Field(ge=0)
    max_daily_transaction_costs: float = Field(ge=0)
    poll_interval_seconds: float = Field(gt=0, le=10)


class DatabentoSettings(_Strict):
    total_budget_usd: float = Field(ge=0, le=125)       # the free credit; going above needs a deliberate code change
    max_request_usd: float = Field(gt=0, le=25)
    strike_band_pct: float = Field(gt=0, le=0.2)
    min_days_to_expiry: int = Field(ge=0)
    max_days_to_expiry: int = Field(ge=1)
    history_path: str


class DataQualitySettings(_Strict):
    max_quote_age_seconds: float = Field(gt=0)
    max_clock_skew_seconds: float = Field(ge=0)
    max_relative_spread: float = Field(gt=0, le=2)
    min_absolute_spread_for_wide: float = Field(ge=0)


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ScheduleSettings(_Strict):
    scan_start: str
    no_new_positions_after: str
    begin_closing: str
    target_flat_by: str

    @model_validator(mode="after")
    def _ordered(self) -> "ScheduleSettings":
        times = [self.scan_start, self.no_new_positions_after, self.begin_closing, self.target_flat_by]
        for t in times:
            if not _HHMM.match(t):
                raise ValueError(f"time '{t}' must be HH:MM (24h)")
        if times != sorted(times) or len(set(times)) != len(times):
            raise ValueError("schedule times must be strictly increasing")
        if self.target_flat_by >= "16:00":
            raise ValueError("target_flat_by must be before the 16:00 close")
        return self


class StorageSettings(_Strict):
    sqlite_path: str
    history_path: str = "data/storage/history.db"
    keep_full_days: int = Field(default=5, ge=1, le=365)
    archive_after_days: int = Field(default=30, ge=2, le=3650)
    archive_path: str = "data/storage/live_archive"

    @model_validator(mode="after")
    def _retention_order(self) -> "StorageSettings":
        if self.archive_after_days <= self.keep_full_days:
            raise ValueError("archive_after_days must be greater than keep_full_days")
        return self


class LoggingSettings(_Strict):
    level: str = "INFO"
    directory: str = "logs"
    json_file: str = "options_quant.jsonl"

    @field_validator("level")
    @classmethod
    def _level(cls, v: str) -> str:
        v = v.upper()
        if v not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError("invalid logging level")
        return v


class MultiDayLimits(_Strict):
    per_trade_risk_pct: float = Field(gt=0, le=0.05)
    max_open_positions: int = Field(ge=1, le=5)
    close_days_before_expiry: int = Field(ge=1, le=30)
    assignment_extrinsic_threshold: float = Field(ge=0, le=5)
    dividend_estimate: float = Field(ge=0, le=20)
    ex_dividend_dates: list[str] = []
    open_settle_minutes: int = Field(ge=0, le=60)
    gap_exit_slack: float = Field(ge=0, le=2)

    @field_validator("ex_dividend_dates")
    @classmethod
    def _dates(cls, v: list[str]) -> list[str]:
        from datetime import date
        for d in v:
            date.fromisoformat(str(d))
        return [str(d) for d in v]


class WeeklySpreadLimits(_Strict):
    """Risk pool of the weekly SPY call spread (owner decision 2026-10-20, paper)."""
    per_trade_risk_pct: float = Field(gt=0, le=0.05)
    max_open_positions: int = Field(ge=1, le=5)


class RiskLimits(_Strict):
    reference_equity: float = Field(gt=0)
    per_trade_risk_pct: float = Field(gt=0, le=0.02)
    aggregate_planned_risk_pct: float = Field(gt=0, le=0.10)
    daily_loss_limit_pct: float = Field(gt=0, le=0.05)
    daily_profit_stop_pct: float = Field(gt=0, le=0.20)
    drawdown_warning_pct: float = Field(gt=0, lt=1)
    drawdown_shutdown_pct: float = Field(gt=0, le=0.25)
    max_concurrent_positions: int = Field(ge=1, le=10)
    preferred_concurrent_positions: int = Field(ge=1)
    take_profit_pct_of_risk: float = Field(gt=0, le=5)
    allow_uncovered_short_options: bool
    allow_overnight_positions: bool
    allow_auto_risk_increase: bool
    allow_autonomous_strategy_changes: bool

    stop_loss_pct_of_max_loss: float = Field(gt=0, le=1)
    max_holding_minutes: int = Field(ge=1, le=390)
    mispricing_exit_z: float = Field(ge=0)
    max_net_delta_pct: float = Field(gt=0, le=5)
    max_net_vega_pct: float = Field(gt=0, le=0.05)
    underlying_betas: dict[str, float]
    stress_loss_pct_of_daily_allowance: float = Field(gt=0, le=2)
    allow_stock_hedge: bool
    max_hedge_shares: int = Field(ge=0)
    max_quote_age_with_positions_seconds: float = Field(gt=0)
    max_consecutive_execution_failures: int = Field(ge=1)
    approval_valid_seconds: float = Field(gt=0, le=300)
    multiday: MultiDayLimits
    weekly_spread: WeeklySpreadLimits

    @model_validator(mode="after")
    def _consistent(self) -> "RiskLimits":
        if self.aggregate_planned_risk_pct < self.per_trade_risk_pct:
            raise ValueError("aggregate_planned_risk_pct must be >= per_trade_risk_pct")
        if self.daily_loss_limit_pct < self.per_trade_risk_pct:
            raise ValueError("daily_loss_limit_pct must be >= per_trade_risk_pct")
        if self.drawdown_warning_pct >= self.drawdown_shutdown_pct:
            raise ValueError("drawdown_warning_pct must be below drawdown_shutdown_pct")
        if self.preferred_concurrent_positions > self.max_concurrent_positions:
            raise ValueError("preferred_concurrent_positions cannot exceed max_concurrent_positions")
        # Hard policy: these must stay off in this system.
        for flag in ("allow_uncovered_short_options", "allow_auto_risk_increase", "allow_autonomous_strategy_changes"):
            if getattr(self, flag):
                raise ValueError(f"{flag} must be false (prohibited by policy)")
        return self


class Settings(_Strict):
    environment: Environment
    app: AppSettings
    trading: TradingSettings
    broker: BrokerSettings
    market_data: MarketDataSettings
    data_quality: DataQualitySettings
    pricing: PricingSettings
    strategy: StrategySettings
    weekly_spread: WeeklySpreadSettings = WeeklySpreadSettings()
    execution: ExecutionSettings
    databento: DatabentoSettings
    schedule: ScheduleSettings
    storage: StorageSettings
    logging: LoggingSettings
    alerts: AlertSettings = AlertSettings()
    risk: RiskLimits

    @model_validator(mode="after")
    def _environment_safety(self) -> "Settings":
        env = self.environment
        if env is Environment.LIVE and self.broker.account_type != "live":
            raise ValueError("live environment must use a live broker account_type")
        if env is not Environment.LIVE and self.broker.account_type == "live":
            raise ValueError(f"{env.value} environment may not use a live broker account")
        if env in (Environment.DEVELOPMENT, Environment.BACKTEST, Environment.OBSERVATION):
            if self.trading.enabled or self.trading.allow_order_submission:
                raise ValueError(f"trading cannot be enabled in the {env.value} environment")
        if env is Environment.LIVE and self.risk.allow_overnight_positions:
            raise ValueError("overnight positions are allowed for paper trading only, not live")
        if self.trading.allow_order_submission and not self.trading.enabled:
            raise ValueError("allow_order_submission requires trading.enabled")
        return self

    @property
    def can_submit_orders(self) -> bool:
        """Orders may only ever be sent to the PAPER account, and only when both trading switches are on.
        Live order submission is not built and stays impossible here."""
        return (self.environment is Environment.PAPER and self.broker.account_type == "paper"
                and self.trading.enabled and self.trading.allow_order_submission)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"missing config file: {path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must contain a mapping at the top level")
    return data


def resolve_environment(value: str | None = None) -> Environment:
    raw = (value if value is not None else os.environ.get(ENV_VAR, "development")).strip().lower()
    try:
        return Environment(raw)
    except ValueError:
        valid = ", ".join(e.value for e in Environment)
        raise ConfigError(f"unknown environment '{raw}' (valid: {valid})") from None


def load_settings(environment: str | None = None, config_dir: Path = CONFIG_DIR) -> Settings:
    env = resolve_environment(environment)

    if env is Environment.LIVE:
        if os.environ.get(LIVE_AUTH_VAR) != LIVE_AUTH_PHRASE:
            raise ConfigError(
                f"live environment requires {LIVE_AUTH_VAR} to be set to the exact authorization phrase"
            )
        if not LIVE_TRADING_SUPPORTED:
            raise ConfigError("live trading is not supported in this build")

    merged = _deep_merge(_read_yaml(config_dir / "settings.yaml"), _read_yaml(config_dir / "environments" / f"{env.value}.yaml"))
    if "risk" in merged:
        raise ConfigError("risk limits may only be set in risk_limits.yaml")
    merged["risk"] = _read_yaml(config_dir / "risk_limits.yaml")
    merged["environment"] = env.value

    try:
        return Settings.model_validate(merged)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'settings'}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"invalid configuration ({env.value}): {problems}") from None
