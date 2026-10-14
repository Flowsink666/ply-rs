"""Reproducible engine benchmarks; no third-party benchmark dependencies."""

import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import timeit

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "ascii-small": (10, 0, True, False, False),
    "ascii-long": (1000, 0, True, False, False),
    "unicode-long": (1000, 0, True, True, False),
    "many-rules": (1000, 24, True, False, False),
    "no-actions": (1000, 0, False, False, False),
    "named-actions": (1000, 0, True, False, True),
    "value-actions": (1000, 0, True, False, "values"),
}


def measure(operation, repeat, iterations):
    operation()  # Warm caches before timing.
    samples = timeit.repeat(operation, repeat=repeat, number=iterations)
    return statistics.median(samples) * 1000 / iterations


def benchmark(mode, repeat, iterations):
    # Import after selecting the process-wide backend.
    os.environ["RPLY_BACKEND"] = mode
    sys.path.insert(0, str(ROOT / "src"))
    from rply import Grammar, LexerEngine, LRParser, LRTable

    results = {}
    for name, (size, extra_rules, actions, unicode_text, named) in CASES.items():
        lexer = LexerEngine()
        for index in range(extra_rules):
            lexer.add_rule("INITIAL", f"KEYWORD{index}", f"keyword{index}")

        def number(tok):
            tok.value = int(tok.value)
            return tok

        lexer.add_rule("INITIAL", "NUMBER", r"\d+", number if actions else None)
        lexer.add_rule("INITIAL", "PLUS", r"\+")
        lexer.set_ignore("INITIAL", " ")
        values = [str(index % 100) for index in range(size)]
        if unicode_text:
            values = [value.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")) for value in values]
        text = " + ".join(values)
        tokens = list(lexer.tokenize(text))
        grammar = Grammar({"NUMBER", "PLUS"})
        if named == "values":
            add = lambda left, plus, right: left + right
            single = lambda value: value
        elif named:
            add = lambda p: p.expr + p.NUMBER
            single = lambda p: p.NUMBER
        else:
            add = (lambda p: p[1] + p[3]) if actions else None
            single = (lambda p: p[1]) if actions else None
        style = "values" if named == "values" else "proxy"
        grammar.add_production("expr", ["expr", "PLUS", "NUMBER"], func=add, action_style=style)
        grammar.add_production("expr", ["NUMBER"], func=single, action_style=style)
        grammar.set_start("expr")
        table = LRTable(grammar)
        parser = LRParser(table)
        expected = sum(index % 100 for index in range(size)) if actions else values[0]
        assert parser.parse(tokens) == expected
        operations = {
            "lex_ms": lambda: list(lexer.tokenize(text)),
            "parse_ms": lambda: parser.parse(tokens),
            "end_to_end_ms": lambda: parser.parse(lexer.tokenize(text)),
            "table_ms": lambda: LRTable(grammar),
            "parser_ms": lambda: LRParser(table),
        }
        results[name] = {
            "token_count": len(tokens),
            **{label: measure(op, repeat, iterations) for label, op in operations.items()},
        }
    return results


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--backend", choices=["rust", "auto"], default="rust")
    args.add_argument("--repeat", type=int, default=5)
    args.add_argument("--iterations", type=int, default=20)
    args.add_argument("--output", type=Path)
    options = args.parse_args()
    if options.repeat < 1 or options.iterations < 1:
        args.error("repeat and iterations must be positive")
    results = {"rust": benchmark(options.backend, options.repeat, options.iterations)}
    report = {
        "python": sys.version, "platform": platform.platform(),
        "repeat": options.repeat, "iterations": options.iterations, "results": results,
    }
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
