"""Explicit callback context and shared semantic action behavior."""

from dataclasses import dataclass
from typing import Any, Callable, Optional

from rply.core.production import ProductionProxy


@dataclass(frozen=True)
class ParseContext:
    lexer: Any = None
    parser: Any = None
    tracking: bool = True
    error_func: Optional[Callable] = None

    def reduce(self, production, lhs, popped, stack):
        if not production.func:
            return lhs.value
        if production.action_style == "values":
            result = production.func(*(symbol.value for symbol in popped))
        else:
            proxy = ProductionProxy(
                [lhs] + popped, stack=stack, lexer=self.lexer, parser=self.parser,
                namemap=production.namemap,
            )
            result = production.func(proxy)
        if result is not None:
            lhs.value = result
        return lhs.value
