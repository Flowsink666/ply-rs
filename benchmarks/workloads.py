"""Deterministic, shared grammars and inputs; independent expected results."""

from dataclasses import dataclass
import json
import re
from typing import Callable, Optional, Tuple


@dataclass(frozen=True)
class TokenRule:
    name: str
    pattern: str
    convert: Optional[Callable] = None


@dataclass(frozen=True)
class Rule:
    name: str
    rhs: str
    action: Callable


@dataclass(frozen=True)
class Workload:
    name: str
    size: int
    text: str
    expected: object
    tokens: Tuple[TokenRule, ...]
    rules: Tuple[Rule, ...]
    start: str


def first(*values):
    return values[0]


def append(items, *values):
    # Mutate a list allocated by this parse, avoiding quadratic list copying.
    items.append(values[-1])
    return items


ARITHMETIC_TOKENS = (
    TokenRule("NUMBER", r"[0-9]+", int),
    TokenRule("PLUS", r"\+"), TokenRule("TIMES", r"\*"),
    TokenRule("LPAREN", r"\("), TokenRule("RPAREN", r"\)"),
)
ARITHMETIC_RULES = (
    Rule("expr", "expr PLUS term", lambda a, op, b: a + b),
    Rule("expr", "term", first),
    Rule("term", "term TIMES atom", lambda a, op, b: a * b),
    Rule("term", "atom", first),
    Rule("atom", "LPAREN expr RPAREN", lambda left, value, right: value),
    Rule("atom", "NUMBER", first),
)


def arithmetic(size):
    terms = [f"({i % 17} + 2) * 3" for i in range(size)]
    return Workload("arithmetic", size, " + ".join(terms),
                    sum((i % 17 + 2) * 3 for i in range(size)),
                    ARITHMETIC_TOKENS, ARITHMETIC_RULES, "expr")


def nested(size):
    # Deep LR stacks without recursively constructing/comparing an AST.
    return Workload("nested", size, "(" * size + "7" + ")" * size, 7,
                    ARITHMETIC_TOKENS, ARITHMETIC_RULES, "expr")


JSON_TOKENS = (
    TokenRule("STRING", r'"(?:[^"\\\x00-\x1f]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*"', json.loads),
    TokenRule("NUMBER", r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", json.loads),
    TokenRule("TRUE", r"true\b", lambda value: True),
    TokenRule("FALSE", r"false\b", lambda value: False),
    TokenRule("NULL", r"null\b", lambda value: None),
    TokenRule("LBRACE", r"\{"), TokenRule("RBRACE", r"\}"),
    TokenRule("LBRACKET", r"\["), TokenRule("RBRACKET", r"\]"),
    TokenRule("COMMA", ","), TokenRule("COLON", ":"),
)
JSON_RULES = (
    *(Rule("value", symbol, first) for symbol in
      ("STRING", "NUMBER", "TRUE", "FALSE", "NULL", "object", "array")),
    Rule("object", "LBRACE RBRACE", lambda left, right: {}),
    Rule("object", "LBRACE members RBRACE", lambda left, pairs, right: dict(pairs)),
    Rule("members", "pair", lambda pair: [pair]),
    Rule("members", "members COMMA pair", append),
    Rule("pair", "STRING COLON value", lambda key, colon, value: (key, value)),
    Rule("array", "LBRACKET RBRACKET", lambda left, right: []),
    Rule("array", "LBRACKET elements RBRACKET", lambda left, values, right: values),
    Rule("elements", "value", lambda value: [value]),
    Rule("elements", "elements COMMA value", append),
)


def json_records(size, unicode=False):
    records = [
        {"id": i, "name": f"用户{i}🚀" if unicode else f"user-{i}",
         "active": i % 2 == 0, "score": -i / 4,
         "tags": ["a", 'quote"slash\\\n', None, {}], "extra": []}
        for i in range(size)
    ]
    text = json.dumps(records, ensure_ascii=not unicode, separators=(",", ":"))
    # Validate against the standard library, independently of the LR grammar.
    expected = json.loads(text)
    return Workload("json-unicode" if unicode else "json", size, text, expected,
                    JSON_TOKENS, JSON_RULES, "value")


def mini_language(size):
    tokens = ARITHMETIC_TOKENS + (
        TokenRule("ID", r"[a-zA-Z_][a-zA-Z_0-9]*"),
        TokenRule("EQUAL", "="), TokenRule("SEMI", ";"),
        TokenRule("LBRACE", r"\{"), TokenRule("RBRACE", r"\}"),
    )
    rules = (
        Rule("program", "statements", first),
        Rule("statements", "", lambda: []),
        Rule("statements", "statements statement", append),
        Rule("statement", "ID EQUAL expr SEMI", lambda name, eq, expr, semi: ("assign", name, expr)),
        Rule("statement", "LBRACE statements RBRACE", lambda left, body, right: ("block", body)),
        Rule("expr", "expr PLUS term", lambda a, op, b: ("+", a, b)),
        Rule("expr", "term", first),
        Rule("term", "term TIMES atom", lambda a, op, b: ("*", a, b)),
        Rule("term", "atom", first),
        Rule("atom", "LPAREN expr RPAREN", lambda left, value, right: value),
        Rule("atom", "NUMBER", first),
        Rule("atom", "ID", lambda name: ("name", name)),
    )
    text = "\n".join(f"{{ v{i} = (1 + 2) * 3; out{i} = v{i} + 4; }}" for i in range(size))
    expected = [("block", [("assign", f"v{i}", ("*", ("+", 1, 2), 3)),
                           ("assign", f"out{i}", ("+", ("name", f"v{i}"), 4))])
                for i in range(size)]
    return Workload("mini-language", size, text, expected, tokens, rules, "program")


def wide_grammar(size, width):
    """Many reachable productions/rules; width is independent of input size."""
    tokens = tuple(TokenRule(f"CMD{i}", re.escape(f"cmd{i}") + r"\b") for i in range(width))
    tokens += (TokenRule("NUMBER", r"[0-9]+", int), TokenRule("LPAREN", r"\("),
               TokenRule("RPAREN", r"\)"), TokenRule("COMMA", ","), TokenRule("SEMI", ";"))
    rules = (
        Rule("program", "", lambda: 0),
        Rule("program", "program command", lambda total, value: total + value),
        *(Rule("command", f"CMD{i} LPAREN NUMBER COMMA NUMBER RPAREN SEMI",
               lambda cmd, left, a, comma, b, right, semi: a + b) for i in range(width)),
    )
    text = "\n".join(f"cmd{i % width}({i % 101}, {i % 13});" for i in range(size))
    return Workload("wide-grammar", size, text,
                    sum(i % 101 + i % 13 for i in range(size)), tokens, rules, "program")


DEFAULT_SIZES = {"arithmetic": 1000, "nested": 512, "json": 250,
                 "json-unicode": 250, "mini-language": 250, "wide-grammar": 1000}


def make_workload(name, size=None, width=64):
    size = DEFAULT_SIZES[name] if size is None else size
    if name == "json-unicode":
        return json_records(size, unicode=True)
    if name == "json":
        return json_records(size)
    if name == "wide-grammar":
        return wide_grammar(size, width)
    return {"arithmetic": arithmetic, "nested": nested,
            "mini-language": mini_language}[name](size)
