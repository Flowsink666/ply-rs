# Engine benchmarks

## Application workloads and PLY/SLY comparison

The new suite uses deterministic generated inputs and shared grammar/action
definitions. No benchmark dependencies or downloaded datasets are required.
Run from the repository root, using the same Python interpreter for building
the release extension and measuring it (activate a virtual environment first):

```sh
python -m maturin develop --release
python benchmarks/suite.py --engine python rust ply sly --output target/benchmarks/suite-current.json
```

PLY/SLY are optional. The suite prefers the pinned clones described in
[upstream/README.md](../upstream/README.md); if a clone is missing, it uses the
installed `ply` or `sly` package. It records the chosen source path, version,
and local clone revision. A requested engine that is unavailable fails with
an error instead of disappearing from the comparison. With no `--engine`
argument, only rply's Python and Rust backends run.

For source-only development, a quick validation, or a focused measurement:

```sh
python benchmarks/suite.py --engine python
python benchmarks/suite.py --engine python rust ply sly --size 3 --check
python benchmarks/suite.py --engine rust ply sly --case json json-unicode --repeat 7 --iterations 10
python benchmarks/suite.py --engine python rust --case wide-grammar --grammar-width 128 --size 2000
python benchmarks/suite.py --engine python rust --case nested --size 4096 --operation parse
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
an iterator, and rply uses its core API rather than its compatibility wrappers.
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
python benchmarks/run.py --backend both --repeat 5 --iterations 20 --output target/benchmarks/current.json
```

Use `--backend python` for source-only development. Explicit Rust mode fails if
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
