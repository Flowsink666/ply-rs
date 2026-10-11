"""
Grammar symbols definition.
"""

from typing import Any, Optional


class GrammarSymbol:
    """Represents a symbol in the grammar (either Terminal or NonTerminal)."""

    __slots__ = ("name", "is_terminal")

    def __init__(self, name: str, is_terminal: bool = False):
        self.name = name
        self.is_terminal = is_terminal

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        kind = "Terminal" if self.is_terminal else "NonTerminal"
        return f"{kind}({self.name!r})"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, GrammarSymbol):
            return self.name == other.name and self.is_terminal == other.is_terminal
        if isinstance(other, str):
            return self.name == other
        return False

    def __hash__(self) -> int:
        return hash((self.name, self.is_terminal))


EOF_SYMBOL = "$end"
ERROR_SYMBOL = "error"
START_SYMBOL = "S'"
EPSILON_SYMBOL = "$epsilon"
