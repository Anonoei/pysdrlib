"""Every script in examples/ runs to completion (on the simulated radio: no hardware). The guides embed them, so this is
what keeps the documentation's code working."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.py"))
NEEDS = {"remote.py": "zmq"}   # an example that needs an optional dependency


@pytest.mark.parametrize("script", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_example_runs(script):
    module = NEEDS.get(script.name)
    if module and importlib.util.find_spec(module) is None:
        pytest.skip(f"needs {module}")
    env = dict(os.environ, PYTHONWARNINGS="ignore")
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120, env=env)
    assert result.returncode == 0, result.stderr[-3000:]


def test_there_are_examples():
    assert len(EXAMPLES) >= 7
