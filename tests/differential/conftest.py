"""Load reference clones under private names without modifying sys.path."""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_reference(name):
    alias = f"_rply_reference_{name}"
    if alias in sys.modules:
        return sys.modules[alias]
    package = ROOT / "upstream" / name / "src" / name / "__init__.py"
    if not package.is_file():
        if os.environ.get("RPLY_STRICT_TESTS", "").lower() in {"1", "true", "yes"}:
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


@pytest.fixture(scope="session")
def upstream_ply():
    package = load_reference("ply")
    # PLY does not re-export lex and yacc in its package initializer.
    import importlib
    return (
        importlib.import_module(f"{package.__name__}.lex"),
        importlib.import_module(f"{package.__name__}.yacc"),
    )


@pytest.fixture(scope="session")
def upstream_sly():
    return load_reference("sly")
