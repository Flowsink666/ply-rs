# Architecture

`ply-rs` is the distribution, `ply_rs` the Python package, `ply-rs-core` the Rust
crate, and `ply_rs._core_rs` its required private PyO3 extension.

## Source layout

- `src/ply_rs/core/`: public models, grammar registration/inspection, snapshots,
  semantic action contexts, diagnostics, and native engine facades.
- `src/ply_rs/backends/`: extension loading and Python/native adapters.
- `src/ply_rs/modern/`: class-based lexer/parser APIs and decorators.
- `src/ply_rs/compat/`: PLY module and SLY class APIs.
- `rust/src/`: scanning, LALR construction, parser execution, lazy reduction views
  and Python ownership.
- `tests/`: public contracts, independent small models and upstream comparisons.
- `benchmarks/`: shared workloads with Rust, upstream PLY and upstream SLY adapters.

There is one execution engine. `PLY_RS_BACKEND=rust` and `auto` select it;
`PLY_RS_BACKEND=python` raises ValueError. Missing extensions raise ImportError;
initialization failures propagate. No source-only fallback or lexer handoff exists.
Python grammar models, production proxies and API adapters remain intentional.
`py.typed` is included in the installed package.

## Regex matching and lexer state

Each original rule is compiled with the active interpreter's Python `re` once,
under the caller's warning/error policy. `core/regex_support.py` translates a
conservative subset of CPython's parsed syntax into an equivalent native pattern.
If a construct, flag, interpreter syntax node or native compilation limit cannot
be handled, that rule retains its compiled Python match method in `RuleMatcher`.
The scanner remains Rust regardless of individual matcher selection.

The translation preserves declaration priority, alternatives, quantifier
preferences and scoped flags. ASCII classes are expanded while keeping Unicode
character matching for dot and negated classes. Unicode categories, case folding, lookaround, backreferences and unsupported
constructs use Python matching where equivalence cannot be established. An
ASCII-input-only translation accelerates Unicode classes/boundaries on entirely
ASCII sources; a separate plan uses Python semantics again for Unicode sources.
Original character-set diagnostics occur once; reparsing for translation does
not repeat them. Python matchers receive the original source and character
position, preserving preceding context for anchors and lookbehind. Lone surrogate
inputs use the authoritative Python matchers for every rule, including rules
normally compiled natively. Their scan buffer keeps one entry per original
Python code point; surrogate values remain distinct from U+FFFD in ignored
characters and literals. Empty matches remain lexical errors.

State plans merge inclusive INITIAL/local definitions in stable priority order,
with INITIAL first on ties. Consecutive Native rules of equal priority use a
multi-pattern automaton with anchored, leftmost-first semantics. Pattern IDs map
back to their original rules. Python matchers are plan boundaries. If combined
compilation exceeds a resource limit, the plan retains individual native matchers.
Rule/state registration invalidates both input-mode plans, including inclusive
dependants. Required literal prefixes can reject Python matcher candidates without
calling Python; such prefilters are derived from the authoritative parsed syntax.

`LexerConfig` owns definitions; the native scanner owns input, position, line,
state, stack and input revision. Definitions commit only after native setup.
`LexerSnapshot` validates all fields before replacing state. Position and line
counters are nonnegative platform-sized integers. Mutable stack views continue
to reference the same runtime. Actions and error handlers can change input,
restore snapshots, register rules and scan recursively without callback replay.
Input revision distinguishes replacement from ordinary advancement.

Python match methods, error factories, imports and callbacks run after scanner
borrows end. Lazy error tokens retain the original Python input and defer suffix
slicing until read. Assignments commit before releasing the original source;
finalizers can inspect committed state and replace input. Native clear operations
release Python references after their RefCell/PyClass borrows end. Matcher method
references, actions, owners and sources participate in cyclic GC.

Modern lexers declare states, state-specific ignored characters and bound error
handlers. State operations and snapshots are forwarded by the high-level instance;
its callback tokens expose that instance as their lexer owner.

## Tables and diagnostics

Canonical LR(0) sets identify states, while ordered closures govern state
inspection and ACTION conflict decisions. Symbol discovery is deterministic.
FIRST/FOLLOW, ordered lookaheads, full LRItem state lists and conflict candidates
are exported to shared Python models. Candidate records retain the actual item
that supplied each selected ACTION; reduce/reduce candidates remain Productions.
LRItem links and production/source locations support complete diagnostic reports.
Repeated builds update existing lookahead lists in place.
LOOKBACK/INCLUDES originate at dot-zero items; advanced items with the same LHS
cannot acquire the follow set of an unrelated nonterminal transition.

The Rust artifact owns shared native ACTION/GOTO rows and snapshots of public
Python dictionaries. Parser instances share rows through Arc. Table edits before
construction or between parse calls compile replacement native rows. Production
names, lengths, bindings and action styles refresh independently. An outer parse
retains its engine if a recursive invocation refreshes the facade.

Runtime tables intern symbols as u32 IDs and use sorted compact integer rows.
Lookahead type/literal IDs are cached while the parser reduces on that token;
production GOTO IDs are compiled with metadata. Native token/symbol names share
Arc strings rather than allocating a String for each stack snapshot or reduction.
Generic Python token attributes still follow the public protocol. Missing ACTION,
explicit forbidden ACTION and valid ACTION remain separate; literal fallback is
allowed only for a missing type. Unknown symbols are ordinary missing actions.

## Actions, freezing and recovery

`ParseContext` owns per-invocation lexer/parser owners, error callbacks and action
handling. Value actions call Python functions directly. Ordinary proxy actions
receive a fresh Python ProductionProxy backed by a native reduction view.
Integer and named value access read detached Rust symbols directly; `p.slice`,
value slicing and position inspection materialize Python symbols and a real list
on demand. Subsequent edits share those same materialized objects. Native symbols
and already materialized symbols both participate in cyclic GC; saved views can
also be inspected from another thread.

The native proxy path requires the original reduction method and unmodified
proxy construction. Custom contexts, decorated hooks and customized constructors
retain the prepared-slice/RHS-only callback path. One Python action frame retains
the original symbols in exception tracebacks even if an action deletes its slice
or discards its proxy argument. Exceptions propagate once, saved slices remain
detached, and consumed token values/positions remain snapshots. RHS and LHS
default values retain their established native release points; RHS finalizers can
edit LHS positions before its runtime snapshot is taken.

Core and modern parsers support freezing. `Parser(frozen=True)` and a modern class
attribute `frozen = True` select detached read-only runtime tables/bindings. An
explicit constructor argument overrides the class default. Error handlers and
callback instance state remain live. Editable mode continues to observe source
edits; frozen mode reuses compiled metadata and native rows without full checks.

Each invocation has its own native recovery control and shared native token cursor. `errok()`
clears suppression; `restart()` resets the state/symbol stack at a safe boundary.
PLY `parser.token()` and SLY `self.tokens` advance that cursor. Recursive calls
restore the outer session even after exceptions. PLY/SLY error callbacks can
supply replacement lookahead tokens; PLY requires errok(), whereas SLY accepts
the returned token directly. SLY errok() alone keeps grammar-based error
recovery, and restart() alone does not enable PLY replacement tokens.
EOF callbacks receive None. Core callbacks
retain result-return behavior unless they explicitly request recovery.

Grammar recovery preserves real lookahead while reducing/shifting a synthetic
error symbol and keeps completed statements across successive errors. Three
real input shifts re-enable callbacks; the synthetic shift does not count.
This documented counting policy differs from upstream PLY. Defaulted-state
reductions are not added. Configure runtime tables between invocations;
mutating tables during an active parse is unsupported.

SLY parser rules follow MRO attribute resolution. Overrides retain rule position,
diamond inheritance contributes each group once, and same-name decorated rule
chains retain all alternatives. Undecorated overrides suppress inherited groups.

## Validation

Strict CI requires the native extension and pinned upstream clones and permits
no skipped contracts. Both auto/rust policy names are covered by subprocess
checks; there is one full runtime matrix. Independent lexer/parser models and
upstream comparisons remain authoritative evidence. Installed-wheel checks run
outside the checkout with isolated Python and verify module origins, py.typed,
public API behavior, reentry, recovery, snapshots, freezing, errors and GC.
