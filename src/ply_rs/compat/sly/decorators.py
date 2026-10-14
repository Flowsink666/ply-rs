"""Shared SLY rule decorator for lexer and parser class namespaces."""

from typing import Any, Callable


def _decorator(*rules: str) -> Callable[..., Any]:
    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        func.rules = list(rules) + list(getattr(func, "rules", []))
        return func
    return decorate
