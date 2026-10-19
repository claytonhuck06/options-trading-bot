"""Read Alpaca credentials from the environment without ever exposing them.

Credentials are never stored in this repository. They are read from
environment variables (ALPACA_* or Alpaca's own APCA_* names), or from a
git-ignored .env file in the project root.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from core.config import PROJECT_ROOT, ConfigError, Settings

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"

_KEY_NAMES = ("ALPACA_API_KEY_ID", "APCA_API_KEY_ID")
_SECRET_NAMES = ("ALPACA_SECRET_KEY", "APCA_API_SECRET_KEY")
_URL_NAMES = ("ALPACA_BASE_URL", "APCA_API_BASE_URL")


def mask(value: str | None) -> str:
    if not value:
        return "<missing>"
    return f"{value[:2]}…({len(value)} chars)"


@dataclass(frozen=True)
class AlpacaCredentials:
    key_id: str = field(repr=False)
    secret_key: str = field(repr=False)
    base_url: str

    @property
    def is_paper(self) -> bool:
        return self.base_url.rstrip("/") == PAPER_URL

    def __repr__(self) -> str:
        return f"AlpacaCredentials(key_id={mask(self.key_id)}, secret_key=<hidden>, base_url={self.base_url})"

    __str__ = __repr__


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.removeprefix("export ").partition("=")
        values[name.strip()] = value.strip().strip("'\"")
    return values


def _first(names: tuple[str, ...], sources: list[dict[str, str]]) -> str | None:
    for source in sources:
        for name in names:
            if source.get(name):
                return source[name]
    return None


def load_alpaca_credentials(settings: Settings, env: dict[str, str] | None = None, dotenv: Path | None = None) -> AlpacaCredentials:
    sources = [dict(os.environ) if env is None else env, _read_dotenv(dotenv or PROJECT_ROOT / ".env")]
    key_id = _first(_KEY_NAMES, sources)
    secret = _first(_SECRET_NAMES, sources)
    if not key_id or not secret:
        raise ConfigError("Alpaca credentials not found (set ALPACA_API_KEY_ID and ALPACA_SECRET_KEY)")

    want_paper = settings.broker.account_type == "paper"
    base_url = (_first(_URL_NAMES, sources) or (PAPER_URL if want_paper else LIVE_URL)).rstrip("/")
    if base_url not in (PAPER_URL, LIVE_URL):
        raise ConfigError(f"unrecognised Alpaca base URL: {base_url}")

    creds = AlpacaCredentials(key_id=key_id, secret_key=secret, base_url=base_url)

    # A paper environment must never be pointed at a live account, whether by
    # URL or by key. Alpaca paper keys start with "PK"; live keys with "AK".
    if want_paper and (not creds.is_paper or key_id.startswith("AK")):
        raise ConfigError("paper configuration is pointed at a LIVE Alpaca account — refusing to start")
    if not want_paper and (creds.is_paper or key_id.startswith("PK")):
        raise ConfigError("live configuration is pointed at a paper account — refusing to start")
    return creds
