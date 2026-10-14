# Runtime contracts and change validation

The public behavior below is authoritative for both backends. Tests check
observable values, exceptions, callback traces, snapshots, and object lifetimes.
Backend agreement alone is insufficient when both implementations could share
the same mistaken assumption.

| Contract | Required behavior | Persistent checks |
| --- | --- | --- |
| Token protocol | Only AttributeError means a missing attribute. Only iterator exhaustion means EOF. Other getter/conversion failures propagate before actions execute. Native position fields are unsigned integers. | `core/test_semantic_contracts.py`, `core/test_generated_sequences.py` |
| Action lookup | Missing, forbidden (`None`), and valid actions stay distinct. Literal fallback applies only to a missing type. Precedence is resolved at registration; conversion preserves numeric tiers, including gaps. | `core/test_semantic_contracts.py`, `differential/test_recovery_sequences.py` |
| Lexer state | Input, position, line, state, stack, and input revision describe one committed input. Rejected restoration/configuration preserves the previous state. Input replacement increments revision; restoration increments it; backend handoff preserves it. | `core/test_state_contracts.py`, `core/test_generated_sequences.py` |
| Grouped scanning | Python combined regexes preserve each rule's matching and warning policy. Registration diagnostics are not repeated or relocated during scanning. Captures, backreferences, global inline flags and ambiguous set syntax retain standalone matching. | `core/test_python_hotpaths.py`, `.github/scripts/verify_wheel.py` |
| Scanner cache | Warm scans observe registered ignore/rule/state updates, including shared configs and inclusive inheritance. Reentrant scans cannot replace outer matchers. String subclasses retain membership calls and release timing. | `core/test_python_hotpaths.py`, `.github/scripts/verify_wheel.py` |
| User code | Actions, error handlers, token factories, attribute access, integer conversion, and finalizers can execute Python. Callbacks run once; their exceptions propagate without replay. Input changes made by callbacks remain effective. Lazy value reads retain callback assignments; lazy value setters commit before releasing the source. Error-class resolution retains the original error metadata and permits reentry. | `core/test_lazy_errors.py`, `core/test_generated_sequences.py`, `core/test_native_error_imports.py` |
| Compatibility error tokens | PLY/SLY error values retain the original unread suffix without copying until read. Assignment, deletion, saved/emitted tokens, public types, copying, serialization, and GC retain their public behavior. Subtype value descriptors and independent private metadata survive copying; public descriptors are read once. | `compat/test_lazy_errors.py`, `compat/test_lazy_review.py`, `compat/test_token_state_regressions.py` |
| Table construction | Graph closure uses explicit traversal state rather than host call-stack depth. Cached transitions preserve incoming-edge item order, public lookahead lists, and conflict diagnostics. Native ACTION construction preserves closure order when several reductions and a shift compete on one token. | `core/test_digraph_depth.py`, `core/test_transition_scaling.py`, `core/test_lookahead_scaling.py`, `core/test_conflict_order.py` |
| Error recovery | Completed statements survive successive errors. Recovery preserves the real lookahead, reduces/shifts the synthetic error, and consumes input only while synchronizing. Three real input shifts re-enable callbacks; EOF cannot be fetched repeatedly. | `core/test_recovery_sequences.py`, `differential/test_recovery_sequences.py` |
| Dynamic/frozen parsing | Dynamic parsers observe between-call table/binding edits. Freezing snapshots runtime fields and bindings; source edits are isolated, while callback state and per-call context stay live. Failed freeze preparation leaves the previous runtime editable. Active-invocation table mutation is unsupported. | `core/test_frozen_parser.py`, `core/test_state_contracts.py`, `core/test_generated_sequences.py` |
| Reduction protocol | Hooks on the shared context and custom contexts receive the original four arguments, with RHS-only symbols. Their exceptions propagate once without replay. Saved proxies/slices stay independent; consumed Token fields are snapshots. Custom LHS factories receive the original call shape, before RHS metadata is read. Invalid lookahead is checked before a reduction lacking an ACTION for it. Older native interfaces and default low-level callbacks retain their existing arguments. | `core/test_python_hotpaths.py`, `core/test_reduction_hotpaths.py`, `.github/scripts/verify_wheel.py` |
| Value actions | Direct calls preserve argument count and identity, including empty and larger productions. None retains the default first RHS value or an empty production's None. Spans survive both dynamic/frozen modes. Exceptions propagate once before parent reductions execute. | `core/test_reduction_hotpaths.py`, `.github/scripts/verify_wheel.py` |
| Ownership | Every native object owning Python references must expose those references to GC, including references inside collections. Owner/token and saved proxy/value cycles are collectible. Clearing drops old references after releasing native borrows. Native default values and RHS symbols retain their original release points when actions replace values or delete slices; exception tracebacks keep RHS references. Existing backend release-order differences remain explicit. | `core/test_native_gc.py`, `core/test_lazy_errors.py`, `core/test_reduction_hotpaths.py` |
| Installed artifact | An installed wheel imports from the isolated environment and runs public contracts with Python, Rust, and auto selection. Source-path tests do not substitute for installation checks. | `.github/scripts/verify_wheel.py`, `.github/workflows/ci.yml` |

## Sequence and reference tests

Generated lexer traces use a small character-level specification independent of
the Python and Rust scanners. Parser traces exercise binding changes, prohibited
actions, repeated calls, and freezing. Check the state and callback trace after
each operation, not just the final value. Enumerations are bounded and ordered
so failures include a short reproducible sequence and normal CI stays practical.

Recovery checks have three separate sources of evidence:

1. A simple statement specification supplies expected results and callbacks.
2. The same ACTION/GOTO table runs through rply and upstream PLY to isolate the
   execution/recovery algorithm.
3. Independent table builders run shared grammar specifications to detect
   compilation errors that a shared-table comparison cannot detect.

Known differences are explicit assertions. PLY counts the synthetic error shift
towards callback suppression; rply counts three real input shifts. PLY rejects
duplicate precedence declarations; rply accepts updates. Generic Python token
positions need not have the same representation as native unsigned fields.
Do not suppress arbitrary differences by normalizing all exceptions or callbacks.

## Native references and state transactions

Before adding a Python reference to native state, identify its owner, lifetime,
GC traversal path, and release point. Add a test that creates a cycle, drops
external references, collects it, and checks a weak reference to the owner/value.

For input replacement, restoration, freezing, and cache refresh:

1. Prepare and validate replacement state without publishing partial results.
2. Commit the complete replacement state.
3. Release old Python references after native borrows end.
4. After user code runs, re-read the state that the contract allows it to change.

Internal preparation failure must not publish a half-built runtime. User callback
side effects are not automatically rolled back when that callback raises.

## Change workflow

For each correctness change, identify the affected contract and add a failing
public-behavior reproduction before editing the runtime. Cover adjacent state
transitions and failure timing with the shared sequence/adversarial fixtures.
Keep correctness fixes, API extensions, and performance optimizations separately
reviewable. Each stage must pass its relevant contract checks before the next.

Run the behavioral suite in both backends. Strict CI also validates test
prerequisites and rejects unexpected skips; documented backend-only skips remain
allowed. Review JUnit collection/execution/skip results and the isolated wheel
check. A performance change additionally needs equivalent output, exceptions,
callback behavior, and object lifetime before reporting timing results.

The development log records defect origin, changed contracts, executed checks,
and verification limits. Test counts report coverage growth; completion requires
the contracts, independent comparisons, and installed artifact checks to pass.
