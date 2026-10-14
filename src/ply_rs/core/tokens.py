"""Shared token, lexical rule, and error types."""

import re
import sys
from typing import Any, Callable, Optional, Pattern


class LexError(Exception):
    """Exception raised on lexical errors."""

    def __init__(self, message: str, text: str = "", error_index: int = 0):
        super().__init__(message)
        self.text = text
        self.error_index = error_index


class Token:
    """Representation of a single lexical token."""

    __slots__ = ("type", "value", "lineno", "lexpos", "end", "lexer")

    def __init__(
        self,
        tok_type: str,
        value: Any,
        lineno: int = 1,
        lexpos: int = 0,
        end: Optional[int] = None,
        lexer: Any = None,
    ):
        self.type = tok_type
        self.value = value
        self.lineno = lineno
        self.lexpos = lexpos
        self.end = end if end is not None else (lexpos + len(str(value)))
        self.lexer = lexer

    @property
    def index(self) -> int:
        return self.lexpos

    @index.setter
    def index(self, val: int) -> None:
        self.lexpos = val

    def __repr__(self) -> str:
        return f"Token(type={self.type!r}, value={self.value!r}, lineno={self.lineno}, pos={self.lexpos})"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Token):
            # Recognize the other public token implementation without loading
            # the extension in explicitly selected Python mode.
            native = sys.modules.get("ply_rs._core_rs")
            if native is None or not isinstance(other, getattr(native, "Token", ())):
                return NotImplemented
        return self.type == other.type and self.value == other.value


class _LazyErrorToken(Token):
    """Keep the original input alive and slice its suffix only when requested."""

    __slots__ = ("_source", "_start", "_value")

    def __init__(self, source: str, lexpos: int, lineno: int = 1, lexer: Any = None,
                 end: Optional[int] = None):
        self.type = "error"
        self._source = source
        self._start = lexpos
        self._value = None
        self.lineno = lineno
        self.lexpos = lexpos
        # Engines already know the input length. Reusing it also avoids running
        # arbitrary str-subclass __len__ code while creating an error token.
        self.end = str.__len__(source) if end is None else end
        self.lexer = lexer

    @property
    def value(self) -> Any:
        source = self._source
        if source is not None:
            materialized = source[self._start:]
            # String-subclass slicing can assign value while this read is in
            # progress. Keep that assignment instead of caching over it.
            if self._source is source:
                self._value = materialized
                self._source = None
        return self._value

    @value.setter
    def value(self, value: Any) -> None:
        source = self._source
        self._source = None
        self._value = value
        # Release the original input after the assignment is visible to its
        # finalizer, which may read or assign this token's value again.
        del source


class LexRule:
    """A single token specification rule."""

    def __init__(
        self,
        name: str,
        pattern: str,
        func: Optional[Callable[..., Any]] = None,
        priority: int = 0,
        flags: int = 0,
    ):
        self.name = name
        self.pattern = pattern
        self.func = func
        self.priority = priority
        self.flags = flags
        self.compiled: Pattern[str] = re.compile(pattern, flags)
