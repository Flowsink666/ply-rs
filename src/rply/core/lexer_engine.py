"""Public lexer facade with configuration and one selected runtime."""

from collections.abc import MutableSequence
from typing import Any, Callable, Iterator, Optional, Sequence, Union

from rply.backends import native_core
from rply.backends.rust_lexer import RustLexerRuntime
from rply.core.lexer_config import LexerConfig, LexerSnapshot, lexer_counter
from rply.core.tokens import LexError, LexRule, Token


class _StateStackView(MutableSequence):
    """A compatibility view that never stores a second copy of runtime state."""

    def __init__(self, lexer):
        self.lexer = lexer

    def __len__(self):
        return len(self.lexer._runtime.state_stack)

    def __getitem__(self, index):
        return self.lexer._runtime.state_stack[index]

    def __iter__(self):
        return iter(self.lexer._runtime.state_stack)

    def copy(self):
        return list(self.lexer._runtime.state_stack)

    def __setitem__(self, index, value):
        stack = list(self.lexer._runtime.state_stack)
        stack[index] = value
        self.lexer.state_stack = stack

    def __delitem__(self, index):
        stack = list(self.lexer._runtime.state_stack)
        del stack[index]
        self.lexer.state_stack = stack

    def insert(self, index, value):
        stack = list(self.lexer._runtime.state_stack)
        stack.insert(index, value)
        self.lexer.state_stack = stack

    def __eq__(self, other):
        return list(self.lexer._runtime.state_stack) == other

    def __repr__(self):
        return repr(list(self.lexer._runtime.state_stack))


class LexerEngine:
    def __init__(self):
        self._config = LexerConfig()
        self.backend = "rust"
        self._runtime = RustLexerRuntime(native_core, self)

    @property
    def states(self):
        return self._config.states

    @property
    def rules(self):
        return self._config.rules

    @property
    def ignore(self):
        return self._config.ignore

    @property
    def error_funcs(self):
        return self._config.error_funcs

    @property
    def literals(self):
        return self._config.literals

    @property
    def data(self):
        return self._runtime.data

    @property
    def length(self):
        return self._runtime.length

    @property
    def pos(self):
        return self._runtime.pos

    @pos.setter
    def pos(self, value):
        self._runtime.pos = lexer_counter(value, "position")

    @property
    def lineno(self):
        return self._runtime.lineno

    @lineno.setter
    def lineno(self, value):
        self._runtime.lineno = lexer_counter(value, "line number")

    @property
    def _input_revision(self):
        return self._runtime.input_revision

    @property
    def current_state(self):
        return self._runtime.current_state

    @current_state.setter
    def current_state(self, value):
        self.begin(value)

    @property
    def state_stack(self):
        return _StateStackView(self)

    @state_stack.setter
    def state_stack(self, value):
        states = list(value)
        if any(state not in self.states for state in states):
            raise ValueError("Undefined lexer state in stack")
        self._runtime.state_stack = states

    def capture_state(self) -> LexerSnapshot:
        return LexerSnapshot(self.data, self.pos, self.lineno, self.current_state,
                             tuple(self._runtime.state_stack))

    def restore_state(self, state: LexerSnapshot) -> None:
        self._runtime.restore_state(state.validated(self.states))

    def _configure(self, method: str, *args: Any) -> None:
        change = self._config.prepare(method, *args)
        self._runtime.configure(method, *change.args,
                                **({"compiled": change.rule.compiled} if change.rule is not None else {}))
        self._config.apply(change)

    def add_state(self, name: str, kind: str = "inclusive") -> None:
        self._configure("add_state", name, kind)

    def add_rule(self, state: str, name: str, pattern: str, func: Optional[Callable] = None,
                 priority: int = 0, flags: int = 0) -> None:
        self._configure("add_rule", state, name, pattern, func, priority, flags)

    def set_ignore(self, state: str, chars_or_regex: str) -> None:
        self._configure("set_ignore", state, chars_or_regex)

    def set_error_func(self, state: str, func: Callable) -> None:
        self._configure("set_error_func", state, func)

    def set_literals(self, lits: Union[str, Sequence[str]]) -> None:
        self._configure("set_literals", tuple(lits))

    def input(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError("Input must be a string")
        self._runtime.input(text)

    def begin(self, state: str) -> None:
        self._runtime.begin(state)

    def push_state(self, state: str) -> None:
        if state not in self.states:
            raise ValueError(f"Undefined lexer state {state!r}")
        self._runtime.push_state(state)

    def pop_state(self) -> None:
        self._runtime.pop_state()

    def skip(self, n: int = 1) -> None:
        self.pos += n

    def token(self) -> Optional[Token]:
        return self._runtime.token()

    def tokenize(self, text: Optional[str] = None) -> Iterator[Token]:
        if text is not None:
            self.input(text)
        while True:
            tok = self.token()
            if tok is None:
                return
            yield tok
