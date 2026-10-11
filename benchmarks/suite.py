"""Realistic parser benchmarks and optional upstream comparisons (stdlib only)."""

import argparse
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import timeit

from adapters import ROOT, build, load_reference
from workloads import DEFAULT_SIZES, make_workload

ENGINES = ("python", "rust", "ply", "sly")
OPERATIONS = ("lex", "parse", "end-to-end", "build")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def git_info(directory):
    def run(*args):
        result = subprocess.run(["git", "-C", str(directory), *args],
                                capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else None
    try:
        return {"commit": run("rev-parse", "HEAD"),
                "dirty": bool(run("status", "--porcelain"))}
    except (OSError, subprocess.TimeoutExpired):
        return {"commit": None, "dirty": None}


def engine_info(engine):
    if engine in ("python", "rust"):
        import rply
        info = {"source": str(Path(rply.__file__).resolve())}
        if engine == "rust":
            from rply.backends import native_core
            artifact = Path(native_core.__file__).resolve()
            info.update(native_extension=str(artifact),
                        native_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
        return info
    package = load_reference(engine)
    info = {"source": str(Path(package.__file__).resolve()),
            "version": getattr(package, "__version__", None)}
    clone = ROOT / "upstream" / engine
    if (clone / ".git").exists():
        info.update(git_info(clone))
    return info


def measure(operation, options, byte_count, token_count, throughput=True):
    for _ in range(options.warmup):
        operation()
    # Include Python GC costs for allocation-heavy AST/JSON/build workloads.
    samples = [elapsed * 1000 / options.iterations for elapsed in
               timeit.repeat(operation, setup=gc.enable, repeat=options.repeat,
                             number=options.iterations)]
    median = statistics.median(samples)
    result = {"median_ms": median, "min_ms": min(samples), "max_ms": max(samples),
              "stdev_ms": statistics.stdev(samples) if len(samples) > 1 else 0.0,
              "samples_ms": samples}
    if throughput:
        result.update(tokens_per_second=token_count * 1000 / median,
                      mib_per_second=byte_count * 1000 / median / (1024 * 1024))
    return result


def worker(options):
    engine = options.engine[0]
    os.environ["RPLY_BACKEND"] = engine if engine in ("python", "rust") else "python"
    sys.path.insert(0, str(ROOT / "src"))
    cases = {}
    for name in options.case:
        workload = make_workload(name, options.size, options.grammar_width)
        runtime = build(workload, engine)
        tokens = list(runtime.tokenize(workload.text))
        if not tokens:
            raise AssertionError(f"{engine}/{name}: empty token stream")
        # Repetition detects reused cursors, mutated results, or parser state leaks.
        for _ in range(2):
            if runtime.parse(tokens) != workload.expected:
                raise AssertionError(f"{engine}/{name}: incorrect pre-tokenized parse")
            if runtime.parse(runtime.tokenize(workload.text)) != workload.expected:
                raise AssertionError(f"{engine}/{name}: incorrect streaming parse")
        byte_count = len(workload.text.encode("utf-8"))
        result = {
            "size": workload.size, "input_bytes": byte_count, "token_count": len(tokens),
            "lexer_rules": len(workload.tokens), "productions": len(workload.rules),
            "states": runtime.state_count,
            "input_sha256": hashlib.sha256(workload.text.encode("utf-8")).hexdigest(),
            "tokens_sha256": digest([(tok.type, tok.value) for tok in tokens]),
            "result_sha256": digest(workload.expected), "verified": True, "timings": {},
        }
        if not options.check:
            operations = {
                "lex": lambda: list(runtime.tokenize(workload.text)),
                "parse": lambda: runtime.parse(tokens),
                "end-to-end": lambda: runtime.parse(runtime.tokenize(workload.text)),
                "build": lambda: build(workload, engine),
            }
            for operation in options.operation:
                result["timings"][operation] = measure(
                    operations[operation], options, byte_count, len(tokens), operation != "build")
        cases[name] = result
        print(f"Verified {engine}/{name}: {len(tokens):,} tokens, {runtime.state_count} states", file=sys.stderr)
    return {engine: {"implementation": engine_info(engine), "cases": cases}}


def validate_comparison(results):
    baseline = next(iter(results.values()))["cases"]
    for engine, report in results.items():
        for name, case in report["cases"].items():
            for key in ("input_sha256", "tokens_sha256", "result_sha256", "token_count"):
                if case[key] != baseline[name][key]:
                    raise AssertionError(f"Comparison invalid: {engine}/{name} differs in {key}")


def print_summary(results):
    print("\nengine  workload          tokens       lex ms   parse ms     e2e ms   build ms", file=sys.stderr)
    for engine, report in results.items():
        for name, case in report["cases"].items():
            timings = case["timings"]
            cells = [f"{timings[op]['median_ms']:10.3f}" if op in timings else f"{'-':>10}"
                     for op in OPERATIONS]
            print(f"{engine:<7} {name:<17} {case['token_count']:7,d} " + " ".join(cells), file=sys.stderr)


def positive(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", nargs="+", choices=ENGINES, default=["python", "rust"])
    parser.add_argument("--case", nargs="+", choices=tuple(DEFAULT_SIZES), default=list(DEFAULT_SIZES))
    parser.add_argument("--operation", nargs="+", choices=OPERATIONS, default=list(OPERATIONS))
    parser.add_argument("--size", type=positive, help="override each selected workload's default size")
    parser.add_argument("--grammar-width", type=positive, default=64, help="commands in wide-grammar")
    parser.add_argument("--repeat", type=positive, default=5)
    parser.add_argument("--iterations", type=positive, default=5)
    parser.add_argument("--warmup", type=positive, default=2)
    parser.add_argument("--check", action="store_true", help="validate only, without timing")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args()
    for name in ("engine", "case", "operation"):
        values = getattr(options, name)
        if len(values) != len(set(values)):
            parser.error(f"--{name} must not contain duplicates")
    if options.worker:
        if len(options.engine) != 1:
            parser.error("a worker requires exactly one engine")
        print(json.dumps(worker(options), ensure_ascii=True))
        return

    results = {}
    for engine in options.engine:
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--engine", engine,
                   "--case", *options.case, "--operation", *options.operation,
                   "--grammar-width", str(options.grammar_width), "--repeat", str(options.repeat),
                   "--iterations", str(options.iterations), "--warmup", str(options.warmup)]
        if options.size is not None:
            command.extend(["--size", str(options.size)])
        if options.check:
            command.append("--check")
        env = os.environ.copy()
        env.update(PYTHONHASHSEED="0", PYTHONIOENCODING="utf-8")
        completed = subprocess.run(command, env=env, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        if completed.returncode:
            parser.exit(1, f"Benchmark engine {engine} failed; no comparison report written.\n")
        results.update(json.loads(completed.stdout))
    validate_comparison(results)
    report = {
        "schema_version": 1, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "executable": sys.executable, "platform": platform.platform(),
        "machine": platform.machine(), "processor": platform.processor(), "repository": git_info(ROOT),
        "settings": {"repeat": options.repeat, "iterations": options.iterations, "warmup": options.warmup,
                     "grammar_width": options.grammar_width, "check_only": options.check,
                     "gc_enabled": True, "pythonhashseed": "0", "timer": "timeit/perf_counter"},
        "results": results,
    }
    print_summary(results)
    encoded = json.dumps(report, indent=2, ensure_ascii=True)
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
