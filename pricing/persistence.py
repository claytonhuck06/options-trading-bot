"""Save pricing runs so later phases (scanner, backtests, reports) can replay them."""

from __future__ import annotations

import json

from data.storage.db import MarketDataStore, _iso, utcnow
from pricing.engine import PricingResult


def save_pricing(store: MarketDataStore, result: PricingResult, notes: str | None = None) -> int:
    snap = result.snapshot
    contracts = result.contracts
    cur = store.conn.execute(
        """INSERT INTO pricing_runs(as_of, underlying, spot, risk_free_rate, rate_source, contracts_in_snapshot,
           contracts_priced, created_at, notes) VALUES (?,?,?,?,?,?,?,?,?)""",
        (_iso(snap.as_of), snap.underlying, snap.spot,
         result.expiries[0].rate if result.expiries else None, result.rate_source,
         len(contracts), sum(c.fair_price is not None for c in contracts), _iso(utcnow()), notes),
    )
    run_id = int(cur.lastrowid)

    for e in result.expiries:
        f = e.fit
        status = "no_forward" if e.forward.forward is None else (f.status if f else "no_fit")
        store.conn.execute(
            "INSERT INTO volatility_surface_fits VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, _iso(e.expiration), e.years, e.forward.forward, e.forward.implied_dividend_yield,
             f.degree if f else None, json.dumps(f.coefficients) if f else None,
             f.points_used if f else None, f.points_excluded if f else None,
             f.rmse_iv if f and f.status == "ok" else None, f.atm_iv if f and f.status == "ok" else None, status),
        )

    store.conn.executemany(
        "INSERT INTO pricing_residuals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (run_id, c.option.symbol, c.option.option_type, c.option.strike, _iso(c.option.expiration), c.years,
             c.log_moneyness, c.option.bid, c.option.ask, c.option.mid, c.iv_bid, c.iv_mid, c.iv_ask,
             c.fair_iv, c.fair_price, c.buy_edge, c.sell_edge, c.price_uncertainty, c.buy_z, c.sell_z,
             c.delta, c.gamma, c.theta, c.vega, c.rho, c.early_exercise_premium, int(c.in_fit),
             json.dumps(sorted(c.flags)))
            for c in contracts
        ],
    )
    store.conn.commit()
    return run_id
