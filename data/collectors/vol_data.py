"""Free data for the volatility strategy (iron condor + long straddle), Phase 1.

  * CBOE volatility indices (VIX, VIX9D, VIX3M): daily OHLC, free CSV downloads.
  * FOMC statement dates: the Federal Reserve's own calendar pages (2015 onward).
  * CPI release dates: bundled file `data/reference/cpi_release_dates.csv` (BLS blocks scripted
    downloads); taken from the BLS release archive and cross-checked against FRED/ALFRED.
  * SPY end-of-day option chains: OptionsDX monthly files, which the owner downloads by hand
    (free checkout on their site); loaded from a folder with `main.py vol-data load --path DIR`.

Everything goes into its own SQLite file (`data/storage/vol_history.db`) so the live collector
and trader databases are never touched. Nothing here looks at trade results: Phase 1 is data only.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from core.logging_setup import get_logger

log = get_logger("vol_data")

CBOE_CSV = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv"
FOMC_CURRENT = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_HISTORICAL = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
VOL_INDICES = ("VIX", "VIX9D", "VIX3M")
FIRST_FOMC_YEAR = 2015
CPI_FILE = Path(__file__).resolve().parents[1] / "reference" / "cpi_release_dates.csv"

# Option rows kept from the vendor files: enough for 14–60 DTE condors and straddles plus margin.
MAX_DTE = 100
STRIKE_BAND = 0.25

SCHEMA = """
CREATE TABLE IF NOT EXISTS index_daily (
    symbol TEXT NOT NULL, day TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL NOT NULL,
    PRIMARY KEY (symbol, day)
);
-- kind: scheduled | cancelled (was scheduled, so known in advance) | unscheduled | notation
CREATE TABLE IF NOT EXISTS events (
    day TEXT NOT NULL, event TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT, source TEXT NOT NULL,
    PRIMARY KEY (day, event)
);
-- One row per strike per expiry per quote day, calls and puts side by side (vendor layout).
CREATE TABLE IF NOT EXISTS eod_chain (
    quote_date TEXT NOT NULL, quote_time_hours REAL, underlying REAL NOT NULL,
    expiry TEXT NOT NULL, dte REAL NOT NULL, strike REAL NOT NULL,
    c_bid REAL, c_ask REAL, c_last REAL, c_iv REAL, c_delta REAL, c_gamma REAL, c_vega REAL, c_theta REAL, c_volume REAL,
    p_bid REAL, p_ask REAL, p_last REAL, p_iv REAL, p_delta REAL, p_gamma REAL, p_vega REAL, p_theta REAL, p_volume REAL,
    PRIMARY KEY (quote_date, expiry, strike)
);
CREATE TABLE IF NOT EXISTS loaded_files (
    name TEXT PRIMARY KEY, rows_read INTEGER, rows_kept INTEGER, loaded_at TEXT
);
"""


def open_vol_db(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def _get(url: str, timeout: float = 60.0) -> str:
    resp = requests.get(url, headers={"User-Agent": "options-quant/0.1"}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


# --- CBOE volatility indices ------------------------------------------------------------------

def parse_cboe_csv(text: str) -> list[tuple[str, float | None, float | None, float | None, float]]:
    """DATE,OPEN,HIGH,LOW,CLOSE with MM/DD/YYYY dates -> (iso day, o, h, l, c). Bad rows are skipped."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            day = datetime.strptime(row["DATE"].strip(), "%m/%d/%Y").date().isoformat()
            close = float(row["CLOSE"])
        except (KeyError, ValueError):
            continue
        if close <= 0:
            continue
        vals = []
        for k in ("OPEN", "HIGH", "LOW"):
            try:
                v = float(row[k])
                vals.append(v if v > 0 else None)
            except (KeyError, ValueError):
                vals.append(None)
        out.append((day, *vals, close))
    return out


def store_index(conn: sqlite3.Connection, symbol: str, rows) -> int:
    conn.executemany("INSERT OR REPLACE INTO index_daily VALUES (?,?,?,?,?,?)", [(symbol, *r) for r in rows])
    conn.commit()
    return len(rows)


# --- FOMC calendar ------------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _month_number(name: str) -> int:
    return _MONTHS[name.strip().lower()[:3]]


def fomc_statement_day(year: int, month_text: str, day_text: str) -> tuple[date, str]:
    """('Apr/May', '30-1*') -> (May 1, 'scheduled'). The statement comes on the meeting's last day."""
    note = day_text.lower()
    if "notation" in note:
        kind = "notation"
    elif "unscheduled" in note:
        kind = "unscheduled"
    elif "cancel" in note:
        kind = "cancelled"
    else:
        kind = "scheduled"
    days = [int(d) for d in re.findall(r"\d+", day_text.split("(")[0])]
    if not days:
        raise ValueError(f"no day in {day_text!r}")
    months = [m for m in month_text.split("/") if m.strip()]
    first, last = days[0], days[-1]
    wraps = len(months) > 1 and last < first          # 'Jan/Feb 31-1': the last day is in the second month
    month = _month_number(months[-1] if wraps else months[0])
    return date(year, month, last), kind


def parse_fomc_current(html: str) -> list[tuple[date, str, str]]:
    """The Fed's current calendar page (last ~5 years plus the next one)."""
    out, year = [], None
    pat = re.compile(r'<a id="\d+">(\d{4}) FOMC Meetings</a>'
                     r'|fomc-meeting__month[^>]*><strong>([^<]*)</strong></div>\s*'
                     r'<div class="fomc-meeting__date[^>]*>([^<]*)</div>')
    for m in pat.finditer(html):
        if m.group(1):
            year = int(m.group(1))
        elif year is not None:
            day, kind = fomc_statement_day(year, m.group(2), m.group(3))
            out.append((day, kind, f"{m.group(2)} {m.group(3).strip()}"))
    return out


def parse_fomc_historical(html: str, year: int) -> list[tuple[date, str, str]]:
    """A year's historical page: headings like 'Jan/Feb 31-1 Meeting - 2017' or 'March 2 (unscheduled) Meeting - 2020'."""
    out = []
    for h in re.findall(r"<h5[^>]*>([^<]*)</h5>", html):
        m = re.match(r"\s*([A-Za-z/]+)\s+([\d\-]+(?:\s*\([^)]*\))?)\s*(?:Meeting)?\s*-\s*(\d{4})", h)
        if not m or int(m.group(3)) != year:
            continue
        day, kind = fomc_statement_day(year, m.group(1), m.group(2))
        out.append((day, kind, h.strip()))
    return out


def store_events(conn: sqlite3.Connection, event: str, rows, source: str) -> int:
    conn.executemany("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?)",
                     [(d.isoformat(), event, kind, detail, source) for d, kind, detail in rows])
    conn.commit()
    return len(rows)


def load_cpi_dates(path: Path = CPI_FILE) -> list[tuple[date, str, str]]:
    rows = []
    with open(path) as f:
        for row in csv.DictReader(line for line in f if not line.startswith("#")):
            rows.append((date.fromisoformat(row["release_date"]), "scheduled", f"CPI for {row['reference_month']}"))
    return rows


# --- OptionsDX end-of-day chains ---------------------------------------------------------------

_COLUMNS = {  # vendor header (brackets and spaces removed) -> our column
    "QUOTE_DATE": "quote_date", "QUOTE_TIME_HOURS": "quote_time_hours", "UNDERLYING_LAST": "underlying",
    "EXPIRE_DATE": "expiry", "DTE": "dte", "STRIKE": "strike",
    "C_BID": "c_bid", "C_ASK": "c_ask", "C_LAST": "c_last", "C_IV": "c_iv", "C_DELTA": "c_delta",
    "C_GAMMA": "c_gamma", "C_VEGA": "c_vega", "C_THETA": "c_theta", "C_VOLUME": "c_volume",
    "P_BID": "p_bid", "P_ASK": "p_ask", "P_LAST": "p_last", "P_IV": "p_iv", "P_DELTA": "p_delta",
    "P_GAMMA": "p_gamma", "P_VEGA": "p_vega", "P_THETA": "p_theta", "P_VOLUME": "p_volume",
}
_REQUIRED = ("QUOTE_DATE", "UNDERLYING_LAST", "EXPIRE_DATE", "DTE", "STRIKE", "C_BID", "C_ASK", "P_BID", "P_ASK")
_ORDER = ["quote_date", "quote_time_hours", "underlying", "expiry", "dte", "strike",
          "c_bid", "c_ask", "c_last", "c_iv", "c_delta", "c_gamma", "c_vega", "c_theta", "c_volume",
          "p_bid", "p_ask", "p_last", "p_iv", "p_delta", "p_gamma", "p_vega", "p_theta", "p_volume"]


class ChainFormatError(ValueError):
    pass


def _num(v: str) -> float | None:
    v = v.strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def parse_optionsdx(text: str) -> tuple[list[tuple], int]:
    """Parse one vendor file. Returns (rows kept, rows read). Unknown layouts raise instead of guessing."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip().strip("[]").strip().upper() for h in next(reader)]
    except StopIteration:
        return [], 0
    missing = [c for c in _REQUIRED if c not in header]
    if missing:
        raise ChainFormatError(f"unexpected file layout; missing columns {missing}")
    idx = {_COLUMNS[h]: i for i, h in enumerate(header) if h in _COLUMNS}
    kept, read = [], 0
    for raw in reader:
        if not raw or len(raw) < len(header):
            continue
        read += 1
        rec = {}
        for col in _ORDER:
            i = idx.get(col)
            if i is None:
                rec[col] = None
            elif col in ("quote_date", "expiry"):
                rec[col] = raw[i].strip()
            else:
                rec[col] = _num(raw[i])
        if not rec["quote_date"] or not rec["expiry"] or rec["underlying"] is None or rec["strike"] is None \
                or rec["dte"] is None:
            continue
        if rec["dte"] > MAX_DTE or abs(rec["strike"] / rec["underlying"] - 1) > STRIKE_BAND:
            continue
        kept.append(tuple(rec[c] for c in _ORDER))
    return kept, read


def _vendor_files(folder: Path):
    """Yield (name, text) for every .txt/.csv in the folder, including inside .zip files."""
    for p in sorted(folder.rglob("*")):
        if p.suffix.lower() in (".txt", ".csv"):
            yield p.name, p.read_text(errors="replace")
        elif p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as z:
                for n in sorted(z.namelist()):
                    if n.lower().endswith((".txt", ".csv")) and not n.startswith("__MACOSX"):
                        yield f"{p.name}:{n}", z.read(n).decode(errors="replace")


def load_optionsdx_folder(conn: sqlite3.Connection, folder: str | Path) -> list[tuple[str, int, int]]:
    """Load every vendor file not loaded before. Returns (file, rows read, rows kept) per new file."""
    done = {r[0] for r in conn.execute("SELECT name FROM loaded_files")}
    results = []
    for name, text in _vendor_files(Path(folder)):
        if name in done:
            continue
        rows, read = parse_optionsdx(text)
        conn.executemany(f"INSERT OR REPLACE INTO eod_chain VALUES ({','.join('?' * len(_ORDER))})", rows)
        conn.execute("INSERT OR REPLACE INTO loaded_files VALUES (?,?,?,?)",
                     (name, read, len(rows), datetime.now(timezone.utc).isoformat(timespec="seconds")))
        conn.commit()
        log.info("loaded %s: %d of %d rows kept", name, len(rows), read)
        results.append((name, read, len(rows)))
    return results


# --- Fetch everything that can be fetched without the owner -------------------------------------

def fetch_all(conn: sqlite3.Connection, today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    counts: dict[str, int] = {}
    for sym in VOL_INDICES:
        counts[sym] = store_index(conn, sym, parse_cboe_csv(_get(CBOE_CSV.format(symbol=sym))))
    fomc = parse_fomc_current(_get(FOMC_CURRENT))
    first_current = min(d.year for d, _, _ in fomc) if fomc else today.year
    for year in range(FIRST_FOMC_YEAR, first_current):
        fomc += parse_fomc_historical(_get(FOMC_HISTORICAL.format(year=year)), year)
    counts["FOMC"] = store_events(conn, "FOMC", fomc, "federalreserve.gov")
    counts["CPI"] = store_events(conn, "CPI", load_cpi_dates(), "bls.gov (checked vs ALFRED)")
    return counts
