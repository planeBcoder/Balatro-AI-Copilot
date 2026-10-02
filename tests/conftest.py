import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def state_data():
    return json.loads((ROOT / "tests/fixtures/observed_state.json").read_text(encoding="utf-8"))
