import json

from core.config import load_settings
from core.logging_setup import get_logger, redact, setup_logging


def test_redact_patterns():
    assert "PKABCDEFGHIJKLMNOP12" not in redact("key PKABCDEFGHIJKLMNOP12 here")
    assert "hunter2" not in redact("password=hunter2")
    assert "x" * 44 not in redact("token " + "x" * 44)


def test_log_file_is_json_and_redacted(tmp_path):
    setup_logging(load_settings(), log_dir=tmp_path)
    log = get_logger("test")
    log.info("connecting with secret_key=%s and key %s", "y" * 44, "PKABCDEFGHIJKLMNOP12", extra={"event": "unit"})
    for h in log.parent.handlers:
        h.flush()
    lines = (tmp_path / "options_quant.jsonl").read_text().strip().splitlines()
    record = json.loads(lines[-1])
    assert record["event"] == "unit"
    assert "y" * 44 not in record["msg"] and "PKABCDEFGHIJKLMNOP12" not in record["msg"]
