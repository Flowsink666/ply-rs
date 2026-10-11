# Architecture

`rply-next` is the distribution name; `rply` is the Python import package;
`rply-core` is the Rust crate. `rply._core_rs` is the private native extension.

## Source layout

```text
src/rply/
  core/                 Shared models and public engine facades
    grammar.py          Grammar validation and FIRST/FOLLOW analysis
    symbols.py          Symbol constants and types
    tokens.py           Token, lexical rule, and lexical error types
    production.py       Parser stack symbols and semantic action interface
    conflicts.py        Conflict records
    lexer_engine.py     Lexer backend dispatch
    lr_table.py         Table builder backend dispatch
    lr_parser.py        Parser runtime backend dispatch
    diagnostics.py      Grammar and conflict reports
  backends/
    __init__.py         Extension loading and backend policy
    python/             Reference lexer, LALR table builder, and LR runtime
    rust.py             Python grammar to native table adapter
  modern/               Class-based @token and @rule APIs
  compat/
    ply/                PLY module API and callback adapters
    sly/                SLY class API, decorators, and callback adapters
rust/src/               Native grammar, lexer, table builder, and runtime
tests/
  core/                 Models, engines, backend policy, and regressions
  modern/               Modern public API
  compat/               PLY and SLY API adapters
  differential/         Optional comparisons against upstream clones
  fixtures/             Shared grammar specifications
examples/               Runnable public API examples
docs/research/          Background research, separate from implementation docs
upstream/               Independent reference clones and reproduction notes
```

The modern and compatibility APIs build shared grammar and engine objects.
Public core engines select the backend; reference algorithms do not import
the native extension or compatibility packages. PLY callbacks receive PLY
tokens from their adapter. The native lexer only knows a generic callback
owner, which keeps runtime positions and state changes synchronized.

Native lexer tokens are read through their Rust fields by the native parser;
other token classes continue to use the Python attribute protocol. Both paths
observe current token values and positions, including lexer callback edits.
Token iteration and token creation still involve Python objects.

The native table retains shared, immutable runtime ACTION/GOTO rows. Parser
instances share these rows through `Arc`; production callback metadata remains
specific to each parser. The public Python table dictionaries remain available
for inspection and editing. Snapshots detect edits before parser construction;
an edited table uses the dictionary-based native constructor instead. This
adds runtime rows and snapshot storage when building a native table, in return
for avoiding repeated dictionary conversion for each parser instance.

Productions without actions propagate values internally. Value-style actions
receive RHS values directly; native parsing calls them without constructing
stack-symbol objects or a production proxy. Proxy-style actions keep the full
compatibility interface. A None return retains the current LHS value: initially
the first RHS value, or None for an empty production. Proxy actions can replace
that value through `p[0]`. Callables are resolved per parse so rebinding remains
effective, and exceptions propagate after a single invocation.

The existing `rply.core.lexer_engine`, `rply.core.lr_parser`, and
`rply.core.lr_table` import paths remain supported. Shared types are
re-exported there. `ProductionProxy` retains its existing access helpers for
callers; the SLY adapter provides SLY-specific RHS indexing and position
attributes. Backend modules are implementation details.

## Backend policy

Set `RPLY_BACKEND` **before importing `rply`**:

| Value | Behavior |
| --- | --- |
| `auto` (default) | Use the extension when importable; otherwise use Python. Native setup failures emit a warning and select Python for that component. |
| `python` | Use Python without loading the extension. |
| `rust` | Require the extension and reject native setup failures. Useful for verifying native coverage. |

`LexerEngine.backend`, `LRTable.backend`, and `LRParser.backend` report the
implementation actually selected by each component. In automatic mode a
Python-only regex can make the lexer fall back while the parser stays native.
Runtime and user callback exceptions propagate; they do not trigger a replay
with another backend.

Python and Rust have different regex engines and may support different
patterns or flags. The compatibility tests cover specific cases, rather than
guaranteeing every upstream feature. Rust table diagnostics currently expose
state counts and conflict metadata without Python LR item candidates.
