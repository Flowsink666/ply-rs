"""Public parser facade with a selected runtime and explicit callback context."""

from typing import Any, Callable, Iterator, Optional, Sequence, Union

from rply.backends import HAS_RUST_CORE, fallback, native_core
from rply.backends.interfaces import ParserRuntime
from rply.backends.python.parser import PythonLRParser
from rply.backends.rust_parser import RustParserRuntime
from rply.core.lr_table import LRTable
from rply.core.parser_context import ParseContext
from rply.core.production import ParsedSymbol, ProductionProxy, ParserError
from rply.core.table_data import ParseTableData


class LRParser:
    def __init__(self, table: LRTable, error_func: Optional[Callable[..., Any]] = None):
        self.table = table
        self._data = ParseTableData.from_table(table)
        self.error_func = error_func
        self._runtime: ParserRuntime
        self.backend = "python"
        data = self._data
        if HAS_RUST_CORE:
            try:
                self._runtime = RustParserRuntime(data, native_core, getattr(table, "runtime_artifact", None))
            except Exception as error:
                fallback("parser", error)
            else:
                self.backend = "rust"
                return
        self._runtime = PythonLRParser(data)

    @property
    def productions(self):
        return self._data.productions

    @productions.setter
    def productions(self, value):
        self._data.productions = value

    @property
    def action(self):
        return self._data.action

    @action.setter
    def action(self, value):
        self._data.action = value

    @property
    def goto(self):
        return self._data.goto

    @goto.setter
    def goto(self, value):
        self._data.goto = value

    def parse(self, token_stream: Union[Iterator[Any], Sequence[Any]],
              lexer: Any = None, tracking: bool = True, *, parser: Any = None) -> Any:
        context = ParseContext(
            lexer=lexer, parser=self if parser is None else parser,
            tracking=tracking, error_func=self.error_func,
        )
        return self._runtime.parse(token_stream, context=context)
