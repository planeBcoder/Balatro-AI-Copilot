import subprocess
import sys
import uuid
from pathlib import Path

from core.single_instance import SingleInstance


def test_second_launch_notifies_running_instance():
    name = "CopilotTest" + uuid.uuid4().hex
    first = SingleInstance(name)
    try:
        assert not first.existing
        assert not first.requested()
        code = "from core.single_instance import SingleInstance; s=SingleInstance(" + repr(name) + "); assert s.existing; s.request_open(); s.close()"
        subprocess.run([sys.executable, "-c", code], check=True, timeout=10, cwd=Path(__file__).resolve().parents[1])
        assert first.requested()
        assert not first.requested()
        code = "from core.single_instance import SingleInstance; s=SingleInstance(" + repr(name) + "); assert s.existing; s.request_quit(); s.close()"
        subprocess.run([sys.executable, "-c", code], check=True, timeout=10, cwd=Path(__file__).resolve().parents[1])
        assert first.quit_requested()
        assert not first.quit_requested()
    finally:
        first.close()
    replacement = SingleInstance(name)
    try:
        assert not replacement.existing
    finally:
        replacement.close()
