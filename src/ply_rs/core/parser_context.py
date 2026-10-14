"""Explicit callback context and shared semantic action behavior."""

from dataclasses import dataclass
from typing import Any, Callable, Optional

from ply_rs.core.production import ProductionProxy


from ply_rs.backends import native_core

TokenCursor = native_core.TokenCursor


def _invoke_proxy(function, proxy, symbols):
    """Retain original symbols in one action frame, including its traceback."""
    return function(proxy)


@dataclass(frozen=True)
class ParseContext:
    lexer: Any = None
    parser: Any = None
    tracking: bool = True
    error_func: Optional[Callable] = None
    control: Any = None
    recovery_style: str = "core"

    def reduce(self, production, lhs, popped, stack, slice_items=None):
        """Run one action, optionally using an already assembled proxy slice.

        The native runtime can prepare one fresh LHS/RHS list for this method.
        Custom reduction hooks retain RHS-only arguments. Ordinary actions use
        the native lazy view when this method and proxy construction are intact.
        Every reduction keeps its own proxy.
        """
        if production.func is None:
            return lhs.value
        if production.action_style == "values":
            n = len(popped)
            if n == 1:
                result = production.func(popped[0].value)
            elif n == 2:
                result = production.func(popped[0].value, popped[1].value)
            elif n == 3:
                result = production.func(popped[0].value, popped[1].value, popped[2].value)
            elif n == 0:
                result = production.func()
            else:
                result = production.func(*(symbol.value for symbol in popped))
        else:
            proxy = ProductionProxy(
                [lhs] + popped if slice_items is None else slice_items,
                stack=stack, lexer=self.lexer, parser=self.parser,
                namemap=production.namemap,
            )
            result = production.func(proxy)
        if result is not None:
            lhs.value = result
        return lhs.value
