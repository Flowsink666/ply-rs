"""Exercise skip gates as real pytest runs, including their JUnit outcome."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

pytest_plugins = ["pytester"]
ROOT = Path(__file__).resolve().parents[2]
GATES = ROOT / "tests" / "conftest.py"


@pytest.fixture(autouse=True)
def utf8_pytester_subprocesses(monkeypatch):
    # Pytester decodes captured subprocess output as UTF-8. Older Windows
    # Python versions otherwise emit localized warnings in the ANSI code page.
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.setenv("PYTHONUTF8", "1")


@pytest.fixture
def gate_module():
    spec = importlib.util.spec_from_file_location("_rply_test_gates", GATES)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def wheel_verifier():
    spec = importlib.util.spec_from_file_location(
        "_rply_wheel_verifier", ROOT / ".github" / "scripts" / "verify_wheel.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gated_run(pytester, monkeypatch):
    monkeypatch.setenv("RPLY_STRICT_TESTS", "1")
    monkeypatch.setenv("RPLY_BACKEND", "python")
    pytester.makeini("[pytest]\n")
    # Skip-report tests isolate the pytest hooks from dependency preflight.
    # The preflight itself is exercised separately below.
    pytester.makeconftest(GATES.read_text(encoding="utf-8") + "\ndef pytest_configure(config):\n    pass\n")
    return pytester


def test_undeclared_execution_skip_fails_and_is_recorded_in_junit(gated_run):
    gated_run.makepyfile("import pytest\ndef test_probe():\n    pytest.skip('missing prerequisite')\n")
    result = gated_run.runpytest_subprocess("-q", "--junitxml=report.xml")
    result.assert_outcomes(failed=1)
    report = ET.parse(gated_run.path / "report.xml")
    failure = report.find(".//failure")
    assert failure is not None and "Undeclared skip" in failure.text
    assert report.find(".//skipped") is None


def test_undeclared_collection_skip_is_an_error(gated_run):
    gated_run.makepyfile("import pytest\npytest.skip('module prerequisite', allow_module_level=True)\n")
    result = gated_run.runpytest_subprocess("-q", "--junitxml=report.xml")
    result.assert_outcomes(errors=1)
    assert result.ret != 0
    assert ET.parse(gated_run.path / "report.xml").find(".//error") is not None


def test_declared_python_skip_retains_its_reason(gated_run):
    gated_run.makeconftest(
        (gated_run.path / "conftest.py").read_text(encoding="utf-8")
        + "\nPYTHON_ONLY_SKIP_PERMISSIONS = {'test_probe.py::test_probe': 'declared native contract'}\n"
    )
    gated_run.makepyfile(test_probe="import pytest\ndef test_probe():\n    pytest.skip('declared native contract')\n")
    result = gated_run.runpytest_subprocess("-q", "--junitxml=report.xml")
    result.assert_outcomes(skipped=1)
    skipped = ET.parse(gated_run.path / "report.xml").find(".//skipped")
    assert skipped is not None and skipped.attrib["message"] == "declared native contract"


def test_missing_native_dependency_is_never_a_permitted_skip(gated_run):
    gated_run.makepyfile("import pytest\ndef test_probe():\n    pytest.skip('native extension unavailable')\n")
    gated_run.runpytest_subprocess("-q").assert_outcomes(failed=1)


def test_local_execution_skip_remains_optional(gated_run, monkeypatch):
    monkeypatch.delenv("RPLY_STRICT_TESTS")
    gated_run.makepyfile("import pytest\ndef test_probe():\n    pytest.skip('optional local reference')\n")
    gated_run.runpytest_subprocess("-q").assert_outcomes(skipped=1)


def test_cli_option_enables_the_same_gate(gated_run, monkeypatch):
    monkeypatch.delenv("RPLY_STRICT_TESTS")
    gated_run.makepyfile("import pytest\ndef test_probe():\n    pytest.skip('optional local reference')\n")
    gated_run.runpytest_subprocess("-q", "--strict-prerequisites").assert_outcomes(failed=1)


@pytest.mark.parametrize("reference", ["ply", "sly"])
def test_missing_reference_fails_during_strict_preflight(pytester, monkeypatch, reference):
    monkeypatch.setenv("RPLY_STRICT_TESTS", "1")
    pytester.makeini("[pytest]\n")
    other = "sly" if reference == "ply" else "ply"
    package = pytester.path / "upstream" / other / "src" / other
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    pytester.makeconftest(GATES.read_text(encoding="utf-8") + "\nROOT = Path(__file__).resolve().parent\n")
    pytester.makepyfile("def test_probe():\n    pass\n")
    result = pytester.runpytest_subprocess("-q")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([f"*Strict prerequisites require upstream/{reference}*"])


@pytest.mark.parametrize("requested, expected", [("python", "python"), ("rust", "rust"), ("auto", "rust")])
def test_backend_preflight_checks_basic_components(gate_module, requested, expected):
    components = {name: SimpleNamespace(backend=expected) for name in ("lexer", "table", "parser")}
    gate_module.verify_backend(requested, components, True)
    components["table"].backend = "rust" if expected == "python" else "python"
    with pytest.raises(pytest.UsageError, match="basic table selected"):
        gate_module.verify_backend(requested, components, True)


def test_python_preflight_still_requires_native_for_subprocess_contracts(gate_module):
    with pytest.raises(pytest.UsageError, match="including in Python mode"):
        gate_module.verify_backend("python", {}, False)


@pytest.mark.parametrize("exception_type", [ImportError, OSError])
def test_found_but_unloadable_native_fails_preflight(gate_module, monkeypatch, exception_type):
    monkeypatch.setattr(gate_module.importlib.util, "find_spec", lambda name: object())

    def broken_import(name):
        raise exception_type("native ABI or shared library mismatch")

    monkeypatch.setattr(gate_module.importlib, "import_module", broken_import)
    with pytest.raises(pytest.UsageError, match="found rply._core_rs but could not load it"):
        gate_module.native_prerequisite_available()


def test_skip_permission_requires_matching_node_reason_and_backend(gate_module, monkeypatch):
    node = "tests/core/test_state_contracts.py::test_native_metadata_refresh_reuses_an_edited_runtime_table"
    monkeypatch.setenv("RPLY_BACKEND", "python")
    assert gate_module.allowed_skip(node + "[parameter]", "native table sharing test")
    assert not gate_module.allowed_skip(node, "native extension unavailable")
    assert not gate_module.allowed_skip(node + "_other", "native table sharing test")
    monkeypatch.setenv("RPLY_BACKEND", "rust")
    assert not gate_module.allowed_skip(node, "native table sharing test")


def test_wheel_verifier_accepts_installed_python_and_native_origins(wheel_verifier, tmp_path):
    installed = tmp_path / "venv" / "site-packages"
    modules = {
        "rply": SimpleNamespace(__file__=installed / "rply" / "__init__.py"),
        "rply._core_rs": SimpleNamespace(__file__=installed / "rply" / "_core_rs.so"),
    }
    wheel_verifier.verify_loaded_origins(modules, [installed])


def test_wheel_verifier_rejects_native_from_checkout_despite_installed_package(wheel_verifier, tmp_path, monkeypatch):
    installed = tmp_path / "venv" / "site-packages"
    checkout = tmp_path / "checkout"
    monkeypatch.setenv("RPLY_CHECKOUT", str(checkout))
    modules = {
        "rply": SimpleNamespace(__file__=installed / "rply" / "__init__.py"),
        "rply._core_rs": SimpleNamespace(__file__=checkout / "src" / "rply" / "_core_rs.so"),
    }
    with pytest.raises(AssertionError, match=r"rply\._core_rs.*outside installed site-packages"):
        wheel_verifier.verify_loaded_origins(modules, [installed])
