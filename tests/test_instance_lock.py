import subprocess
import sys
import textwrap

import pytest

from core.config import PROJECT_ROOT
from core.instance_lock import AlreadyRunning, is_running, single_instance


def test_not_running_until_the_lock_is_held(tmp_path):
    assert not is_running("collector", tmp_path)
    with single_instance("collector", lock_dir=tmp_path):
        assert is_running("collector", tmp_path)
        assert not is_running("trader", tmp_path)
    assert not is_running("collector", tmp_path)


def test_second_copy_is_refused(tmp_path):
    with single_instance("trader", lock_dir=tmp_path):
        with pytest.raises(AlreadyRunning):
            with single_instance("trader", wait=0.2, lock_dir=tmp_path):
                pass


def test_lock_is_released_when_the_holding_process_dies(tmp_path):
    # A crashed or killed job must never block the next start.
    holder = textwrap.dedent(f"""
        import os
        from pathlib import Path
        from core.instance_lock import single_instance
        with single_instance("collector", lock_dir=Path({str(tmp_path)!r})):
            print("held", flush=True)
            os._exit(1)                      # die without cleanup, like a crash
    """)
    out = subprocess.run([sys.executable, "-c", holder], capture_output=True, text=True,
                         timeout=30, cwd=PROJECT_ROOT)
    assert "held" in out.stdout
    assert not is_running("collector", tmp_path)
    with single_instance("collector", wait=0.2, lock_dir=tmp_path):
        pass


def test_a_process_that_only_mentions_the_job_is_not_mistaken_for_it(tmp_path):
    # The old pgrep check counted any command line containing "main.py collect".
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)", "main.py collect"])
    try:
        assert not is_running("collector", tmp_path)
    finally:
        p.kill()


def test_unknown_job_rejected(tmp_path):
    with pytest.raises(ValueError):
        is_running("collect", tmp_path)
