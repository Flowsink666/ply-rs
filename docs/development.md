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

Rebuild with `maturin develop --release` after changing native sources or
updating a checkout that changes the runtime interface. An older installed
extension may fail native setup; auto mode reports that failure and uses
Python, while explicit Rust mode raises an initialization error.

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

The [runtime contracts](../tests/CONTRACTS.md) define the behavior that changes
must preserve. Bounded operation sequences use independent small models; the
recovery suite checks all 3280 short inputs in editable and frozen modes and
also compares independently built PLY tables. Known callback/precedence policy
differences are asserted explicitly.

CI enables strict prerequisites. To reproduce that gate after building the
extension and checking out both references:

```powershell
$env:RPLY_BACKEND = 'rust'
python -m pytest -q --strict-prerequisites --junitxml=target/contracts-rust.xml
$env:RPLY_BACKEND = 'python'
python -m pytest -q --strict-prerequisites --junitxml=target/contracts-python.xml
Remove-Item Env:RPLY_BACKEND
```

`RPLY_STRICT_TESTS=1` enables the same gate. Strict runs require a loadable native
extension even in Python mode because subprocess contracts exercise native and
auto selection. Basic lexer, table, and parser selection is checked up front;
auto-mode tests may still exercise supported fallback behavior afterward.
Missing prerequisites and undeclared collection/execution skips fail. Exact
backend-specific node/reason permissions in `tests/conftest.py` retain legitimate
Python-mode skips, with their reasons in JUnit reports. Local non-strict runs
keep optional references optional.

## Verify an installed wheel

CI builds a release wheel, installs it in a fresh virtual environment, and runs
`.github/scripts/verify_wheel.py` from a directory outside the checkout. The
script needs only the standard library and starts separate isolated processes
for Python, Rust, and auto. It verifies package/module origins and core, modern,
compatibility, snapshot, frozen, exception, recovery, and GC behavior.

For a local check, install the freshly built wheel with `--no-deps`, copy the
script to an external temporary directory, change to that directory, unset
`PYTHONPATH`, set `RPLY_CHECKOUT` to the checkout's absolute path, then run the
fresh environment's Python with `-I verify_wheel.py --all`. Source pytest adds
`src` to the import path and cannot replace this installed-artifact check.

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

## CI and releases

CI runs on pushes to `main`, and on pull requests targeting `main`.
You can also start CI from the GitHub Actions page using **Run workflow**.
The workflow changes must be committed and pushed to GitHub before they take effect;
a local commit alone does not trigger Actions.
CI tests Python 3.9 through 3.14 on Linux, Windows, and macOS, selects the
Rust and Python backends explicitly, and checks out the pinned PLY/SLY
references so differential tests run on a clean checkout.
The complete macOS matrix uses Intel runners; Python 3.11–3.14 also run
on Apple Silicon. Older Python installers are unavailable on ARM runners;
see the [setup-python compatibility notes](https://github.com/actions/setup-python/issues/906).

CD builds release artifacts when a tag matching `v*` is pushed (for example,
`v0.1.0`), then publishes them to a GitHub Release. A manual CD run builds
artifacts; it publishes a release only when run against a tag. Ordinary branch
pushes do not trigger CD.
Linux wheel builds discover the interpreters in the manylinux build image;
Windows and macOS builds use an explicit Python 3.9–3.14 matrix.
macOS builds use Intel interpreters and cross-compile the Apple Silicon wheels.

## Repository hygiene

Keep `Cargo.lock` under version control for reproducible development builds.
Build output, binary extensions, caches, virtual environments, and independent
upstream clones are ignored. Source files use LF endings through
`.gitattributes`. Upstream source remains in its own repositories and retains
its own license.
