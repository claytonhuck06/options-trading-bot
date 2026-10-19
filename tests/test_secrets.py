import pytest

from core.config import ConfigError, load_settings
from core.secrets import LIVE_URL, PAPER_URL, load_alpaca_credentials

FAKE_PAPER_KEY = "PKTESTTESTTESTTESTTEST1234"
FAKE_LIVE_KEY = "AKTESTTESTTESTTESTTEST1234"
FAKE_SECRET = "s" * 44


@pytest.fixture
def paper_settings():
    return load_settings("paper")


def load(settings, tmp_path, **env):
    return load_alpaca_credentials(settings, env=env, dotenv=tmp_path / "none.env")


def test_loads_paper_credentials(paper_settings, tmp_path):
    c = load(paper_settings, tmp_path, ALPACA_API_KEY_ID=FAKE_PAPER_KEY, ALPACA_SECRET_KEY=FAKE_SECRET, ALPACA_BASE_URL=PAPER_URL)
    assert c.is_paper


def test_accepts_alpaca_official_variable_names(paper_settings, tmp_path):
    c = load(paper_settings, tmp_path, APCA_API_KEY_ID=FAKE_PAPER_KEY, APCA_API_SECRET_KEY=FAKE_SECRET)
    assert c.base_url == PAPER_URL


def test_reads_dotenv_file(paper_settings, tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"ALPACA_API_KEY_ID={FAKE_PAPER_KEY}\nexport ALPACA_SECRET_KEY='{FAKE_SECRET}'\n")
    assert load_alpaca_credentials(paper_settings, env={}, dotenv=f).is_paper


def test_missing_credentials_rejected(paper_settings, tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load(paper_settings, tmp_path)


def test_paper_settings_refuse_live_url(paper_settings, tmp_path):
    with pytest.raises(ConfigError, match="LIVE"):
        load(paper_settings, tmp_path, ALPACA_API_KEY_ID=FAKE_PAPER_KEY, ALPACA_SECRET_KEY=FAKE_SECRET, ALPACA_BASE_URL=LIVE_URL)


def test_paper_settings_refuse_live_key(paper_settings, tmp_path):
    with pytest.raises(ConfigError, match="LIVE"):
        load(paper_settings, tmp_path, ALPACA_API_KEY_ID=FAKE_LIVE_KEY, ALPACA_SECRET_KEY=FAKE_SECRET, ALPACA_BASE_URL=PAPER_URL)


def test_unknown_url_rejected(paper_settings, tmp_path):
    with pytest.raises(ConfigError, match="unrecognised"):
        load(paper_settings, tmp_path, ALPACA_API_KEY_ID=FAKE_PAPER_KEY, ALPACA_SECRET_KEY=FAKE_SECRET, ALPACA_BASE_URL="https://evil.example")


def test_repr_never_shows_secrets(paper_settings, tmp_path):
    c = load(paper_settings, tmp_path, ALPACA_API_KEY_ID=FAKE_PAPER_KEY, ALPACA_SECRET_KEY=FAKE_SECRET)
    text = repr(c) + str(c) + f"{c}"
    assert FAKE_PAPER_KEY not in text and FAKE_SECRET not in text
