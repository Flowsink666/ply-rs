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
    lexer_config.py     Shared lexer definitions and immutable state snapshots
    table_data.py       Backend-neutral parse table data and build results
    parser_context.py   Per-parse callback context and semantic action handling
    production.py       Parser stack symbols and semantic action interface
    conflicts.py        Conflict records
    lexer_engine.py     Lexer backend dispatch
    lr_table.py         Table builder backend dispatch
    lr_parser.py        Parser runtime backend dispatch
    diagnostics.py      Grammar and conflict reports
  backends/
    __init__.py         Extension loading and backend policy
    interfaces.py       Structural lexer/parser runtime contracts
    python/             Reference lexer, LALR table builder, and LR runtime
    rust.py             Python grammar to native table adapter
    rust_lexer.py       Native lexer runtime adapter
    rust_parser.py      Native parser runtime adapter
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
Public core engines select and hold backend runtimes through composition;
they do not inherit reference implementations. Reference algorithms do not
import the native extension or compatibility packages. PLY callbacks receive
PLY tokens from their adapter. A callback's lexer owner is the public facade,
which forwards position and state operations to its active runtime.

`LexerConfig` stores definitions separately from scan state. Only the active
runtime owns input, position, line number, current state, and the state stack.
Public patterns retain Python regex validation; native compilation also applies
its own supported syntax and flags. Validation does not construct a Python
scanning runtime.
The Python scanner combines only compatible capture-free rules. Global inline
flags and ambiguous nested-set/set-operation syntax remain standalone so internal
compilation does not repeat or relocate registration diagnostics. This check
does not change the caller's warning filters. Normal registration continues to
validate each original pattern under the caller's current warning policy.
The scanner keeps the current state's matchers and ignored characters ready for
ordinary string inputs. State setters invalidate this cache. Shared configuration
has a separate scanning revision so changing ignored characters refreshes the
cache without rebuilding regexes. Rule/state registration still invalidates all
compiled state matchers, including inclusive states. String subclasses retain
the uncached lookup path and original reference lifetime. Matcher entries record
whether group-index dispatch is needed instead of checking their length per token.
`capture_state()` returns an immutable `LexerSnapshot`; `restore_state()`
applies it to a runtime with matching state definitions. PLY clones use this
interface instead of accessing backend state fields. The public `state_stack`
is a mutable sequence view over the active runtime, including after fallback.
Snapshots validate every field before changing input. Positions and line numbers
use nonnegative platform-sized counters on both backends; zero line numbers and
positions past EOF remain valid. A rejected restoration keeps the original
runtime state. Native restoration also validates names against native states.

Actions and error handlers may call `input()` or `restore_state()`. After an
ignored callback the scanner resumes from the current input, position, and state;
a returned token is delivered before the next scan resumes. Native scanners
release input and Unicode index borrows before user callbacks. An input revision
distinguishes replacing input from advancing within the same input, so error
recovery does not automatically skip the first character of a replacement.

A native configuration failure in auto mode transfers a snapshot to a Python
runtime. If registration fails inside a callback, the callback finishes once
and the retired scanner yields control before scanning the next token. Runtime
and action exceptions continue to propagate without replay.
Registration prepares and validates a shared definition before native setup,
and commits it after setup or a successful handoff. Strict rejection, a warning
promoted to an exception, or failed handoff preparation leaves the original
definitions and runtime selected. Native rule compilation precedes state creation.
Configure lexer definitions through registration methods; exposed definition
maps are available for inspection, not direct updates to native configuration.

Both table builders return `TableBuildResult` with the same `ParseTableData`
structure. The facade exposes its action/goto dictionaries and diagnostics;
the Rust adapter owns the native table and cache-validation snapshots. Public
code passes that artifact through without inspecting native details.
The native builder uses canonical LR(0) item sets to identify states and keeps
an ordered closure view during construction. ACTION entries and conflict
decisions follow that view, including when several reductions compete with
one shift; sorting items by production number can change the selected action.
`ParseTableData.runtime_metadata()` describes resolved production names, lengths,
action presence, and value-call flags with named immutable fields. The native
adapter consumes that description without re-inferring rule bindings.

`ParseContext` supplies the lexer, parser owner, tracking option, and error
callback for each parse invocation. Both runtimes use it for proxy actions;
native value-style actions retain their direct-call path. Modern and PLY APIs
pass their public parser as an explicit parse argument, and recursive calls keep independent
contexts instead of modifying a runtime's callback owner.
The preassembled-slice path in both runtimes is restricted to the original shared
`ParseContext.reduce` method by bound-method identity. A replacement or decorated
hook retains the four-argument protocol and RHS-only symbols, as do custom
contexts. Exceptions propagate without a retry. Each reduction keeps a separate
proxy/slice, and consumed Token fields remain snapshots. Runtimes query the
actual lookahead ACTION before reduction; they do not add PLY defaulted-state
reductions. This does not make semantic actions transactional: a later input
error can still follow actions on an earlier valid prefix.

The native adapter opts into prepared slices only when its extension advertises
support. Older extensions and custom contexts keep RHS-only callback arguments.
Native proxy reductions move the detached LHS into its Python wrapper, prepare
one fresh slice, and copy the result back into the native stack after the action.
That final copy preserves the independence of saved slices. Stack borrows end
before creating Python objects or calling user code. Value-style reductions use
direct zero-to-three-argument calls and tuple construction for larger arities;
argument identity, default values, positions, and exception behavior are retained.
An independent RHS list and a reference to the initial LHS value retain their
original release timing. Slice edits cannot release RHS values early, and failed
actions retain the RHS through their traceback. Old native/Python release-order
differences remain explicit behavioral tests instead of being normalized away.

Native lexer tokens are read through their Rust fields by the native parser;
other token classes continue to use the Python attribute protocol. Both paths
observe current token values and positions, including lexer callback edits.
Token iteration and token creation still involve Python objects.

The native table retains shared, immutable runtime ACTION/GOTO rows. Parser
instances share these rows through `Arc`; production callback metadata remains
specific to each parser. The public Python table dictionaries remain available
for inspection and editing. Snapshots detect edits before parser construction
and at the start of subsequent parse calls, including edits to existing parser
instances. Changed rows use the dictionary-based native constructor; unchanged
rows retain their current native table. Each parse also checks production names,
lengths, action presence, and action style. Metadata changes create an engine
sharing the current rows through `Arc`, including previously edited rows.
Unchanged parses only compare snapshots and metadata, without reconstructing
rows. A recursive parse may refresh the runtime; the outer invocation keeps its
own native engine reference. Configure tables and productions between parse
calls; mutation during an active invocation is not a supported update contract.

`LRParser(table, frozen=True)` and `parser.freeze()` provide an explicit static
mode; the modern `Parser` also exposes `freeze()` and `frozen`. Freezing snapshots
the current ACTION/GOTO rows, runtime production fields, name maps, and callback
bindings. Public runtime views are read-only, and edits to the original table
or productions no longer affect this parser. Callback state and error handlers
remain live. The original `parser.table` is still the editable source table.
Freezing an existing instance prepares a replacement runtime before publishing
the snapshot, so a failed preparation preserves the editable instance. Native
static parses reuse cached metadata, value callbacks, and their engine without
full-table comparisons. Dynamic mode retains the snapshot checks above.
LR items and conflict diagnostic objects remain shared inspection artifacts;
freezing isolates the data consumed during parsing rather than copying the
entire diagnostic graph. An unset name map keeps automatic proxy-name generation.

Production precedence is resolved during rule registration and passed unchanged
to the native builder, including valid zero precedence. The native adapter keeps
empty numeric tiers when declarations leave gaps, so terminal shift precedence
and production reduction precedence stay on the same scale. ACTION entries preserve
three states: missing token type, an explicit forbidden action, and a valid
shift/reduce/accept action. Literal-value fallback is permitted only for a
missing type. Normal parsing and error recovery use the same lookup contract.
Recovery preserves the real lookahead while the LR loop reduces and shifts a
synthetic `error` symbol, so completed statements survive subsequent errors.
Three successful input shifts re-enable error callbacks; the synthetic shift
does not count. Input is discarded only while the stack already contains the
error symbol awaiting synchronization. Generic token `type`/`value` property
failures propagate unless the property is absent. Only exhaustion of the input
iterator is interpreted as EOF.
Native position fields remain nonnegative machine integers; getter and integer
conversion failures propagate instead of silently substituting a position.
PLY counts its synthetic error shift towards callback suppression, so callback
frequency can differ even when both engines produce the same recovered result.

Both lexers create lazy error tokens that retain the original input and starting
position. Their `value` getter materializes and caches the remaining text on
first access; assignment overrides it without slicing. A callback can replace
the input or retire the native runtime while a saved error token still refers
to the original text. Normal native tokens retain their direct parser field path.
Assignments made during suffix slicing remain effective. An explicit value
assignment commits before releasing the retained source, so a source finalizer
observes the new value and may assign it again.
Error-token factories run after native scan borrows are released, with progress
captured beforehand. Input replacement and restoration release the previous
Python source only after the whole state transition is committed; a source
finalizer can replace input without a borrow conflict or mixed snapshot fields.
PLY and SLY wrappers also defer error suffixes while retaining their public
token classes. Reading `value` caches the suffix; copying or serialization
materializes it and stores public fields, preserving legacy token pickles.
Subtype value descriptors and extra slots remain independent of the deferred
value storage; state export reads each public descriptor once.
Native scans resolve the current `LexError` class only on an unhandled error
or an empty rule match, after retaining error metadata and releasing borrows.
Python and native tokens compare by type/value across implementations, and
unsupported comparison types return `NotImplemented`.

Native tokens, parsed symbols, and saved parser stacks expose their Python
references to cyclic garbage collection. This permits owner/token and saved
proxy/value cycles to be reclaimed. Clearing moves references out under a short
borrow and drops them after the borrow ends, so finalizers can inspect objects
or replace lexer input without conflicting with the cleanup borrow.

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
