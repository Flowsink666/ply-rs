"""Isolated interpreter runs for imports, environment selection, and native reentry."""

from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def run_python(backend, source):
    env = os.environ.copy()
    env["PLY_RS_BACKEND"] = backend
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-c", source], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30,
    )
