"""Benchmark integrity: validate workloads, isolation, and usable sampling output."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "benchmarks" / "suite.py"


def run_suite(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_all_workloads_validate_with_native_engine():
    result = run_suite("--engine", "rust", "--size", "3", "--grammar-width", "8", "--check")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    cases = report["results"]["rust"]["cases"]
    assert set(cases) == {"arithmetic", "nested", "json", "json-unicode", "mini-language", "wide-grammar"}
    assert all(case["verified"] and case["token_count"] > 0 for case in cases.values())
    assert all(case["timings"] == {} for case in cases.values())
    assert cases["json-unicode"]["input_bytes"] > cases["json"]["input_bytes"]
    assert cases["wide-grammar"]["states"] > cases["arithmetic"]["states"]


def test_measurements_save_raw_samples_and_throughput(tmp_path):
    output = tmp_path / "report.json"
    result = run_suite("--engine", "rust", "--case", "json-unicode", "--size", "2",
                       "--repeat", "2", "--iterations", "2", "--warmup", "1", "--output", str(output))
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report == json.loads(result.stdout)
    timings = report["results"]["rust"]["cases"]["json-unicode"]["timings"]
    assert set(timings) == {"lex", "parse", "end-to-end", "build"}
    for measurement in timings.values():
        assert len(measurement["samples_ms"]) == 2
        assert 0 < measurement["min_ms"] <= measurement["median_ms"] <= measurement["max_ms"]
    assert timings["lex"]["tokens_per_second"] > 0
    assert timings["end-to-end"]["mib_per_second"] > 0
    assert "tokens_per_second" not in timings["build"]


@pytest.mark.parametrize("args", [
    ("--size", "0"), ("--repeat", "-1"), ("--iterations", "0"),
    ("--grammar-width", "0"), ("--engine", "rust", "rust"),
])
def test_invalid_sampling_options_fail_before_running(args):
    result = run_suite(*args)
    assert result.returncode == 2
    assert "Verified" not in result.stderr
