"""Structural guarantees that Phase 1 cannot place, change or cancel orders."""

import re
from pathlib import Path

from execution.broker import ReadOnlyBroker

ROOT = Path(__file__).resolve().parent.parent
WRITE_CALLS = re.compile(r"\b(submit_order|replace_order|cancel_order\w*|cancel_orders|close_position|close_all_positions|exercise_options_position)\b")


def test_read_only_broker_has_no_write_methods():
    public = [n for n in dir(ReadOnlyBroker) if not n.startswith("_")]
    for name in public:
        assert not re.search(r"submit|order_create|replace|cancel|close|exercise|liquidate", name), name


def test_no_order_write_calls_anywhere_in_project_code():
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in (".venv", "tests"):
            continue
        if WRITE_CALLS.search(path.read_text()):
            offenders.append(str(rel))
    assert offenders == [], f"order-writing calls found in: {offenders}"


def test_gitignore_protects_secrets():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in ignored
