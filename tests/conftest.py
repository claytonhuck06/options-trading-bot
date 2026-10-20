import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def config_dir(tmp_path):
    """A writable copy of the real config/ directory."""
    dst = tmp_path / "config"
    shutil.copytree(ROOT / "config", dst)
    return dst


@pytest.fixture
def edit_yaml():
    def _edit(path: Path, **changes):
        data = yaml.safe_load(path.read_text()) or {}
        for dotted, value in changes.items():
            node = data
            *parents, leaf = dotted.split("__")
            for p in parents:
                node = node.setdefault(p, {})
            node[leaf] = value
        path.write_text(yaml.safe_dump(data))
    return _edit


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("OPTIONS_QUANT_ENV", "OPTIONS_QUANT_LIVE_AUTHORIZATION"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def alerts_sent(monkeypatch):
    """No test ever reaches ntfy: every alert is captured here instead."""
    from monitoring import alerts
    sent = []
    monkeypatch.setenv(alerts.TOPIC_VAR, "test-topic")
    monkeypatch.setattr(alerts, "_post", lambda url, body, headers, timeout: sent.append(
        {"url": url, "title": headers["Title"], "priority": headers["Priority"], "message": body.decode()}))
    return sent
