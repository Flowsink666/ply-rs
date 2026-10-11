"""Public lexer interface with optional native acceleration."""

from typing import Any, Callable, Optional, Sequence, Union

from rply.backends import HAS_RUST_CORE, fallback, native_core
from rply.backends.python.lexer import PythonLexerEngine
from rply.core.tokens import LexError, LexRule, Token


class LexerEngine(PythonLexerEngine):
    """Keep shared rule definitions while dispatching to one runtime backend."""

    def __init__(self):
        self._rs_engine = None
        self.backend = "python"
        super().__init__()
        if HAS_RUST_CORE:
            try:
                engine = native_core.LexerEngine()
                engine.callback_owner = self
            except Exception as error:
                fallback("lexer", error)
            else:
                self._rs_engine = engine
                self.backend = "rust"

    @property
    def pos(self) -> int:
        return self._rs_engine.pos if self._rs_engine is not None else self._pos

    @pos.setter
    def pos(self, value: int) -> None:
        self._pos = value
        if self._rs_engine is not None:
            self._rs_engine.pos = value

    @property
    def lineno(self) -> int:
        return self._rs_engine.lineno if self._rs_engine is not None else self._lineno

    @lineno.setter
    def lineno(self, value: int) -> None:
        self._lineno = value
        if self._rs_engine is not None:
            self._rs_engine.lineno = value

    @property
    def current_state(self) -> str:
        return (
            self._rs_engine.current_state
            if self._rs_engine is not None
            else self._current_state
        )

    @current_state.setter
    def current_state(self, value: str) -> None:
        self._current_state = value

    def _configure_native(self, method: str, *args: Any) -> None:
        if self._rs_engine is None:
            return
        try:
            getattr(self._rs_engine, method)(*args)
        except Exception as error:
            fallback("lexer", error)
            self._pos = self.pos
            self._lineno = self.lineno
            self._current_state = self.current_state
            self._rs_engine = None
            self.backend = "python"

    def add_state(self, name: str, kind: str = "inclusive") -> None:
        super().add_state(name, kind)
        self._configure_native("add_state", name, kind)

    def add_rule(
        self, state: str, name: str, pattern: str,
        func: Optional[Callable[..., Any]] = None,
        priority: int = 0, flags: int = 0,
    ) -> None:
        super().add_rule(state, name, pattern, func, priority, flags)
        self._configure_native("add_rule", state, name, pattern, func, priority, flags)

    def set_ignore(self, state: str, chars_or_regex: str) -> None:
        super().set_ignore(state, chars_or_regex)
        self._configure_native("set_ignore", state, chars_or_regex)

    def set_error_func(self, state: str, func: Callable[..., Any]) -> None:
        super().set_error_func(state, func)
        self._configure_native("set_error_func", state, func)

    def set_literals(self, lits: Union[str, Sequence[str]]) -> None:
        super().set_literals(lits)
        self._configure_native("set_literals", lits)

    def input(self, text: str) -> None:
        super().input(text)
        if self._rs_engine is not None:
            self._rs_engine.input(text)

    def begin(self, state: str) -> None:
        super().begin(state)
        if self._rs_engine is not None:
            self._rs_engine.begin(state)

    def token(self) -> Optional[Token]:
        if self._rs_engine is not None:
            return self._rs_engine.token()
        return super().token()
