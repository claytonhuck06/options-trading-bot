"""Logging: readable console output plus an append-only JSON-lines audit file.

A redaction filter scrubs anything that looks like an Alpaca key or secret,
so a stray log statement can never leak credentials.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from core.config import PROJECT_ROOT, Settings

_SECRET_PATTERNS = [
    re.compile(r"\b(?:PK|AK|CK)[A-Z0-9]{14,}\b"),           # Alpaca key ids
    re.compile(r"\b[A-Za-z0-9/+]{40,}\b"),                  # long secret-like tokens
    re.compile(r"(?i)(secret|api[_-]?key|token|password)(\s*[=:]\s*)\S+"),
]


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 2:
            text = pattern.sub(r"\1\2<redacted>", text)
        else:
            text = pattern.sub("<redacted>", text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = record.event
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


def setup_logging(settings: Settings, log_dir: Path | None = None) -> logging.Logger:
    directory = log_dir or PROJECT_ROOT / settings.logging.directory
    directory.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("options_quant")
    root.setLevel(settings.logging.level)
    root.propagate = False
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    file_handler = logging.FileHandler(directory / settings.logging.json_file)
    file_handler.setFormatter(JsonFormatter())

    for handler in (console, file_handler):
        handler.addFilter(RedactionFilter())
        root.addHandler(handler)

    # Keep third-party HTTP chatter (which can include headers) out of logs.
    for noisy in ("urllib3", "alpaca", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"options_quant.{name}")
