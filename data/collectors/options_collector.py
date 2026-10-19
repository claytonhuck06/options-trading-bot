"""Continuous SPY options collector.

One cycle = underlying quote + full options chain in the expiry window,
validated and stored. The loop runs during regular market hours only,
backs off exponentially on errors, refreshes the contract list
periodically, and stops after too many consecutive failures.

This module reads market data only. It never places orders.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from core.config import Settings
from core.logging_setup import get_logger
from data.models import OptionContract, OptionQuote, UnderlyingQuote
from data.storage.db import MarketDataStore, utcnow
from data.validators.quotes import check_quote

log = get_logger("collector")
EASTERN = ZoneInfo("America/New_York")


class MarketSource(Protocol):
    option_feed: str
    stock_feed: str

    def fetch_contracts(self, underlying: str, gte: date, lte: date) -> list[OptionContract]: ...
    def fetch_underlying(self, symbol: str) -> UnderlyingQuote: ...
    def fetch_chain(self, underlying: str, gte: date, lte: date,
                    strike_gte: float | None = None, strike_lte: float | None = None) -> list[OptionQuote]: ...


@dataclass
class CycleReport:
    cycle_id: int
    received: int = 0
    new: int = 0
    repeat: int = 0
    unusable: int = 0
    underlying_price: float | None = None
    flag_counts: dict[str, int] | None = None


def expiry_window(settings: Settings, today: date | None = None) -> tuple[date, date]:
    today = today or datetime.now(EASTERN).date()
    md = settings.market_data
    return today + timedelta(days=md.min_days_to_expiry), today + timedelta(days=md.max_days_to_expiry)


class OptionsCollector:
    def __init__(self, settings: Settings, source: MarketSource, store: MarketDataStore,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], datetime] = utcnow):
        self.settings = settings
        self.source = source
        self.store = store
        self.sleep = sleep
        self.clock = clock
        self.underlying = settings.market_data.primary_underlying
        self._contracts_refreshed_at: datetime | None = None
        self._known: set[str] = store.known_contract_symbols(self.underlying)

    # --- contracts --------------------------------------------------------
    def refresh_contracts(self, force: bool = False) -> int:
        refresh_every = timedelta(minutes=self.settings.market_data.contracts_refresh_minutes)
        now = self.clock()
        if not force and self._contracts_refreshed_at and now - self._contracts_refreshed_at < refresh_every:
            return 0
        gte, lte = expiry_window(self.settings)
        contracts = self.source.fetch_contracts(self.underlying, gte, lte)
        n = self.store.upsert_contracts(contracts)
        self._known = self.store.known_contract_symbols(self.underlying)
        self._contracts_refreshed_at = now
        self.store.log_event("INFO", "contracts_refreshed", {"underlying": self.underlying, "count": n, "window": [str(gte), str(lte)]})
        log.info("contracts refreshed: %d in window %s..%s", n, gte, lte)
        return n

    # --- one cycle --------------------------------------------------------
    def collect_once(self) -> CycleReport:
        self.refresh_contracts()
        rules = self.settings.data_quality
        cycle_id = self.store.start_cycle(self.underlying, self.source.option_feed)
        report = CycleReport(cycle_id)
        try:
            uq = self.source.fetch_underlying(self.underlying)
            self.store.insert_underlying_quote(cycle_id, uq, check_quote(uq, rules))
            report.underlying_price = uq.last_price

            spot = uq.last_price or (uq.bid and uq.ask and (uq.bid + uq.ask) / 2)
            if not spot:
                raise RuntimeError(f"no usable {self.underlying} price to centre the strike band")
            band = self.settings.market_data.strike_band_pct
            gte, lte = expiry_window(self.settings)
            quotes = self.source.fetch_chain(self.underlying, gte, lte, round(spot * (1 - band), 2), round(spot * (1 + band), 2))
            checked = [(q, check_quote(q, rules, known_contract=q.symbol in self._known)) for q in quotes]

            flag_counts: dict[str, int] = {}
            for _, r in checked:
                for f in r.flags:
                    flag_counts[f] = flag_counts.get(f, 0) + 1
            report.flag_counts = flag_counts
            report.received = len(checked)
            report.unusable = sum(not r.usable for _, r in checked)
            report.new = self.store.insert_option_quotes(cycle_id, checked)
            report.repeat = report.received - report.new

            if report.received == 0:
                raise RuntimeError("options chain returned no quotes")
            counts = {"received": report.received, "new": report.new, "repeat": report.repeat, "unusable": report.unusable}
            self.store.finish_cycle(cycle_id, "ok", counts)
            log.info("cycle %d: %d quotes (%d new, %d repeat, %d unusable), %s=%s",
                     cycle_id, report.received, report.new, report.repeat, report.unusable, self.underlying, report.underlying_price)
            return report
        except Exception as exc:
            self.store.finish_cycle(cycle_id, "failed", error=f"{type(exc).__name__}: {exc}")
            raise

    # --- loop -------------------------------------------------------------
    def run(self, is_market_open: Callable[[], bool], max_cycles: int | None = None, exit_when_closed: bool = True) -> int:
        """Collect until the market closes (or max_cycles). Returns completed cycles."""
        interval = self.settings.market_data.poll_interval_seconds
        max_failures = self.settings.market_data.max_consecutive_failures
        failures = done = 0
        self.store.log_event("INFO", "collector_started", {"underlying": self.underlying, "feed": self.source.option_feed})

        while max_cycles is None or done < max_cycles:
            try:
                open_now = is_market_open()
            except Exception as exc:
                open_now = None
                log.warning("market clock unavailable: %s", exc)
            if open_now is False:
                if exit_when_closed:
                    self.store.log_event("INFO", "collector_stopped", {"reason": "market_closed", "cycles": done})
                    log.info("market closed — collector stopping after %d cycles", done)
                    return done
                self.sleep(60)
                continue

            started = time.monotonic()
            try:
                if open_now is None:
                    raise RuntimeError("market clock unavailable")
                self.collect_once()
                done += 1
                if failures:
                    self.store.log_event("INFO", "collector_recovered", {"after_failures": failures})
                failures = 0
                self.sleep(max(0.0, interval - (time.monotonic() - started)))
            except Exception as exc:
                failures += 1
                backoff = min(300.0, interval * 2 ** min(failures - 1, 5))
                self.store.log_event("ERROR", "cycle_failed", {"error": f"{type(exc).__name__}: {exc}", "consecutive": failures, "backoff_s": backoff})
                log.error("cycle failed (%d in a row): %s — retrying in %.0fs", failures, exc, backoff)
                if failures >= max_failures:
                    self.store.log_event("CRITICAL", "collector_halted", {"consecutive_failures": failures})
                    log.critical("collector halted after %d consecutive failures", failures)
                    raise RuntimeError(f"collector halted after {failures} consecutive failures") from exc
                self.sleep(backoff)
        self.store.log_event("INFO", "collector_stopped", {"reason": "max_cycles", "cycles": done})
        return done
