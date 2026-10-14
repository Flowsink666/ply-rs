"""Public parser facade with a selected runtime and explicit callback context."""

from typing import Any, Callable, Iterator, Optional, Sequence, Union

from ply_rs.backends import native_core
from ply_rs.backends.rust_parser import RustParserRuntime
from ply_rs.core.lr_table import LRTable
from ply_rs.core.parser_context import ParseContext, TokenCursor
from ply_rs.core.production import ParsedSymbol, ProductionProxy, ParserError
from ply_rs.core.table_data import ParseTableData


class LRParser:
    def __init__(self, table: LRTable, error_func: Optional[Callable[..., Any]] = None,
                 *, frozen: bool = False):
        self.table = table
        self._data = ParseTableData.from_table(table)
        if frozen:
            self._data = self._data.frozen_copy()
        self.error_func = error_func
        self._sessions = []
        self._runtime: RustParserRuntime
        self.backend, self._runtime = self._create_runtime(self._data)

    def _create_runtime(self, data):
        return "rust", RustParserRuntime(data, native_core, getattr(self.table, "runtime_artifact", None))

    @property
    def frozen(self):
        return self._data.frozen

    def freeze(self):
        """Compile a detached, read-only snapshot and return this parser.

        Source-table and production edits made afterward do not affect it.
        Callback state and the per-call lexer/parser context remain live.
        """
        if not self.frozen:
            data = self._data.frozen_copy()
            backend, runtime = self._create_runtime(data)
            self._data, self._runtime, self.backend = data, runtime, backend
        return self

    def _require_editable(self):
        if self.frozen:
            raise TypeError("Frozen parser tables and productions cannot be replaced")

    @property
    def productions(self):
        return self._data.productions

    @productions.setter
    def productions(self, value):
        self._require_editable()
        self._data.productions = value

    @property
    def action(self):
        return self._data.action

    @action.setter
    def action(self, value):
        self._require_editable()
        self._data.action = value

    @property
    def goto(self):
        return self._data.goto

    @goto.setter
    def goto(self, value):
        self._require_editable()
        self._data.goto = value

    def parse(self, token_stream: Union[Iterator[Any], Sequence[Any]],
              lexer: Any = None, tracking: bool = True, *, parser: Any = None,
              recovery_style: str = "core") -> Any:
        control = native_core.ParserControl()
        cursor = token_stream if isinstance(token_stream, TokenCursor) else TokenCursor(token_stream)
        context = ParseContext(
            lexer=lexer, parser=self if parser is None else parser,
            tracking=tracking, error_func=self.error_func,
            control=control, recovery_style=recovery_style,
        )
        self._sessions.append((control, cursor, context))
        try:
            return self._runtime.parse(cursor, context=context)
        finally:
            self._sessions.pop()

    def _active_session(self):
        if not self._sessions:
            raise RuntimeError("Parser recovery controls require an active parse")
        return self._sessions[-1]

    def errok(self):
        self._active_session()[0].errok()

    def restart(self):
        self._active_session()[0].restart()

    def token(self):
        return next(self._active_session()[1], None)
