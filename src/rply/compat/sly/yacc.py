"""
SLY (Sylvester's Lex-Yacc) compatible parser module.
Drop-in replacement for `from sly import Parser, _`.
"""

import inspect
import sys
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union
from rply.core.grammar import Grammar, GrammarError
from rply.core.lr_table import LRTable
from rply.core.lr_parser import LRParser, ProductionProxy
from rply.compat.sly.decorators import _decorator


_ = _decorator


class SlyProduction:
    """SLY action view: RHS indexing and source positions are adapter concerns."""

    def __init__(self, proxy: ProductionProxy):
        self._proxy = proxy

    def __getitem__(self, index: int) -> Any:
        return self._proxy[index + 1] if index >= 0 else self._proxy[index]

    def __len__(self) -> int:
        return len(self._proxy) - 1

    @property
    def lineno(self) -> int:
        return next((s.lineno for s in self._proxy.slice[1:] if s.lineno), 0)

    @property
    def index(self) -> int:
        return next((s.lexpos for s in self._proxy.slice[1:] if s.lexpos is not None), 0)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._proxy, name)


class ParserMetaDict(dict):
    """Metaclass dict that enables unquoted tokens in class bodies."""

    def __init__(self):
        super().__init__()
        self["_"] = _decorator

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self and callable(value) and hasattr(value, "rules"):
            value.next_func = self[key]
        super().__setitem__(key, value)

    def __getitem__(self, key: str) -> Any:
        if key not in self and key.isupper() and not key.startswith("_"):
            return key.upper()
        return super().__getitem__(key)


class ParserMeta(type):
    """Metaclass that collects SLY parser rules and handles @_ decorators."""

    @classmethod
    def __prepare__(mcs, name: str, bases: Tuple[type, ...]) -> ParserMetaDict:
        return ParserMetaDict()

    def __new__(mcs, name: str, bases: Tuple[type, ...], attrs: Dict[str, Any]) -> type:
        if "_" in attrs:
            del attrs["_"]

        cls = super().__new__(mcs, name, bases, attrs)
        rules: List[Tuple[str, List[str], Callable[..., Any]]] = []

        for key, val in attrs.items():
            funcs: List[Callable[..., Any]] = []
            curr = val
            while callable(curr) and hasattr(curr, "rules"):
                funcs.append(curr)
                curr = getattr(curr, "next_func", None)

            for func in reversed(funcs):
                func_rules = getattr(func, "rules")
                for rule_str in func_rules:
                    if ":" in rule_str or "::=" in rule_str:
                        delim = ":" if ":" in rule_str else "::="
                        lhs, rhs = rule_str.split(delim, 1)
                        lhs = lhs.strip()
                        rhs_syms = rhs.strip().split()
                    else:
                        lhs = key
                        rhs_syms = rule_str.strip().split()
                    rules.append((lhs, rhs_syms, func))

        cls._sly_rules = rules  # type: ignore
        return cls


class Parser(metaclass=ParserMeta):
    """
    SLY-compatible Parser class.
    """

    tokens: Union[Set[str], Sequence[str]] = ()
    precedence: Sequence[Tuple[str, Union[List[str], Tuple[str, ...]]]] = ()
    start: Optional[str] = None
    _sly_rules: List[Any] = []

    def __init__(self):
        self._grammar = Grammar(terminals=set(self.tokens) if self.tokens else None)

        if self.precedence:
            prec_specs: List[Tuple[str, List[str]]] = []
            for item in self.precedence:
                assoc = item[0]
                toks = [str(t) for t in item[1:]]
                prec_specs.append((assoc, toks))
            self._grammar.set_precedence(prec_specs)

        # Register rules
        for lhs, rhs, func in self._sly_rules:
            bound_func = func.__get__(self, self.__class__)
            clean_rhs = list(rhs)
            rule_prec = None
            if "%prec" in clean_rhs:
                idx = clean_rhs.index("%prec")
                if idx + 1 < len(clean_rhs):
                    rule_prec = clean_rhs[idx + 1]
                    del clean_rhs[idx : idx + 2]
            self._grammar.add_production(
                name=lhs,
                syms=clean_rhs,
                func=lambda proxy, action=bound_func: action(SlyProduction(proxy)),
                precedence=rule_prec,
            )

        self._grammar.set_start(self.start)
        self._table = LRTable(self._grammar)

        err_func = getattr(self, "error", None)
        bound_err = err_func.__get__(self, self.__class__) if callable(err_func) else None
        self._engine = LRParser(self._table, error_func=bound_err)

    def parse(self, token_stream: Union[Iterator[Any], Sequence[Any]]) -> Any:
        return self._engine.parse(token_stream)
