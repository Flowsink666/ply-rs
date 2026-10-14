"""
Modern, type-friendly, class-based Lexer API.
"""

import inspect
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union
from ply_rs.core.lexer_engine import LexerEngine, Token, LexError
from ply_rs.core.lexer_config import LexerSnapshot


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
            if (attr_name.startswith("_") or attr_name.startswith("ignore_")
                    or attr_name.startswith("error_")
                    or attr_name in ("ignore", "literals", "tokens", "states")):
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
        states = {}
        for base in reversed(cls.__mro__):
            declarations = base.__dict__.get("states", ())
            states.update(dict(declarations))
        cls._states = tuple(states.items())
        return cls


class Lexer(metaclass=LexerMeta):
    """
    Modern class-based Lexer base class.
    """

    ignore: str = ""
    literals: Union[str, Set[str], List[str]] = ""
    tokens: Set[str] = set()
    states: Sequence[Tuple[str, str]] = ()
    _rules: List[Any] = []

    def __init__(self):
        self._engine = LexerEngine()
        self._engine._runtime.engine.callback_owner = self
        for name, kind in self._states:
            self._engine.add_state(name, kind)
        for name, kind in (("INITIAL", "inclusive"), *self._states):
            ignored = getattr(self, "ignore_" + name, self.ignore if kind == "inclusive" else "")
            self._engine.set_ignore(name, ignored)
            error = getattr(self, "error_" + name, getattr(self, "error", None) if kind == "inclusive" else None)
            if error is not None:
                self._engine.set_error_func(name, error)
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

    def begin(self, state: str) -> None:
        self._engine.begin(state)

    def push_state(self, state: str) -> None:
        self._engine.push_state(state)

    def pop_state(self) -> None:
        self._engine.pop_state()

    def skip(self, n: int = 1) -> None:
        self._engine.skip(n)

    def capture_state(self) -> LexerSnapshot:
        return self._engine.capture_state()

    def restore_state(self, state: LexerSnapshot) -> None:
        self._engine.restore_state(state)

    @property
    def current_state(self) -> str:
        return self._engine.current_state

    @current_state.setter
    def current_state(self, state: str) -> None:
        self.begin(state)

    @property
    def state_stack(self):
        return self._engine.state_stack

    @state_stack.setter
    def state_stack(self, states):
        self._engine.state_stack = states

    @property
    def lineno(self) -> int:
        return self._engine.lineno

    @lineno.setter
    def lineno(self, value: int) -> None:
        self._engine.lineno = value

    @property
    def pos(self) -> int:
        return self._engine.pos

    @pos.setter
    def pos(self, value: int) -> None:
        self._engine.pos = value

    @property
    def data(self) -> str:
        return self._engine.data


ModernLexer = Lexer
