# Tests

The default suite checks product behavior with explicit examples and small
parameter tables. Tests of the test harness, benchmark tooling and exhaustive
searches run separately. A smaller pytest item count alone is not the goal:
keep meaningful public boundaries without multiplying unrelated dimensions.

## Running tests

```console
# Normal development suite (core, modern, compat and differential)
python -m pytest -q

# Require the built extension, pinned references and zero skips
python -m pytest -q --strict-prerequisites --basetemp=.pytest_cache/test-tmp

# Optional exhaustive and implementation checks
python -m pytest tests/extended -q

# Test harness and benchmark-tool checks
python -m pytest tests/infrastructure -q

# Everything, explicitly
python -m pytest tests -q --strict-prerequisites
```

`pyproject.toml` selects the four default directories. No tests are skipped or
silently deselected to implement this split. Passing `tests` explicitly includes
the optional suites. CI runs the default suite across its Python/OS matrix;
extended, infrastructure and benchmark consistency checks run once on Linux
with Python 3.14. Installed-wheel checks remain in their own CI job.

## Module ownership

```text
tests/
  core/
    lexer/           # matching, input/state, callbacks and lexical errors
    parser/          # actions, tables, freezing, token protocols and recovery
    grammar/         # grammar analysis, precedence, conflicts and table building
    runtime/         # shared native/Python reference lifetimes
    infrastructure/  # product backend selection and required-extension behavior
  modern/            # modern class/decorator definitions and wrapper forwarding
  compat/            # PLY/SLY integration and their public token protocols
  differential/      # representative comparisons with independently built upstream tables
  extended/          # explicit state/graph/recovery sweeps and internal factory regression
  infrastructure/    # pytest gates, JUnit outcomes, wheel verifier and benchmark CLI
  fixtures/          # fresh builders, adapter setup and reference construction
```

Core tests own engine behavior. Wrappers keep their definition collection,
field mapping and callback-owner smoke checks. Public input replacement,
snapshot restoration and freeze transitions are covered directly in
`core/lexer/test_lexer_state.py`, `core/lexer/test_lexer_configuration.py`,
`core/parser/test_parser_tables.py` and `core/parser/test_frozen_parser.py`.
The exhaustive state model supplements these examples in `extended/`.

Shared setup belongs in `fixtures/`; do not import another collected test module
or `conftest.py` for helpers. Builders must return fresh mutable objects.
Backend names `rust` and `auto` select the same engine: test their selection in
`core/infrastructure/test_backends.py`, then run behavior scenarios once.

## Choosing cases

- Recovery uses 17 explicit boundaries in dynamic and frozen modes, including
  EOF, repeated errors, subsequent valid statements and the documented PLY/core
  callback-suppression difference. PLY builds its own tables. The optional sweep
  shares tables to isolate runtime execution and has no aggregate magic-count
  assertions.
- Value actions retain arities 0, 1, 2 and 3 plus arity 4 for the generic tuple
  path. Default results and freezing have selected representative cases rather
  than a full arity × frozen × return-value matrix.
- Token failures cover type, value, position conversion and index fallback;
  StopIteration has a representative check instead of repeating it at every
  attribute. Exceptions must propagate before semantic actions run.
- Core owns lazy suffix caching and lifetime behavior. Each compatibility
  adapter has three smoke scenarios for fields, edits and returned error tokens.
- Serialization checks public fields, mutable-value ownership, deleted values,
  deferred error values and a representative subclass with slots and a dictionary.
  Shallow copy, deep copy and pickle remain distinct behaviors; descriptor types
  are not multiplied across all operations and adapters.
- Each API has a calculator smoke test. Broader expression/precedence comparisons
  live in the core and pinned upstream comparison suites.
- Ordinary lexer contracts run in process. Isolated interpreters are reserved
  for environment/import selection and the table test requiring a fixed hash seed.
  LexError tests assert public error type, source position and text, not the
  timing of imports or monkeypatched module attribute lookups.

Add a regression to the module owning its behavior. Prefer one readable input
and expected output for a known defect. New matrix dimensions or exhaustive
searches should have a concrete reason; do not expand them merely because another
combination can be generated. GC cycles, finalizer ordering and traceback-held
references remain distinct lifetime boundaries where relevant.
