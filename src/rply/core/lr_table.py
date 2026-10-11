"""Public LALR table interface with optional native acceleration."""

from rply.backends import HAS_RUST_CORE, fallback
from rply.backends.python.lr_table import PythonLRTable, digraph
from rply.backends.rust import build_table
from rply.core.conflicts import ConflictRecord


class LRTable(PythonLRTable):
    """Generate a parse table and expose the backend that actually built it."""

    def _build(self) -> None:
        self.backend = "python"
        self._rs_table = None
        if HAS_RUST_CORE:
            try:
                build_table(self)
            except Exception as error:
                fallback("table builder", error)
                self.action.clear()
                self.goto.clear()
                self.conflicts.clear()
                self.sr_conflicts.clear()
                self.rr_conflicts.clear()
            else:
                self.backend = "rust"
                return
        super()._build()
