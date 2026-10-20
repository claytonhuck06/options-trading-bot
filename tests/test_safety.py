"""Structural guarantees about who can touch the broker.

Only execution/alpaca_paper.py may call Alpaca's order-writing methods, and only
it and the read-only broker may create an Alpaca trading client at all.
"""

import re
from pathlib import Path

import pytest

from core.config import ConfigError, load_settings
from core.secrets import AlpacaCredentials, LIVE_URL, PAPER_URL
from execution.broker import ReadOnlyBroker

ROOT = Path(__file__).resolve().parent.parent
WRITE_CALLS = re.compile(r"\b(submit_order|replace_order_by_id|cancel_order_by_id|cancel_orders|close_position|"
                         r"close_all_positions|exercise_options_position)\b")
ORDER_WRITER = Path("execution/alpaca_paper.py")
CLIENT_OWNERS = {Path("execution/alpaca_paper.py"), Path("execution/broker.py")}


def project_files():
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] not in (".venv", "tests", ".claude"):        # .claude: side-session worktree copies
            yield rel, path.read_text()


def test_read_only_broker_has_no_write_methods():
    public = [n for n in dir(ReadOnlyBroker) if not n.startswith("_")]
    for name in public:
        assert not re.search(r"submit|order_create|replace|cancel|close|exercise|liquidate", name), name


def test_only_the_paper_broker_can_write_orders():
    offenders = [str(rel) for rel, text in project_files() if WRITE_CALLS.search(text) and rel != ORDER_WRITER]
    assert offenders == [], f"order-writing calls outside {ORDER_WRITER}: {offenders}"


def test_only_two_files_create_trading_clients():
    offenders = [str(rel) for rel, text in project_files() if "TradingClient(" in text and rel not in CLIENT_OWNERS]
    assert offenders == [], offenders


def test_paper_broker_hardcodes_paper_endpoint():
    text = (ROOT / ORDER_WRITER).read_text()
    assert "paper=True" in text and "paper=False" not in text


def test_paper_broker_refuses_when_submission_disabled():
    from execution.alpaca_paper import AlpacaPaperBroker
    creds = AlpacaCredentials("PK" + "X" * 24, "s" * 44, PAPER_URL)
    with pytest.raises(ConfigError, match="disabled"):
        AlpacaPaperBroker(load_settings("development"), creds)


def test_paper_broker_refuses_live_credentials(config_dir, edit_yaml):
    from execution.alpaca_paper import AlpacaPaperBroker
    edit_yaml(config_dir / "environments" / "paper.yaml", trading__enabled=True, trading__allow_order_submission=True)
    settings = load_settings("paper", config_dir=config_dir)
    with pytest.raises(ConfigError, match="PAPER"):
        AlpacaPaperBroker(settings, AlpacaCredentials("AK" + "X" * 24, "s" * 44, LIVE_URL))
    with pytest.raises(ConfigError, match="PAPER"):
        AlpacaPaperBroker(settings, AlpacaCredentials("AK" + "X" * 24, "s" * 44, PAPER_URL))


def test_gitignore_protects_secrets():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in ignored
