"""One-time end-to-end demonstration on the Alpaca PAPER account (market hours only).

  1. Recovery + reconciliation must be clean before anything is sent.
  2. Rest a spread order far below the market, confirm it is working, cancel it,
     confirm it is cancelled, reconcile.
  3. Open one small SPY debit spread through the risk engine and order manager,
     confirm the fill with the broker, reconcile.
  4. Close it, confirm the fill, reconcile back to flat.
Each step is written to docs/research/paper_demo_<date>.md. It stops at the first
failure and the kill switch's normal rules still apply.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from core.config import PROJECT_ROOT, Settings
from execution.broker_api import OrderLeg, OrderStatus
from execution.reconciliation import reconcile
from execution.trader import TradingSession
from pricing.snapshot import latest_receipt_time
from risk.positions import LegQuote, OptionLeg, Portfolio
from risk.risk_engine import RiskContext, TradeRequest, approval_is_valid, approve
from risk.state import Level
from zoneinfo import ZoneInfo


def run_demo(settings: Settings, session: TradingSession, out_dir: Path | None = None) -> Path:
    log: list[str] = []
    now = datetime.now(timezone.utc)
    path = (out_dir or PROJECT_ROOT / "docs" / "research") / f"paper_demo_{now:%Y-%m-%d}.md"

    def step(text: str) -> None:
        log.append(f"- {datetime.now(timezone.utc):%H:%M:%S} UTC — {text}")
        print(text)

    def finish(ok: bool) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join([f"# Paper-trading demo — {now:%Y-%m-%d}", "",
                                   f"**Result: {'PASSED' if ok else 'STOPPED'}** (Alpaca PAPER account; no real money)", ""] + log + [""]))
        return path

    rep = session.startup()
    step(f"Startup recovery: {rep.recon.summary()}")
    if not rep.safe_to_resume:
        return finish(False)
    latest = latest_receipt_time(session.store, session.underlying)
    if latest is None or (now - latest).total_seconds() > 60:
        step("Market data is not fresh (is the market open and the collector running?)")
        return finish(False)

    result, run_id = session._price_now(datetime.now(timezone.utc))
    spot = result.snapshot.spot
    calls = sorted((c for c in result.contracts if c.reliable and c.option.option_type == "call"),
                   key=lambda c: (c.option.expiration, abs(c.option.strike - spot)))
    pair = next(((a, b) for a in calls for b in calls if b.option.expiration == a.option.expiration
                 and b.option.strike == a.option.strike + 1 and a.option.strike >= spot), None)
    if pair is None:
        step("No clean 1-wide call spread found in the snapshot")
        return finish(False)
    a, b = pair
    legs = [OrderLeg(a.option.symbol, "buy", 1, "buy_to_open"), OrderLeg(b.option.symbol, "sell", 1, "sell_to_open")]
    step(f"Spread: buy {a.option.symbol} @ {a.option.ask:.2f} / sell {b.option.symbol} @ {b.option.bid:.2f}, SPY {spot:.2f}")

    # 2. rest-and-cancel
    low = max(round(a.option.mid - b.option.mid - 0.30, 2), 0.01)
    order = session.broker.submit_multileg(f"oq-demo-cancel-{now:%H%M%S}", legs, 1, low)
    time.sleep(2)
    order = session.broker.get_order(order.broker_id)
    step(f"Rested order at {low:.2f}: broker status {order.status.value}")
    if order.status not in (OrderStatus.WORKING, OrderStatus.CANCELED):
        step("Unexpected status for a far-from-market order")
        return finish(False)
    session.broker.cancel_order(order.broker_id)
    for _ in range(10):
        time.sleep(1)
        order = session.broker.get_order(order.broker_id)
        if order.status.terminal:
            break
    step(f"After cancel: broker status {order.status.value}, filled {order.filled_qty}")
    recon = reconcile(session.book.open_positions(), session.broker.positions(), session.broker.open_orders(), expected_open_orders=set())
    step(f"Reconciliation: {recon.summary()}")
    if order.status != OrderStatus.CANCELED or not recon.ok:
        return finish(False)

    # 3. open through the risk engine
    equity = session.broker.equity()
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    day = session.state.start_day(today, equity)
    req = TradeRequest(f"demo-{now:%Y%m%d%H%M%S}", session.underlying,
                       (OptionLeg(a.option.symbol, "SPY", "call", a.option.strike, a.option.expiration, 1, a.option.ask),
                        OptionLeg(b.option.symbol, "SPY", "call", b.option.strike, b.option.expiration, -1, b.option.bid)),
                       settings.strategy.fee_per_contract * 4, 1, a.option.symbol)
    quotes = {c.option.symbol: LegQuote(c.option.bid, c.option.ask, c.fair_iv or 0.2, c.option.quote_ts) for c in (a, b)}
    ctx = RiskContext(datetime.now(timezone.utc), settings, session.state, day, equity, session.state.level(today),
                      Portfolio(session.book.open_positions()), quotes, {"SPY": spot}, result.expiries[0].rate, session.stress,
                      (datetime.now(timezone.utc) - result.snapshot.spot_received_at).total_seconds())
    dec = approve(req, ctx)
    step(f"Risk engine: {'approved ' + str(dec.contracts) if dec.approved else 'refused: ' + ', '.join(dec.reasons)}")
    if not dec.approved:
        return finish(False)
    res = session.orders.execute(req.request_id, req.request_id, "open", legs, dec.contracts,
                                 lambda: session._leg_quotes({a.option.symbol, b.option.symbol}, datetime.now(timezone.utc))[0],
                                 (a.option.ask - b.option.bid) + settings.execution.max_price_deterioration,
                                 still_approved=lambda: approval_is_valid(dec, datetime.now(timezone.utc)))
    step(f"Open order: {res.status}, filled {res.filled_qty} at net {res.avg_net_price}")
    if not res.filled_qty:
        return finish(False)
    pos = dec.position
    pos.legs = [OptionLeg(l.symbol, l.underlying, l.option_type, l.strike, l.expiration, l.quantity * res.filled_qty,
                          res.leg_prices.get(l.symbol, l.entry_price)) for l in pos.legs]
    session.book.open_position(pos)
    recon = reconcile(session.book.open_positions(), session.broker.positions(), session.broker.open_orders(), expected_open_orders=set())
    step(f"Reconciliation after open: {recon.summary()}")
    if not recon.ok:
        return finish(False)

    # 4. close
    quotes, _ = session._leg_quotes({l.symbol for l in pos.legs}, datetime.now(timezone.utc))
    step("Closing: " + session._close(pos, "demo", quotes, emergency=False))
    recon = reconcile(session.book.open_positions(), session.broker.positions(), session.broker.open_orders(), expected_open_orders=set())
    step(f"Reconciliation after close: {recon.summary()}; open positions in book: {len(session.book.open_positions())}")
    return finish(recon.ok and not session.book.open_positions())


def demo_passed(path: Path) -> bool:
    return path.is_file() and "**Result: PASSED**" in path.read_text()
