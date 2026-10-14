"""Native/reference prerequisites and strict rejection of skipped contracts.

Local runs may omit optional native/reference dependencies. CI opts into strict
checks; an unexpected collection or execution skip then fails and appears in
JUnit XML. The native engine is required in every mode; strict CI runs permit no skips.
"""

import importlib
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

def pytest_addoption(parser):
    parser.addoption(
        "--strict-prerequisites", action="store_true",
        help="require native/reference dependencies and reject undeclared skips",
    )


def strict_prerequisites(config):
    return config.getoption("strict_prerequisites", default=False) or (
        os.environ.get("PLY_RS_STRICT_TESTS", "").lower() in {"1", "true", "yes"}
    )


def verify_backend(requested, components, extension_available):
    """Verify the required engine and its compatibility alias."""
    if requested not in {"rust", "auto"}:
        raise pytest.UsageError(f"Invalid PLY_RS_BACKEND: {requested!r}")
    if not extension_available:
        raise pytest.UsageError(
            "Strict prerequisites require the loadable ply_rs._core_rs native extension."
        )
    expected = "rust"
    for name, component in components.items():
        if component.backend != expected:
            raise pytest.UsageError(
                f"PLY_RS_BACKEND={requested}: basic {name} selected "
                f"{component.backend!r}, expected {expected!r}"
            )


def native_prerequisite_available():
    if importlib.util.find_spec("ply_rs._core_rs") is None:
        return False
    try:
        # A .pyd/.so file may exist but fail to load due to ABI or DLL issues.
        importlib.import_module("ply_rs._core_rs")
    except Exception as error:
        raise pytest.UsageError(
            "Strict prerequisites found ply_rs._core_rs but could not load it: "
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
                f"Strict prerequisites require upstream/{name}; see docs/upstream.md"
            )
    from ply_rs import Grammar, LexerEngine, LRParser, LRTable

    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "N", r"\d+")
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    table = LRTable(grammar)
    parser = LRParser(table)
    verify_backend(
        os.environ.get("PLY_RS_BACKEND", "auto").lower(),
        {"lexer": lexer, "table builder": table, "parser": parser},
        native_prerequisite_available(),
    )
    if parser.parse(lexer.tokenize("42")) != "42":
        raise pytest.UsageError("Strict backend smoke parse returned an unexpected result")


def pytest_report_header(config):
    if strict_prerequisites(config):
        return "ply-rs strict prerequisites: enabled; no skipped contracts"




def enforce_skip_permission(config, report):
    if not strict_prerequisites(config) or not report.skipped:
        return
    reason = report.longrepr[2].removeprefix("Skipped: ")
    report.outcome = "failed"
    report.longrepr = (
        f"Undeclared skip in strict prerequisite mode: {report.nodeid}\n"
        f"Reason: {reason}\n"
        "Restore the missing prerequisite or implement the missing native contract."
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    enforce_skip_permission(item.config, outcome.get_result())


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    outcome = yield
    enforce_skip_permission(collector.config, outcome.get_result())
