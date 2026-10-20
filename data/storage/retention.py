"""Keep the live collector's database from filling the disk (about 1 GB per trading day).

Policy, counted in trading days that have data (newest first):
  * the newest `keep_full_days` days stay at full detail (every 15-second quote);
  * older days are thinned to one quote per contract per minute (the last one in each minute,
    which is the state of the market at that minute's end), the same resolution as the paid
    historical data;
  * days older than `archive_after_days` move out of the database into compressed Parquet files
    under `archive_path` (one folder per day), checked row-for-row before anything is deleted.
    `restore_day` puts a day back.
Only the live collector's tables are touched. The paid Databento history and the SPY minute bars
live in other files and are never changed here. Space is given back to the disk with VACUUM, which
is skipped while the collector or trader is running.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from core.config import PROJECT_ROOT, Settings
from core.instance_lock import is_running
from core.logging_setup import get_logger

log = get_logger("retention")
ARCHIVED_TABLES = ("option_quotes", "underlying_quotes", "collection_cycles")


@dataclass
class RetentionReport:
    thinned: dict[str, int] = field(default_factory=dict)       # day -> quote rows removed
    archived: dict[str, int] = field(default_factory=dict)      # day -> rows moved to files
    vacuumed: bool = False
    size_before_mb: float = 0.0
    size_after_mb: float = 0.0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"database {self.size_before_mb:,.0f} MB -> {self.size_after_mb:,.0f} MB"]
        if self.thinned:
            parts.append(f"thinned {len(self.thinned)} day(s)")
        if self.archived:
            parts.append(f"archived {len(self.archived)} day(s)")
        return "; ".join(parts + self.notes)


def _day_of(column: str) -> str:
    return f"substr({column}, 1, 10)"            # timestamps are ISO UTC; a US session never crosses UTC midnight


def data_days(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(f"SELECT DISTINCT {_day_of('started_at')} FROM collection_cycles ORDER BY 1 DESC")]


def thin_day(conn: sqlite3.Connection, day: str) -> int:
    """Keep the last quote per contract per minute. Returns rows removed."""
    cur = conn.execute(
        f"DELETE FROM option_quotes WHERE {_day_of('received_at')} = ? AND id NOT IN ("
        f"  SELECT MAX(id) FROM option_quotes WHERE {_day_of('received_at')} = ? GROUP BY symbol, substr(received_at, 1, 16))",
        (day, day))
    conn.commit()
    return cur.rowcount


def _is_thin(conn: sqlite3.Connection, day: str) -> bool:
    row = conn.execute(f"SELECT COUNT(*), COUNT(DISTINCT symbol || substr(received_at, 1, 16)) FROM option_quotes "
                       f"WHERE {_day_of('received_at')} = ?", (day,)).fetchone()
    return row[0] == row[1]


def _time_column(table: str) -> str:
    return "started_at" if table == "collection_cycles" else "received_at"


def archive_day(conn: sqlite3.Connection, day: str, archive_dir: Path) -> int:
    """Write the day's rows to compressed Parquet, verify them, then delete them from the database."""
    out = archive_dir / day
    out.mkdir(parents=True, exist_ok=True)
    moved = 0
    for table in ARCHIVED_TABLES:
        df = pd.read_sql_query(f"SELECT * FROM {table} WHERE {_day_of(_time_column(table))} = ?", conn, params=(day,))
        path = out / f"{table}.parquet"
        df.to_parquet(path, compression="zstd", index=False)
        if len(pd.read_parquet(path)) != len(df):
            raise OSError(f"archive check failed for {table} on {day}; nothing deleted")
        moved += len(df)
    for table in ARCHIVED_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE {_day_of(_time_column(table))} = ?", (day,))
    conn.commit()
    return moved


def restore_day(conn: sqlite3.Connection, day: str, archive_dir: Path) -> int:
    """Put an archived day back into the database (e.g. to re-run a report on it)."""
    folder = archive_dir / day
    if not folder.is_dir():
        raise FileNotFoundError(f"no archive for {day}")
    restored = 0
    for table in reversed(ARCHIVED_TABLES):          # cycles first: quotes refer to them
        df = pd.read_parquet(folder / f"{table}.parquet")
        if df.empty:
            continue
        cols = ",".join(df.columns)
        marks = ",".join("?" * len(df.columns))
        rows = [tuple(None if pd.isna(v) else v for v in r) for r in df.itertuples(index=False)]
        conn.executemany(f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({marks})", rows)
        restored += len(df)
    conn.commit()
    shutil.rmtree(folder)
    return restored


def _busy() -> bool:
    """True while the collector or trader is running (VACUUM needs the database to itself)."""
    try:
        return is_running("collector") or is_running("trader")
    except OSError:
        return True


def run_retention(settings: Settings, db_path: Path | None = None, vacuum: bool = True) -> RetentionReport:
    cfg = settings.storage
    path = db_path or PROJECT_ROOT / cfg.sqlite_path
    archive_dir = PROJECT_ROOT / cfg.archive_path
    rep = RetentionReport(size_before_mb=path.stat().st_size / 1e6 if path.exists() else 0.0)
    conn = sqlite3.connect(path, timeout=60)
    try:
        days = data_days(conn)
        for i, day in enumerate(days):
            if i >= cfg.archive_after_days:
                rep.archived[day] = archive_day(conn, day, archive_dir)
            elif i >= cfg.keep_full_days and not _is_thin(conn, day):
                rep.thinned[day] = thin_day(conn, day)
        free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0] * conn.execute("PRAGMA page_size").fetchone()[0]
        if vacuum and free_pages > 50e6:          # more than 50 MB of deleted rows waiting to be given back
            if _busy():
                rep.notes.append("space not yet given back (collector or trader running); next run will")
            elif shutil.disk_usage(path.parent).free < path.stat().st_size * 1.1:
                rep.notes.append("space not given back: not enough free disk for VACUUM")
            else:
                conn.execute("VACUUM")
                rep.vacuumed = True
    finally:
        conn.close()
    rep.size_after_mb = path.stat().st_size / 1e6 if path.exists() else 0.0
    log.info("storage retention: %s", rep.summary())
    return rep
