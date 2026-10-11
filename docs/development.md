# Development

## Install and build

Use Python 3.9 or newer and a Rust toolchain. From the project root, create and
activate a virtual environment. On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install maturin pytest
python -m maturin develop --release
```

On POSIX shells, activate with `source .venv/bin/activate` instead.

The Python package and native extension are configured together in
`pyproject.toml` under `[tool.maturin]`. `Cargo.toml` describes the Rust crate
and dependencies. `cargo build --release` alone does not install the extension
into the Python package. Build a distributable wheel with:

```powershell
python -m maturin build --release
```

For Rust unit tests, point PyO3 at the active interpreter when it cannot find
one automatically:

```powershell
$env:PYO3_PYTHON = (Get-Command python).Source
cargo test
```

Running directly from Python sources supports the fallback engine without
Rust; building the mixed Python/Rust distribution requires Rust.

## Run tests

Run from the project root; pytest adds `src` to the import path:

```powershell
$env:RPLY_BACKEND = 'python'
python -m pytest -q
$env:RPLY_BACKEND = 'rust'
python -m pytest -q
Remove-Item Env:RPLY_BACKEND
```

On POSIX shells, use `RPLY_BACKEND=python python -m pytest -q` and
`RPLY_BACKEND=rust python -m pytest -q`.

Explicit Rust mode ensures native initialization errors fail the tests instead
of hiding behind Python fallback. The same behavioral suite runs against both
backends. Backend policy tests start separate Python processes because
selection happens at import time.

`tests/differential` compares token streams and parse results with real PLY
and SLY implementations using shared fixtures. Reference clones are optional;
missing clones produce explicit skips. Follow [the upstream instructions](../upstream/README.md)
to reproduce their pinned revisions. Run only these tests with
`python -m pytest -q -m differential`, or omit them with `-m 'not differential'`.

## Run the example

After `maturin develop`, run `python examples/calculator.py` (expected: `20`).
For source-only use, set `PYTHONPATH=src` and `RPLY_BACKEND=python` first.

## Measure performance

Run `python benchmarks/run.py --backend both --output target/benchmarks/current.json`
after building the native extension. Backend selection happens in separate
processes. The seven scenarios measure scanning, parsing an existing token list,
end-to-end execution, table construction, and parser initialization independently. See
[benchmark details](../benchmarks/README.md) for interpretation and limitations.

For larger application workloads and optional upstream comparison, run:

```powershell
python benchmarks/suite.py --engine python rust ply sly --output target/benchmarks/suite-current.json
```

Use `--engine python` for source-only execution. `--check` validates results
without collecting timings; `--case`, `--size`, and `--grammar-width` select
and scale the workloads. The benchmark guide explains the timing boundaries.

Native lexer rules support `re.IGNORECASE`, `re.MULTILINE`, `re.DOTALL`,
`re.UNICODE`, `re.VERBOSE`, and combinations of these flags. Unsupported flags
(including `re.ASCII`) cause a warning and Python fallback in `auto` mode,
and an initialization error in explicit `rust` mode. The two regex engines
still differ in syntax and some Unicode matching details; run application
regression tests in both modes when using advanced patterns.

## Repository hygiene

Keep `Cargo.lock` under version control for reproducible development builds.
Build output, binary extensions, caches, virtual environments, and independent
upstream clones are ignored. Source files use LF endings through
`.gitattributes`. Upstream source remains in its own repositories and retains
its own license.
