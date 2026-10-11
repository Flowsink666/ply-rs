"""Shared token, lexical rule, and error types."""

import re
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
        if isinstance(other, Token):
            return self.type == other.type and self.value == other.value
        return False


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
