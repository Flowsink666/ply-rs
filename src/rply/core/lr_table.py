"""Public parse table facade over backend-neutral build results."""

from rply.backends import native_core
from rply.core.grammar import digraph
from rply.backends.rust import build_table
from rply.core.conflicts import ConflictRecord


class LRTable:
    def __init__(self, grammar):
        self.grammar = grammar
        self.backend = "rust"
        result = build_table(grammar, native_core)
        self.action = result.data.action
        self.goto = result.data.goto
        self.productions = result.data.productions
        self.states = result.data.states
        self.conflicts = result.data.conflicts
        self.sr_conflicts = result.data.sr_conflicts
        self.rr_conflicts = result.data.rr_conflicts
        self._backend_artifact = result.artifact

    @property
    def runtime_artifact(self):
        """Opaque backend-owned build artifact; consumers do not inspect it."""
        return self._backend_artifact
