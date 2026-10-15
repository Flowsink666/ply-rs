"""Load pinned optional reference packages under isolated module names."""

from pathlib import Path
import importlib
import importlib.util
import os
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_reference(name):
    alias = f"_ply_rs_reference_{name}"
    if alias in sys.modules:
        return sys.modules[alias]
    package = ROOT / "upstream" / name / "src" / name / "__init__.py"
    if not package.is_file():
        if os.environ.get("PLY_RS_STRICT_TESTS", "").lower() in {"1", "true", "yes"}:
            pytest.fail(f"Missing required upstream/{name} reference clone; see docs/upstream.md")
        pytest.skip(f"Missing upstream/{name} reference clone; see docs/upstream.md")
    spec = importlib.util.spec_from_file_location(alias, package)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(alias, None)
        raise
    return module


def load_ply_reference():
    package = load_reference("ply")
    return (
        importlib.import_module(f"{package.__name__}.lex"),
        importlib.import_module(f"{package.__name__}.yacc"),
    )
