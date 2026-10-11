# rply-next

A LALR(1) parser generator for Python, with a pure-Python reference engine
and an optional Rust runtime through PyO3.

- Modern class-based lexer and parser APIs using `@token` and `@rule`.
- PLY-style module definitions via `rply.compat.ply`.
- SLY-style class definitions via `rply.compat.sly`.
- Grammar analysis, precedence resolution, and conflict diagnostics.
- Explicit Python or Rust backend selection for development and testing.

The distribution is named `rply-next`; import it as `rply`. Compatibility is
covered by regression tests and upstream comparisons for selected cases;
complete PLY/SLY compatibility and performance ratios are not claimed.

## Quick start

Build and install into an active virtual environment with Python 3.9+ and Rust:

```sh
python -m pip install maturin
python -m maturin develop --release
python examples/calculator.py
```

The example prints `20`. Its API looks like this:

```python
from rply import Lexer, Parser, token, rule

class NumberLexer(Lexer):
    ignore = " \t"

    @token(r"\d+")
    def NUMBER(self, tok):
        tok.value = int(tok.value)
        return tok

class NumberParser(Parser):
    tokens = NumberLexer.tokens

    @rule("expr : NUMBER")
    def number(self, p):
        return p[1]

lexer = NumberLexer()
parser = NumberParser()
print(parser.parse(lexer.tokenize("42")))  # 42
```

The parser builds on construction. Modern precedence entries have the form
`("left", ["PLUS", "MINUS"])`. Grammar rules accept `:` or `::=` between the left and
right sides. [The calculator](examples/calculator.py) shows operator
precedence and literal parentheses.

Actions normally receive a production proxy (`p[1]`, `p.NUMBER`). For callbacks
that take RHS values directly, use `@rule("expr : NUMBER", action_style="values")`
or `Grammar.add_production(..., action_style="values")`. The default `auto`
checks the signature once: a callback accepting one argument gets the proxy;
otherwise a compatible RHS-argument signature gets the values. Action exceptions
propagate without retrying the callback.

To use compatibility APIs, replace the corresponding imports:

```python
from rply.compat.ply import lex, yacc
from rply.compat.sly import Lexer, Parser
```

SLY's `@_` decorator is supplied inside lexer/parser class bodies. Existing
code can also import `_` from `rply.compat.sly`. See shared [PLY](tests/fixtures/ply_calculator.py)
and [SLY](tests/fixtures/sly_calculator.py) specifications for complete examples.

## Project layout

| Path | Responsibility |
| --- | --- |
| `src/rply/core/` | Shared models, public engine interfaces, diagnostics |
| `src/rply/backends/` | Backend selection, Python algorithms, Rust adapters |
| `src/rply/modern/` | Modern public API |
| `src/rply/compat/` | PLY and SLY adapters |
| `rust/src/` | Native implementation |
| `tests/` | Core, public API, and upstream comparison tests |
| `examples/` | Runnable examples |
| `benchmarks/` | Repeatable lexer, parser, and table benchmarks |
| `docs/` | Architecture, development, and background research |
| `upstream/` | Optional independent reference clones |

## Development

```sh
python -m pip install pytest
python -m pytest -q
cargo test
python -m maturin build --release
```

Set `RPLY_BACKEND=python` or `RPLY_BACKEND=rust` before importing the package
to test each implementation explicitly. The default `auto` selects Rust when
available and reports setup failures before falling back. Engine instances
expose `.backend` to show their actual selection.

- [Architecture and backend policy](docs/architecture.md)
- [Environment setup, builds, and test commands](docs/development.md)
- [Benchmark scenarios and commands](benchmarks/README.md)
- [Pinned upstream reference clones](upstream/README.md)
- [Background research](docs/research/PLY%20-%20SLY%20现代化继任项目调查报告.md)

Python source execution can use the reference engine without Rust. Building
the mixed distribution requires the Rust toolchain. Differential tests skip
when optional upstream clones are absent.

For application benchmarks (arithmetic, nested input, JSON, Unicode JSON,
AST construction, and a larger grammar), including optional upstream comparison:

```sh
python benchmarks/suite.py --engine python rust ply sly --output target/benchmarks/suite-current.json
```

Use `--engine python` without a native build, or `--check` to validate the
workloads without timing. See the [benchmark guide](benchmarks/README.md)
for input sizes, measurement boundaries, and upstream setup.

## License

[MIT](LICENSE). Upstream reference clones retain their own licenses.
