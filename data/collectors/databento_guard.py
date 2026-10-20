"""Spending guard for Databento — the ONLY module allowed to make billable Databento calls.

Every download goes through `GuardedDatabento.get_range`:
  1. ask Databento for the exact price of the request (free),
  2. refuse if it exceeds the per-request cap or would push total spend past the budget,
  3. record the request in the spending ledger BEFORE downloading,
  4. download.
The budget lives in config/settings.yaml (databento.total_budget_usd). Nothing in the code
raises it; spending beyond it requires the owner to change that setting.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.config import DatabentoSettings
from core.logging_setup import get_logger

log = get_logger("databento")

LEDGER = """
CREATE TABLE IF NOT EXISTS databento_spend (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_at TEXT NOT NULL,
    dataset TEXT NOT NULL,
    schema TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    n_symbols INTEGER NOT NULL,
    quoted_usd REAL NOT NULL,
    billable_bytes INTEGER NOT NULL,
    status TEXT NOT NULL,             -- downloading | done | failed
    note TEXT
);
"""


class BudgetExceeded(Exception):
    """A download was refused because it would break a spending cap."""


@dataclass(frozen=True)
class SpendSummary:
    spent_usd: float
    budget_usd: float
    requests: int

    @property
    def remaining_usd(self) -> float:
        return max(self.budget_usd - self.spent_usd, 0.0)


class GuardedDatabento:
    def __init__(self, cfg: DatabentoSettings, ledger: sqlite3.Connection, client=None):
        self.cfg = cfg
        self.ledger = ledger
        self.ledger.executescript(LEDGER)
        self.ledger.commit()
        self._client_obj = client             # created on first use, so local-only commands need no key

    @property
    def _client(self):
        if self._client_obj is None:
            import os

            from core.config import ConfigError
            if not os.environ.get("DATABENTO_API_KEY"):
                raise ConfigError("DATABENTO_API_KEY is not set in this terminal. Run `source ~/.zshrc` "
                                  "or open a new terminal window, then try again.")
            import databento as db
            try:
                self._client_obj = db.Historical()          # reads DATABENTO_API_KEY from the environment
            except ValueError:
                # Databento's own message includes the key itself; never let that reach the screen or logs.
                raise ConfigError("Databento rejected the API key format. Re-run scripts/set_databento_key.sh.") from None
        return self._client_obj

    # --- free calls -------------------------------------------------------------
    def quote(self, **request) -> tuple[float, int]:
        """(USD, bytes) Databento would charge for this request. Free."""
        with _plain_errors():
            return (float(self._client.metadata.get_cost(**request)),
                    int(self._client.metadata.get_billable_size(**request)))

    def summary(self) -> SpendSummary:
        spent, n = self.ledger.execute(
            "SELECT COALESCE(SUM(quoted_usd), 0), COUNT(*) FROM databento_spend WHERE status != 'refused'").fetchone()
        return SpendSummary(float(spent), self.cfg.total_budget_usd, int(n))

    # --- the only billable call --------------------------------------------------------
    def get_range(self, *, dataset: str, schema: str, symbols: list[str], stype_in: str, start: str, end: str):
        request = dict(dataset=dataset, schema=schema, symbols=symbols, stype_in=stype_in, start=start, end=end)
        cost, size = self.quote(**request)
        s = self.summary()
        if cost > self.cfg.max_request_usd:
            raise BudgetExceeded(f"request would cost ${cost:.2f}, over the ${self.cfg.max_request_usd:.2f} per-request cap")
        if s.spent_usd + cost > self.cfg.total_budget_usd + 1e-9:
            raise BudgetExceeded(f"request would cost ${cost:.2f}; ${s.spent_usd:.2f} of the ${s.budget_usd:.2f} budget "
                                 f"is already used. Raising the budget is the owner's decision.")
        cur = self.ledger.execute(
            "INSERT INTO databento_spend(requested_at, dataset, schema, start, end, n_symbols, quoted_usd, billable_bytes, status) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), dataset, schema, start, end, len(symbols), cost, size, "downloading"))
        self.ledger.commit()
        try:
            with _plain_errors(), _time_limit(REQUEST_TIMEOUT_SECONDS):
                data = self._client.timeseries.get_range(**request)
        except Exception as exc:
            # Databento may still bill a request that failed mid-way; keep it counted against the budget.
            self.ledger.execute("UPDATE databento_spend SET status='failed', note=? WHERE id=?", (str(exc)[:500], cur.lastrowid))
            self.ledger.commit()
            raise
        self.ledger.execute("UPDATE databento_spend SET status='done' WHERE id=?", (cur.lastrowid,))
        self.ledger.commit()
        log.info("databento %s %s..%s %d symbols: $%.4f (total $%.2f of $%.2f)", schema, start, end, len(symbols), cost,
                 s.spent_usd + cost, s.budget_usd)
        return data


REQUEST_TIMEOUT_SECONDS = 300


class _time_limit:
    """Abort a download that hangs (the client has no timeout of its own). Main thread only; elsewhere a no-op."""

    def __init__(self, seconds: int):
        self.seconds = seconds

    def __enter__(self):
        import signal
        import threading
        self.active = threading.current_thread() is threading.main_thread()
        if self.active:
            def _raise(signum, frame):
                raise TimeoutError(f"Databento did not answer within {self.seconds} s")
            self.previous = signal.signal(signal.SIGALRM, _raise)
            signal.alarm(self.seconds)
        return self

    def __exit__(self, *exc):
        if self.active:
            import signal
            signal.alarm(0)
            signal.signal(signal.SIGALRM, self.previous)
        return False


class _plain_errors:
    """Turn authentication failures into a plain instruction, and make sure no error text carries the key."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is None:
            return False
        import os

        from core.config import ConfigError
        key = os.environ.get("DATABENTO_API_KEY") or ""
        text = str(exc).replace(key, "<hidden>") if key else str(exc)
        if "401" in text or "auth" in text.lower():
            raise ConfigError("Databento rejected the API key. Check it in the Databento portal and re-run "
                              "scripts/set_databento_key.sh.") from None
        if key and key in str(exc):
            raise RuntimeError(text) from None
        return False


def open_ledger(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
