"""Lexer definitions and snapshots, independent of scanning runtime state."""

from dataclasses import dataclass
from operator import index
import sys
from typing import Callable, Dict, List, Optional, Set

from rply.core.tokens import LexRule

MAX_LEXER_COUNTER = 2 * sys.maxsize + 1


def lexer_counter(value, name):
    """Use the native unsigned counter range on either backend."""
    value = index(value)
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
    if value > MAX_LEXER_COUNTER:
        raise OverflowError(f"{name} exceeds the platform counter range")
    return value


def _text(value, name):
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


@dataclass(frozen=True)
class LexerSnapshot:
    data: str
    pos: int
    lineno: int
    current_state: str
    state_stack: tuple

    def validated(self, states):
        data = _text(self.data, "input")
        pos = lexer_counter(self.pos, "position")
        lineno = lexer_counter(self.lineno, "line number")
        current = _text(self.current_state, "state")
        stack = tuple(_text(state, "stack state") for state in self.state_stack)
        if current not in states or any(state not in states for state in stack):
            raise ValueError("Snapshot contains an undefined lexer state")
        return type(self)(data, pos, lineno, current, stack)


@dataclass(frozen=True)
class LexerChange:
    method: str
    args: tuple
    rule: Optional[LexRule] = None


class LexerConfig:
    def __init__(self):
        self.states: Dict[str, str] = {"INITIAL": "inclusive"}
        self.rules: Dict[str, List[LexRule]] = {"INITIAL": []}
        self.ignore: Dict[str, str] = {"INITIAL": ""}
        self.error_funcs: Dict[str, Optional[Callable]] = {"INITIAL": None}
        self.literals: Set[str] = set()
        self.revision = 0
        # Scanning also depends on ignored characters, independently of REs.
        self.scan_revision = 0

    def prepare(self, method, *args):
        """Validate and compile a definition without changing shared state."""
        if method == "add_state":
            name, kind = args
            _text(name, "state")
            if kind not in ("inclusive", "exclusive"):
                raise ValueError(f"State kind must be 'inclusive' or 'exclusive', got {kind!r}")
        elif method == "add_rule":
            state, name, pattern, func, priority, flags = args
            _text(state, "state")
            _text(name, "rule name")
            _text(pattern, "pattern")
            if func is not None and not callable(func):
                raise TypeError("Rule action must be callable or None")
            args = (state, name, pattern, func, index(priority), index(flags))
            return LexerChange(method, args, LexRule(*args[1:]))
        elif method in ("set_ignore", "set_error_func"):
            state, value = args
            _text(state, "state")
            if method == "set_ignore":
                _text(value, "ignored characters")
            elif value is not None and not callable(value):
                raise TypeError("Error action must be callable or None")
        elif method == "set_literals":
            literals = tuple(args[0])
            if any(not isinstance(char, str) or len(char) != 1 for char in literals):
                raise ValueError("Literals must be single-character strings")
            args = (literals,)
        else:
            raise ValueError(f"Unknown lexer configuration operation {method!r}")
        return LexerChange(method, tuple(args))

    def _add_state(self, name, kind="inclusive"):
        self.states[name] = kind
        self.rules.setdefault(name, [])
        self.ignore.setdefault(name, "")
        self.error_funcs.setdefault(name, None)
        self.revision += 1
        self.scan_revision += 1

    def apply(self, change):
        """Commit a prepared definition after backend setup succeeds."""
        method, args = change.method, change.args
        if method == "set_literals":
            self.literals.clear()
            self.literals.update(args[0])
            return
        state = args[0]
        if method == "add_state":
            self._add_state(*args)
            return
        if state not in self.states:
            self._add_state(state)
        if method == "add_rule":
            self.rules[state].append(change.rule)
            self.rules[state].sort(key=lambda rule: rule.priority, reverse=True)
            self.revision += 1
            self.scan_revision += 1
        elif method == "set_ignore":
            self.ignore[state] = args[1]
            self.scan_revision += 1
        elif method == "set_error_func":
            self.error_funcs[state] = args[1]

    def add_state(self, name, kind="inclusive"):
        self.apply(self.prepare("add_state", name, kind))

    def add_rule(self, state, name, pattern, func=None, priority=0, flags=0):
        self.apply(self.prepare("add_rule", state, name, pattern, func, priority, flags))

    def set_ignore(self, state, chars):
        self.apply(self.prepare("set_ignore", state, chars))

    def set_error_func(self, state, func):
        self.apply(self.prepare("set_error_func", state, func))

    def set_literals(self, literals):
        self.apply(self.prepare("set_literals", literals))
