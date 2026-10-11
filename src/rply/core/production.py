"""Shared parser symbols and semantic action interface."""

import builtins
from typing import Any, Dict, List, Optional, Union


def make_namemap(symbols) -> Dict[str, int]:
    """Build RHS name-to-index aliases once, preserving declaration order."""
    names = {}
    counts = {}
    for index, symbol in enumerate(symbols, 1):
        count = counts.get(symbol, 0)
        names.setdefault(f"{symbol}{count}", index)
        names.setdefault(symbol, index)
        counts[symbol] = count + 1
    return names


class ParserError(Exception):
    """Exception raised during parsing."""
    pass


class ParsedSymbol:
    """Represents a symbol on the parser stack."""

    __slots__ = ("type", "value", "lineno", "endlineno", "lexpos", "endlexpos")

    def __init__(
        self,
        sym_type: str,
        value: Any = None,
        lineno: int = 1,
        endlineno: int = 1,
        lexpos: int = 0,
        endlexpos: int = 0,
    ):
        self.type = sym_type
        self.value = value
        self.lineno = lineno
        self.endlineno = endlineno
        self.lexpos = lexpos
        self.endlexpos = endlexpos

    def __repr__(self) -> str:
        return f"ParsedSymbol(type={self.type!r}, value={self.value!r})"


class ProductionProxy:
    """
    Proxy object passed to grammar rule reduction functions.
    Compatible with:
      - PLY: p[0], p[1], len(p), p.lineno(n), p.lexpos(n), p.slice
      - SLY: p.expr0, p.expr1, p.index, p.lineno
    """

    __slots__ = ("_slice", "_stack", "lexer", "parser", "_namemap")

    def __init__(
        self,
        slice_items: List[ParsedSymbol],
        stack: Optional[List[ParsedSymbol]] = None,
        lexer: Any = None,
        parser: Any = None,
        namemap: Optional[Dict[str, int]] = None,
    ):
        self._slice = slice_items
        self._stack = stack
        self.lexer = lexer
        self.parser = parser
        self._namemap = namemap

    @property
    def slice(self) -> List[ParsedSymbol]:
        return self._slice

    def __getitem__(self, n: Union[int, builtins.slice]) -> Any:
        if isinstance(n, slice):
            return [s.value for s in self._slice[n]]
        if n >= 0:
            return self._slice[n].value
        elif self._stack is not None:
            return self._stack[n].value
        else:
            raise IndexError("Stack is not available for negative index")

    def __setitem__(self, n: int, v: Any) -> None:
        self._slice[n].value = v

    def __len__(self) -> int:
        return len(self._slice)

    # --- PLY compatibility methods ---
    def lineno(self, n: int) -> int:
        return getattr(self._slice[n], "lineno", 0)

    def set_lineno(self, n: int, lineno: int) -> None:
        self._slice[n].lineno = lineno

    def linespan(self, n: int) -> tuple[int, int]:
        start = getattr(self._slice[n], "lineno", 0)
        end = getattr(self._slice[n], "endlineno", start)
        return start, end

    def lexpos(self, n: int) -> int:
        return getattr(self._slice[n], "lexpos", 0)

    def set_lexpos(self, n: int, lexpos: int) -> None:
        self._slice[n].lexpos = lexpos

    def lexspan(self, n: int) -> tuple[int, int]:
        start = getattr(self._slice[n], "lexpos", 0)
        end = getattr(self._slice[n], "endlexpos", start)
        return start, end

    def error(self) -> None:
        raise SyntaxError("Syntax error requested in grammar action")

    # --- SLY compatibility attributes & named access ---
    @property
    def lineno_attr(self) -> int:
        for s in self._slice:
            if s.lineno:
                return s.lineno
        return 0

    @property
    def index(self) -> int:
        for s in self._slice:
            if s.lexpos is not None:
                return s.lexpos
        return 0

    def __getattr__(self, name: str) -> Any:
        if name == "lineno":
            return self.lineno_attr
        if self._namemap is None:
            self._namemap = make_namemap(s.type for s in self._slice[1:])
        try:
            return self._slice[self._namemap[name]].value
        except KeyError:
            raise AttributeError(f"No symbol or attribute {name!r} in production") from None
