"""Backend-neutral parse table data and build results."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ParseTableData:
    action: Dict[int, Dict[str, Optional[int]]]
    goto: Dict[int, Dict[str, int]]
    productions: List[Any]
    states: List[Any]
    conflicts: List[Any]
    sr_conflicts: List[Any]
    rr_conflicts: List[Any]

    @classmethod
    def from_table(cls, table):
        return cls(table.action, table.goto, table.productions, getattr(table, "states", []),
                   getattr(table, "conflicts", []), getattr(table, "sr_conflicts", []),
                   getattr(table, "rr_conflicts", []))

    @property
    def num_states(self):
        return max(len(self.states), max(self.action, default=-1) + 1,
                   max(self.goto, default=-1) + 1)


@dataclass
class TableBuildResult:
    data: ParseTableData
    artifact: Any = None
