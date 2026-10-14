"""CI prerequisites and explicit, reason-specific backend skip permissions.

Local runs may omit optional native/reference dependencies. CI opts into strict
checks; an unexpected collection or execution skip then fails and appears in
JUnit XML. Add a permission only for a declared implementation-specific test,
never for an unavailable dependency.
"""

import importlib
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Base node IDs cover parametrization without relying on a fixed test count.
PYTHON_ONLY_SKIP_PERMISSIONS = {
    "tests/core/test_semantic_contracts.py::test_native_position_conversion_exceptions_propagate":
        "native position fields require unsigned integers",
    "tests/core/test_state_contracts.py::test_native_metadata_refresh_reuses_an_edited_runtime_table":
        "native table sharing test",
    "tests/core/test_state_contracts.py::test_failed_handoff_preparation_preserves_selected_runtime_and_definitions":
        "native handoff preparation test",
}


def pytest_addoption(parser):
    parser.addoption(
        "--strict-prerequisites", action="store_true",
        help="require native/reference dependencies and reject undeclared skips",
    )


def strict_prerequisites(config):
    return config.getoption("strict_prerequisites", default=False) or (
        os.environ.get("RPLY_STRICT_TESTS", "").lower() in {"1", "true", "yes"}
    )


def verify_backend(requested, components, extension_available):
    """Verify ordinary supported inputs, without banning auto fallback tests."""
    if requested not in {"python", "rust", "auto"}:
        raise pytest.UsageError(f"Invalid RPLY_BACKEND: {requested!r}")
    if not extension_available:
        raise pytest.UsageError(
            "Strict prerequisites require rply._core_rs, including in Python "
            "mode: subprocess tests exercise native mode and auto fallback."
        )
    expected = "python" if requested == "python" else "rust"
    for name, component in components.items():
        if component.backend != expected:
            raise pytest.UsageError(
                f"RPLY_BACKEND={requested}: basic {name} selected "
                f"{component.backend!r}, expected {expected!r}"
            )


def native_prerequisite_available():
    if importlib.util.find_spec("rply._core_rs") is None:
        return False
    try:
        # A .pyd/.so file may exist but fail to load due to ABI or DLL issues.
        # This instrumentation does not change backends.native_core or the
        # requested Python components; ordinary local Python runs stay lazy.
        importlib.import_module("rply._core_rs")
    except Exception as error:
        raise pytest.UsageError(
            "Strict prerequisites found rply._core_rs but could not load it: "
            f"{type(error).__name__}: {error}"
        ) from error
    return True


def pytest_configure(config):
    if not strict_prerequisites(config):
        return
    for name in ("ply", "sly"):
        package = ROOT / "upstream" / name / "src" / name / "__init__.py"
        if not package.is_file():
            raise pytest.UsageError(
                f"Strict prerequisites require upstream/{name}; see upstream/README.md"
            )
    from rply import Grammar, LexerEngine, LRParser, LRTable

    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "N", r"\d+")
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    table = LRTable(grammar)
    parser = LRParser(table)
    verify_backend(
        os.environ.get("RPLY_BACKEND", "auto").lower(),
        {"lexer": lexer, "table builder": table, "parser": parser},
        native_prerequisite_available(),
    )
    if parser.parse(lexer.tokenize("42")) != "42":
        raise pytest.UsageError("Strict backend smoke parse returned an unexpected result")


def pytest_report_header(config):
    if strict_prerequisites(config):
        return "rply strict prerequisites: enabled; skips require explicit permission"


def allowed_skip(nodeid, reason):
    if os.environ.get("RPLY_BACKEND", "auto").lower() != "python":
        return False
    base_nodeid = nodeid.split("[", 1)[0]
    return PYTHON_ONLY_SKIP_PERMISSIONS.get(base_nodeid) == reason


def enforce_skip_permission(config, report):
    if not strict_prerequisites(config) or not report.skipped:
        return
    reason = report.longrepr[2].removeprefix("Skipped: ")
    if allowed_skip(report.nodeid, reason):
        return
    report.outcome = "failed"
    report.longrepr = (
        f"Undeclared skip in strict prerequisite mode: {report.nodeid}\n"
        f"Reason: {reason}\n"
        "Restore the missing prerequisite or declare an exact backend-specific "
        "permission in tests/conftest.py."
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    enforce_skip_permission(item.config, outcome.get_result())


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    outcome = yield
    enforce_skip_permission(collector.config, outcome.get_result())
