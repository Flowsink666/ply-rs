# Engine benchmarks

## Application workloads and PLY/SLY comparison

The new suite uses deterministic generated inputs and shared grammar/action
definitions. No benchmark dependencies or downloaded datasets are required.
Run from the repository root, using the same Python interpreter for building
the release extension and measuring it (activate a virtual environment first):

```sh
python -m maturin develop --release
python benchmarks/suite.py --engine rust ply sly --output target/benchmarks/suite-current.json
```

PLY/SLY are optional. The suite prefers the pinned clones described in
[docs/upstream.md](upstream.md); if a clone is missing, it uses the
installed `ply` or `sly` package. It records the chosen source path, version,
and local clone revision. A requested engine that is unavailable fails with
an error instead of disappearing from the comparison. With no `--engine`
argument, only the native Rust engine runs.

For a quick validation or a focused measurement after building the extension:

```sh
python benchmarks/suite.py --engine rust
python benchmarks/suite.py --engine rust ply sly --size 3 --check
python benchmarks/suite.py --engine rust ply sly --case json json-unicode --repeat 7 --iterations 10
python benchmarks/suite.py --engine rust --case wide-grammar --grammar-width 128 --size 2000
python benchmarks/suite.py --engine rust --case nested --size 4096 --operation parse
```

| Workload | Default input | What it exercises |
| --- | --- | --- |
| `arithmetic` | 1,000 terms, 7,999 tokens | Addition/multiplication, grouping, numeric callbacks |
| `nested` | 512 nested parentheses, 1,025 tokens | Deep LR stacks, scalar result, no recursive AST comparison |
| `json` | 250 records, 9,001 tokens | Objects, arrays, numbers, bool/null, empty containers, escaped strings |
| `json-unicode` | Same JSON structure and token count | Raw Chinese text and emoji; throughput counts UTF-8 bytes |
| `mini-language` | 250 blocks, 4,500 tokens | Assignments, variables, precedence, AST allocation, nullable statement lists |
| `wide-grammar` | 1,000 commands; 64 command forms, 451 states | Many regex rules and reachable productions; grammar width can vary independently of input length |

`--size` overrides the size of every selected workload (records, terms, blocks,
commands, or nesting depth respectively). `--grammar-width` affects only
`wide-grammar`. `--case`, `--engine`, and `--operation` each accept one or more
space-separated names. The existing arithmetic microbenchmarks remain in
`run.py` below, including the direct RHS-value callback scenario.

Each engine runs in a fresh child process with `PYTHONHASHSEED=0` and explicit
backend selection. Before timing, every case checks both replayed tokens and
streaming parsing twice against an independently constructed expected result;
JSON uses `json.loads` as its oracle. Cross-engine comparisons also require
identical input, token type/value streams, and result checksums.

The four measurements have different boundaries:

- `lex`: Scan the complete input and materialize a token list, including value conversion.
- `parse`: Parse an existing token list with semantic actions, excluding scanning.
- `end-to-end`: Stream lexer tokens directly into the parser.
- `build`: Create a fresh lexer, register grammar/actions, generate tables,
  and construct a parser. Input generation and process startup are excluded.
  This is a warm-process construction benchmark, not a cold-start measurement.

The defaults are two warm-up operations, five repeats, and five operations per
repeat. Python garbage collection remains enabled, so allocation-heavy cases
include collection costs. The terminal summary shows median ms/operation;
stdout and `--output` contain JSON with all per-repeat samples, min/max,
standard deviation, tokens/s, and MiB/s. Construction has no input throughput.
Reports also record workload sizes, rule/state counts, environment details,
repository revision/dirty status, and the Rust extension's SHA-256.

The adapters use the same token patterns, value converters, productions, and
semantic actions. Tokens without conversion do not receive artificial callbacks.
Each implementation retains its own regex rule ordering/dispatch strategy;
these workloads avoid ambiguous overlapping token patterns. All parsers enable
position tracking, although each engine's tracking behavior and cost differ.
The shared actions are invoked through small adapters using the public
production APIs. PLY uses a token cursor to replay or stream tokens, SLY uses
an iterator, and ply-rs uses its core API rather than its compatibility wrappers.
Thus these results measure the public integration and Python callback boundary,
not isolated native Rust operations or compatibility-wrapper overhead.

For before/after comparisons, keep the interpreter, release build, workload
size, selected operations, and sampling settings fixed. Check raw samples for
noise; increase iterations for short operations. Engine runs are sequential,
so CPU temperature and background load can affect ratios. Avoid interpreting a
single run as a universal speedup. Error recovery, peak memory, and cold process
startup are not measured by this suite.

## Arithmetic microbenchmarks

Run from the repository root with the native extension built and available:

```sh
python benchmarks/run.py --backend rust --repeat 5 --iterations 20 --output target/benchmarks/current.json
```

The native extension is required. Explicit Rust mode fails if
the extension is unavailable or a native setup error occurs. Each backend runs
in a separate process, with the same grammar, text, and iteration counts.

| Scenario | Input and actions |
| --- | --- |
| `ascii-small` | 10 numbers, 19 tokens, numeric actions |
| `ascii-long` | 1,000 numbers, 1,999 tokens, numeric actions |
| `unicode-long` | Arabic digits, 1,999 tokens, numeric actions |
| `many-rules` | 24 unmatched rules before NUMBER/PLUS, 1,999 tokens |
| `no-actions` | 1,999 tokens without lexer or parser actions |
| `named-actions` | 1,999 tokens, actions read `p.expr` and `p.NUMBER` |
| `value-actions` | 1,999 tokens, actions receive RHS values directly |

Results are median milliseconds per operation after a warm-up. `lex_ms`
includes materializing the token list. `parse_ms` uses an existing token list.
`end_to_end_ms` streams lexer tokens directly to the parser. `table_ms` builds
a table from an existing grammar; it excludes grammar registration and process
startup. `parser_ms` creates a parser from an existing table. Every scenario checks its parse result before timing. JSON reports
include the Python version, operating system, and sampling settings.

Compare before/after reports on the same machine with the same interpreter,
release build, and settings. Background load and CPU frequency can affect short
timings. These small arithmetic grammars locate runtime overhead; they do not
represent large grammars, error-heavy input, memory consumption, or cold starts.
Measure those separately before changing table caching or native object storage.

The Python hot-path changes remove repeated facade property calls during
scanning, reuse each state's active rules, avoid unused string conversions,
and reuse production name maps. Native parsing reads native token fields
directly and calls value-style actions with RHS values. Proxy-style actions
retain symbol objects and the compatibility proxy. Token iteration and semantic
actions still cross the Python/native boundary.

## First hot-path pass, 2026-10-11

Windows 11, CPython 3.14.7, native release build, five repeats of 20 operations.
The baseline precedes the runtime optimizations; the final report includes
the Python hot-path changes and native ownership transfers. Times are ms per
operation, rounded to three decimals.

| Backend and operation | Before | After | Time reduction |
| --- | ---: | ---: | ---: |
| Python, `ascii-long` scanning | 4.037 | 2.869 | 29% |
| Python, `ascii-long` parsing | 3.864 | 2.486 | 36% |
| Python, `ascii-long` end-to-end | 8.687 | 5.488 | 37% |
| Python, `named-actions` parsing | 6.805 | 2.539 | 63% |
| Rust, `ascii-long` parsing | 2.882 | 2.601 | 10% |
| Rust, `named-actions` parsing | 7.591 | 2.703 | 64% |

Raw reports from this run are in `target/benchmarks/before.json` and
`target/benchmarks/after.json` (ignored build output). A report immediately
before the native ownership changes is in `before-native-moves.json`;
ASCII parsing went from 2.818 to 2.601 ms in that narrower comparison.
These sequential runs provide an indication of improvement; they do not
isolate every change or control background load.

At that point, the matrix showed:

- Python scanning with 24 unmatched rules takes 10.435 ms versus 2.869 ms with
  two rules. Sequential regex matching remains a significant cost.
- Native parsing takes 1.263 ms without actions and 2.601 ms with numeric
  actions. These scenarios also have different token values; combined with
  the runtime's per-reduction callback and object construction, the comparison
  motivates profiling that boundary before more native changes.
- This small grammar's table construction takes roughly 0.02–0.07 ms.
  Persistent table caching needs a larger-grammar or cold-start benchmark
  before it becomes a priority.

## Native boundary pass, 2026-10-11

Same machine and sampling settings as above; this baseline is immediately
before native token field access, direct value actions, and shared runtime
tables. No-action and value-action reductions also avoid Python proxies.

| Backend, scenario, operation | Before (ms) | After (ms) | Time reduction |
| --- | ---: | ---: | ---: |
| Rust, `ascii-long`, parsing | 2.523 | 1.466 | 42% |
| Rust, `no-actions`, parsing | 1.358 | 0.292 | 78% |
| Rust, `value-actions`, parsing | 3.233 | 0.391 | 88% |
| Rust, `ascii-long`, end-to-end | 5.172 | 4.070 | 21% |
| Python, `no-actions`, parsing | 2.402 | 1.627 | 32% |

Native parser initialization from an existing table changed from 3.33 to
2.26 microseconds in the ASCII scenario. Table construction now prepares
native runtime rows and keeps Python dictionary snapshots; this adds setup
and storage costs once per table. Subsequent parser instances share native
rows unless public ACTION/GOTO dictionaries were edited.

Reports: `target/benchmarks/boundary-before.json` and
`target/benchmarks/boundary-after.json`. The direct token path applies to
native lexer tokens; external Python token classes still use attribute
adaptation. Python token creation, iteration, and proxy-style callbacks remain.
Sequential timings are subject to background load and do not isolate each
optimization.

> Historical measurements below describe the removed Python reference engine.
> They are retained as research records; current benchmark commands select
> Rust, upstream PLY, or upstream SLY.

## Python scanner and parser pass, 2026-10-14

Windows 11, Intel Core i7-11800H, CPython 3.14.7. The comparison loads saved
pre-edit Python engines and current engines in separate processes, in the order
before/after/after/before. Each group uses three warm-ups and five repeats of ten
operations with GC enabled. The table pools the ten per-repeat samples for each
version and shows median end-to-end milliseconds per operation.

| Workload | Before | After | Before/after ratio |
| --- | ---: | ---: | ---: |
| `arithmetic` | 41.833 | 38.729 | 1.08x |
| `nested` | 5.776 | 5.143 | 1.12x |
| `json` | 48.522 | 39.716 | 1.22x |
| `json-unicode` | 47.857 | 40.514 | 1.18x |
| `mini-language` | 25.426 | 22.768 | 1.12x |
| `wide-grammar` | 83.201 | 33.403 | 2.49x |

Equal-weight geometric mean ratios across the six workloads are 1.63x for
scanning, 1.07x for parsing, and 1.30x end-to-end. The wide-grammar scanner goes
from 78.513 to 23.471 ms (3.35x). These are warm-process observations; smaller
differences remain subject to CPU frequency, temperature, and background load.

The Python scanner now combines consecutive capture-free rules with identical
effective regex flags, retaining stable priority order. Captures/backreferences,
conditional groups, global inline flags, and DEBUG patterns remain standalone.
Combined expressions are prepared lazily per state and invalidated by rule
registration. First scanning pays that preparation cost; the suite's `build`
operation constructs the lexer but does not yet scan input.

The parser binds tables per invocation, uses direct indexing for ordinary
ACTION/GOTO dictionaries, and keeps missing/forbidden actions and custom mapping
semantics intact. Proxy reductions use one fresh LHS/RHS list. Saved proxies and
consumed-token snapshots retain their original lifetime/value behavior.

Short cProfile checks show 432,020 to 7,000 regex `match` calls for the
7,000-token wide workload, and 60,000 to 19,000 dictionary `get` calls for
arithmetic parsing. Indexing still performs table lookups; profiler timings are
not used for speed ratios. A separate four-engine run confirms that Python
remains slower than PLY on these workloads after this pass.

Final validation: Python strict 499 passed with ten declared native-only skips;
Rust strict 509 passed; isolated release-wheel checks passed for Python, Rust,
and auto. Python 3.9 additionally passes the inline-flags warning contract and
the six-workload, four-engine correctness check.

Local reports and raw samples are in
`docs/benchmarks/ply-optimization-2026-10-14.md`,
`target/benchmarks/ply-optimization-paired-2026-10-14.json`, and
`target/benchmarks/ply-optimization-matrix-2026-10-14.json`. Saved pre-edit sources and the
comparison script are in the same ignored benchmark directory.

Follow-up semantic checks keep ambiguous nested-set/set-operation expressions
standalone, preserving the warning policy applied during original registration.
The prepared-slice parser path now checks the original bound method's identity;
instrumented reduction hooks retain four arguments and RHS-only symbols.
Normal-path cProfile call counts remain 7,000 regex matches for the wide workload
and 19,000 dictionary `get` calls for arithmetic parsing. This follow-up checked
call counts, not a new before/after timing ratio. Details and regression evidence
are in `docs/reviews/semantics-audit-2026-10-14.md`.

The next scanning pass caches the current state's matchers and ignored characters
for ordinary strings. State setters invalidate the cache; a separate shared scan
revision observes ignore edits without recompiling expressions. String subclasses
retain uncached lookup and release behavior. Matcher entries record group dispatch
at preparation time, avoiding a length check per match. Token snapshots, saved
proxies, custom constructors, and lookahead validation retain their prior behavior.

This smaller change is measured with same-process ABBA pairs for each workload,
with token values, positions, lines, and parse results checked before timing.
Each trial has eight samples per version and operation, ten iterations per sample,
three warmups, and GC enabled. Pure parsing is unchanged and serves as a noise
control. The initial cache candidate had negligible benefit and was simplified;
the separately tested guarded symbol-construction candidate was slower and was
not adopted. Raw trials, source snapshots, and scripts are under
`target/benchmarks/scan-cache-*`; details are in
`docs/benchmarks/scan-cache-2026-10-14.md`.

Pooling the two final trials gives equal-weight geometric mean ratios of 1.026x
for scanning and 1.023x end-to-end. The unchanged parser measures 0.991x, showing
that small timing movements still include noise. These are modest observations,
not a guarantee for every workload. The wide scanner retains 7,000 regex matches
while dictionary `get` calls fall from 14,000 to two and matcher-dispatch `len`
calls fall from 7,000 to zero. Final strict checks: Python 517 passed plus ten
declared skips; Rust 527 passed. Isolated release-wheel checks pass 19 contract
groups under each of Python, Rust, and auto.

The Rust follow-up prepares a fresh proxy slice in native code for the original
shared reducer, removes a redundant LHS clone before that callback, and uses
direct calls for value actions with zero to three arguments. Custom reduction
hooks and older extensions retain the original protocol; snapshots and saved
proxy objects remain independent. Public proxy/values and dynamic/frozen mode
choices are unchanged. The native RHS list and original default value remain
alive through the same callback/traceback release points, even after slice edits.

Native before/after libraries and their respective adapters are loaded separately
in the same interpreter for ABBA measurements. Each runtime's tokens come from
its own native lexer, and their types are checked to avoid accidentally measuring
generic attribute conversion on only one side. Table construction is common and
outside timing. Six default proxy workloads plus arithmetic/JSON value actions
in dynamic/frozen mode have independent output and token-position checks. Reports,
raw samples, saved source/binaries, and the comparison script are under
`target/benchmarks/rust-reduction-*`; the summary is
`docs/benchmarks/rust-reduction-2026-10-14.md`.

Two final trials pool 16 samples per version/operation, reconstructing runtimes
per ABBA block to average native HashMap layout differences. Equal-weight ratios
for the six default proxy workloads are 1.033x for parsing and 1.036x end-to-end.
Arithmetic/JSON value actions measure 1.078x/1.048x in dynamic mode and
1.049x/1.061x when frozen. Modes are compared separately and are not switched by
the benchmark or implementation. CPU frequency and external load remain variable.
The initial experimental single-list candidate changed finalizer/slice release
timing and is excluded from final measurements. Final checks: Python 565 passed
plus ten declared skips, Rust 575 passed, Cargo 12 passed, strict Clippy/rustfmt
passed, and isolated Python/Rust/auto wheel verification passes 22 groups each.


## Native-only engine migration, 2026-10-14

Windows, CPython 3.14.7, release builds. The saved pre-migration and final
reports use identical workloads, one warm-up, three repeats and three operations
per repeat. Input, token and result checksums agree for every workload. These
sequential observations include all compatibility/API changes and do not isolate
individual optimizations; small differences are subject to background load.

| Workload | Scan before/after (ms) | Parse before/after (ms) | End-to-end before/after (ms) | Build before/after (ms) |
| --- | ---: | ---: | ---: | ---: |
| arithmetic | 3.916 / 2.788 | 23.190 / 21.042 | 28.697 / 24.183 | 0.297 / 0.502 |
| nested | 0.346 / 0.462 | 3.413 / 3.393 | 3.924 / 3.394 | 0.334 / 0.569 |
| json | 8.622 / 6.482 | 19.704 / 18.058 | 28.661 / 25.806 | 1.159 / 1.741 |
| json-unicode | 8.610 / 6.917 | 18.259 / 16.974 | 26.043 / 25.258 | 1.156 / 2.292 |
| mini-language | 2.433 / 1.484 | 12.493 / 11.239 | 14.820 / 13.112 | 0.500 / 1.027 |
| wide-grammar | 12.729 / 2.577 | 6.523 / 5.605 | 19.930 / 8.228 | 8.473 / 13.397 |

Native multi-pattern plans accelerate wide rule sets. ASCII-only input plans
avoid unnecessary Python matching while Unicode sources retain Python regex
semantics. Parsed literal prefixes filter unmatched Python candidates. Shared
native token cursors avoid adding a Python forwarding frame per token; compact
u32 table rows and Arc symbol names reduce parsing lookup/allocation work.

Full table diagnostics and regex translation add construction work. Reuse built
lexer/parser instances and freeze static parsers; these measurements do not claim
that every workload or startup phase improved. Local raw reports are
`target/backend-removal-baseline.json` and `target/backend-removal-after.json`.

## Lazy native reduction views, 2026-10-14

Windows 11, CPython 3.14.7, native release builds. This comparison starts with
the native-only engine immediately before lazy reduction views. Saved baseline
and final packages run in separate isolated processes, using two interleaved
before/after/after/before cycles. Each operation has two warm-ups and five
samples of five invocations per process, giving 20 samples per version and
operation. GC remains enabled. Input, token values/positions/lines, parse results
and state counts agree across every run.

These are the six default proxy-action workloads, with editable parsers and
position tracking. Public indexing methods retain their type annotations.
Times are median milliseconds per operation; ratios express throughput gains.

| Workload | Parse before | Parse after | Parse ratio | End-to-end before | End-to-end after | End-to-end ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| arithmetic | 21.031 | 16.572 | 1.27x | 24.288 | 20.658 | 1.18x |
| nested | 2.845 | 2.256 | 1.26x | 3.160 | 2.603 | 1.21x |
| json | 16.206 | 13.491 | 1.20x | 24.148 | 20.678 | 1.17x |
| json-unicode | 16.959 | 13.308 | 1.27x | 26.396 | 22.686 | 1.16x |
| mini-language | 12.035 | 9.536 | 1.26x | 13.935 | 12.328 | 1.13x |
| wide-grammar | 5.928 | 4.887 | 1.21x | 8.879 | 8.153 | 1.09x |

Equal-weight geometric mean ratios are 1.246x for parsing and 1.156x end-to-end.
The final extension matches the installed wheel's SHA-256. Local raw samples,
checksums and settings are in `target/native_proxy_abba.json`; its comparison
script is `target/native_proxy_abba.py`. The pre-edit source and binary snapshot
is under `target/native-proxy-baseline/`. These ignored artifacts preserve the
local comparison; CPU frequency and background load still affect timings.
The run measures warm parsing, not peak memory, error-heavy inputs or cold starts.

Ordinary proxy actions now read and write values through detached native records.
Python symbol objects and a real RHS list are created only when an action inspects
`p.slice`, value slices or positions. The action path skips the Python reducer
and proxy initializer while retaining one Python frame for traceback ownership.
Every reduction retains a distinct public proxy; materialized slices stay mutable.
Constructor customization and reduction hooks use the materialized callback
protocol. Value-style actions keep their direct invocation path.

Validation: 654 strict Python tests pass without skips, 13 Cargo tests pass,
Clippy and rustfmt pass, and all 18 Rust/PLY/SLY workload checks agree. A fresh
environment outside the checkout verifies 26 installed-wheel contract groups
under each of `rust` and `auto`, including saved views and reference lifetimes.
