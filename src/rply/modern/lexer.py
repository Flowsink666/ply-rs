"""
Modern, type-friendly, class-based Lexer API.
"""

import inspect
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, Union
from rply.core.lexer_engine import LexerEngine, Token, LexError


def token(
    pattern: str,
    priority: int = 0,
    state: str = "INITIAL",
    flags: int = 0,
) -> Callable[..., Any]:
    """Decorator to define a token pattern on a lexer method."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func._is_token_rule = True  # type: ignore
        func._pattern = pattern  # type: ignore
        func._priority = priority  # type: ignore
        func._state = state  # type: ignore
        func._flags = flags  # type: ignore
        return func
    return decorator


class LexerMeta(type):
    """Metaclass that collects token rules defined on class body."""

    def __new__(mcs, name: str, bases: Tuple[type, ...], attrs: Dict[str, Any]) -> type:
        cls = super().__new__(mcs, name, bases, attrs)
        rules: List[Tuple[str, str, str, Optional[Callable[..., Any]], int, int]] = []
        token_names: Set[str] = set()

        # Preserve inherited rule positions, resolving overrides through the
        # MRO. New attributes follow in declaration order; diamonds deduplicate.
        rule_names = dict.fromkeys(
            inherited[1] for base in cls.__mro__[1:]
            for inherited in getattr(base, "_rules", ())
        )
        rule_names.update(attrs)
        for attr_name in rule_names:
            if attr_name.startswith("_") or attr_name in ("ignore", "literals", "tokens", "states"):
                continue
            attr_val = getattr(cls, attr_name)

            if callable(attr_val) and getattr(attr_val, "_is_token_rule", False):
                # Method decorated with @token
                tok_name = attr_name
                pat = attr_val._pattern
                pri = attr_val._priority
                st = attr_val._state
                fl = attr_val._flags
                rules.append((st, tok_name, pat, attr_val, pri, fl))
                token_names.add(tok_name)

            elif isinstance(attr_val, str) and not callable(attr_val):
                # Class attribute string regex, e.g. PLUS = r'\+'
                tok_name = attr_name
                pat = attr_val
                rules.append(("INITIAL", tok_name, pat, None, 0, 0))
                token_names.add(tok_name)

        cls._rules = rules  # type: ignore
        cls.tokens = token_names  # type: ignore
        return cls


class Lexer(metaclass=LexerMeta):
    """
    Modern class-based Lexer base class.
    """

    ignore: str = ""
    literals: Union[str, Set[str], List[str]] = ""
    tokens: Set[str] = set()
    _rules: List[Any] = []

    def __init__(self):
        self._engine = LexerEngine()
        if self.ignore:
            self._engine.set_ignore("INITIAL", self.ignore)
        if self.literals:
            self._engine.set_literals(self.literals)

        # Register rules
        for state, name, pat, func, pri, fl in self._rules:
            bound_func = None
            if func is not None:
                # Bind function to self
                bound_func = func.__get__(self, self.__class__)
            self._engine.add_rule(state, name, pat, bound_func, pri, fl)

    def input(self, text: str) -> None:
        self._engine.input(text)

    def token(self) -> Optional[Token]:
        return self._engine.token()

    def tokenize(self, text: Optional[str] = None) -> Iterator[Token]:
        return self._engine.tokenize(text)


ModernLexer = Lexer
