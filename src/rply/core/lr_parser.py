"""Public LR parser interface with optional native acceleration."""

from typing import Any, Callable, Iterator, Optional, Sequence, Union

from rply.backends import HAS_RUST_CORE, fallback, native_core
from rply.backends.python.parser import PythonLRParser
from rply.core.lr_table import LRTable
from rply.core.production import ParsedSymbol, ProductionProxy, ParserError


class LRParser(PythonLRParser):
    """Run a shared parse table through the selected runtime backend."""

    def __init__(self, table: LRTable, error_func: Optional[Callable[..., Any]] = None):
        super().__init__(table, error_func)
        self._rs_engine = None
        self.backend = "python"
        if HAS_RUST_CORE:
            try:
                num_states = len(table.states) or (max(table.action) + 1 if table.action else 0)
                value_funcs = [
                    p.func if p.action_style == "values" else None
                    for p in self.productions
                ]
                metadata = (
                    [p.name for p in self.productions],
                    [p.len for p in self.productions],
                    [bool(p.func) for p in self.productions],
                    [bool(func) for func in value_funcs],
                )
                self._has_value_actions = any(metadata[-1])
                if (
                    getattr(table, "_rs_table", None) is not None
                    and num_states == table._rs_table.num_states
                    and self.action == table._native_action_snapshot
                    and self.goto == table._native_goto_snapshot
                ):
                    self._rs_engine = native_core.LRParserEngine.from_table(table._rs_table, *metadata)
                else:
                    self._rs_engine = native_core.LRParserEngine(
                        num_states, self.action, self.goto, *metadata,
                    )
            except Exception as error:
                fallback("parser", error)
            else:
                self.backend = "rust"

    def _parse_with_rust(self, token_stream: Any, lexer: Any = None) -> Any:
        productions = self.productions
        parser_self = self

        def reduce_cb(p_num: int, lhs_sym: Any, popped_syms: list) -> Any:
            p = productions[p_num]
            if not p.func:
                return lhs_sym.value
            if p.action_style == "values":
                res = p.func(*(s.value for s in popped_syms))
                return lhs_sym.value if res is None else res
            slice_items = [lhs_sym] + popped_syms
            proxy = ProductionProxy(
                slice_items,
                stack=None,
                lexer=lexer,
                parser=parser_self,
                namemap=p.namemap,
            )

            res = p.func(proxy)
            return proxy[0] if res is None else res

        return self._rs_engine.parse(
            token_stream,
            reduce_cb,
            self.error_func,
            # Resolve callables for each parse, just as proxy actions do, so
            # Production.bind() and rebinding an action remain effective.
            [p.func for p in productions] if self._has_value_actions else None,
        )

    def parse(
        self, token_stream: Union[Iterator[Any], Sequence[Any]],
        lexer: Any = None, tracking: bool = True,
    ) -> Any:
        if self._rs_engine is not None:
            return self._parse_with_rust(token_stream, lexer=lexer)
        return super().parse(token_stream, lexer=lexer, tracking=tracking)
