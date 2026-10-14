"""Backend-neutral parse table data and build results."""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from rply.core.grammar import Production


class _FrozenProduction(Production):
    """Snapshot runtime metadata while retaining the production inspection API."""

    __slots__ = ("_sealed",)

    def __init__(self, production):
        for name in Production.__slots__:
            value = getattr(production, name)
            if name == "namemap" and value is not None:
                value = MappingProxyType(dict(value))
            elif name == "lookaheads":
                value = MappingProxyType({state: tuple(tokens) for state, tokens in value.items()})
            elif name == "usyms":
                value = frozenset(value)
            elif name == "lr_items":
                value = tuple(value)
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise TypeError("Frozen parser productions cannot be modified")
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        raise TypeError("Frozen parser productions cannot be modified")


def native_rows(rows):
    """PyO3 consumes dictionaries; frozen public views stay read-only."""
    if isinstance(rows, dict):
        return rows
    return {state: dict(row) for state, row in rows.items()}


class ParserMetadata(NamedTuple):
    """Resolved runtime fields; adapters preserve their meaning and order."""

    names: Tuple[str, ...]
    lengths: Tuple[int, ...]
    has_actions: Tuple[bool, ...]
    value_actions: Tuple[bool, ...]


@dataclass
class ParseTableData:
    action: Dict[int, Dict[str, Optional[int]]]
    goto: Dict[int, Dict[str, int]]
    productions: List[Any]
    states: List[Any]
    conflicts: List[Any]
    sr_conflicts: List[Any]
    rr_conflicts: List[Any]
    frozen: bool = False
    _num_states: Optional[int] = field(default=None, init=False, repr=False)

    @classmethod
    def from_table(cls, table):
        return cls(table.action, table.goto, table.productions, getattr(table, "states", []),
                   getattr(table, "conflicts", []), getattr(table, "sr_conflicts", []),
                   getattr(table, "rr_conflicts", []))

    @property
    def num_states(self):
        if self.frozen:
            return self._num_states
        return max(len(self.states), max(self.action, default=-1) + 1,
                   max(self.goto, default=-1) + 1)

    def runtime_metadata(self) -> ParserMetadata:
        """Describe current productions without re-inferring action bindings."""
        return ParserMetadata(
            tuple(p.name for p in self.productions),
            tuple(p.len for p in self.productions),
            tuple(p.func is not None for p in self.productions),
            tuple(p.func is not None and p.action_style == "values" for p in self.productions),
        )

    def frozen_copy(self):
        """Detach tables and callback bindings from their editable source objects."""
        result = type(self)(
            MappingProxyType({state: MappingProxyType(dict(row)) for state, row in self.action.items()}),
            MappingProxyType({state: MappingProxyType(dict(row)) for state, row in self.goto.items()}),
            tuple(_FrozenProduction(production) for production in self.productions),
            tuple(self.states), tuple(self.conflicts), tuple(self.sr_conflicts), tuple(self.rr_conflicts),
            frozen=True,
        )
        result._num_states = self.num_states
        return result


@dataclass
class TableBuildResult:
    data: ParseTableData
    artifact: Any = None
