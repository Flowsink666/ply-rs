"""
Modern, class-based Parser API with @rule decorator and typed callbacks.
"""

import inspect
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union
from rply.core.grammar import Grammar, GrammarError
from rply.core.lr_table import LRTable
from rply.core.lr_parser import LRParser, ProductionProxy


def rule(*specs: str, precedence: Optional[str] = None, action_style: str = "auto") -> Callable[..., Any]:
    """
    Decorator defining one or more grammar productions on a parser method.
    Example:
        @rule("expr : expr PLUS expr", "expr : expr MINUS expr")
        def binary_op(self, p):
            ...
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        existing_rules = getattr(func, "_grammar_rules", [])
        new_rules = [(spec, precedence, action_style) for spec in specs]
        func._grammar_rules = existing_rules + new_rules  # type: ignore
        return func
    return decorator


def _parse_rule_spec(spec: str) -> Tuple[str, List[str]]:
    """Parse string specification like 'expr : expr PLUS term' into LHS and RHS list."""
    if "::=" in spec:
        lhs, rhs = spec.split("::=", 1)
    elif ":" in spec:
        lhs, rhs = spec.split(":", 1)
    else:
        raise GrammarError(f"Invalid rule specification syntax {spec!r}, missing ':'")
    return lhs.strip(), rhs.strip().split()


class ParserMeta(type):
    """Metaclass that collects grammar rules from class methods."""

    def __new__(mcs, name: str, bases: Tuple[type, ...], attrs: Dict[str, Any]) -> type:
        cls = super().__new__(mcs, name, bases, attrs)
        rules: List[Tuple[str, List[str], Callable[..., Any], Optional[str], str, int, str]] = []

        # Preserve rule positions (and the implicit start rule) on override.
        # Attribute lookup follows the MRO and diamonds contribute each once.
        rule_names = dict.fromkeys(
            attr_name for base in cls.__mro__[1:]
            for attr_name in base.__dict__.get("_rule_names", ())
        )
        rule_names.update(attrs)
        collected_names = []
        for attr_name in rule_names:
            attr_val = getattr(cls, attr_name, None)
            if callable(attr_val) and hasattr(attr_val, "_grammar_rules"):
                collected_names.append(attr_name)
                spec_list = getattr(attr_val, "_grammar_rules")
                filename = inspect.getsourcefile(attr_val) or ""
                try:
                    _, firstline = inspect.getsourcelines(attr_val)
                except Exception:
                    firstline = 0

                for spec, prec, action_style in spec_list:
                    lhs, rhs = _parse_rule_spec(spec)
                    rules.append((lhs, rhs, attr_val, prec, filename, firstline, action_style))

        cls._collected_rules = rules  # type: ignore
        cls._rule_names = tuple(collected_names)  # type: ignore
        return cls


class Parser(metaclass=ParserMeta):
    """
    Modern class-based Parser base class.
    """

    tokens: Union[Set[str], Sequence[str]] = ()
    precedence: Sequence[Tuple[str, Union[List[str], Tuple[str, ...]]]] = ()
    start: Optional[str] = None
    _collected_rules: List[Any] = []

    def __init__(self):
        self._grammar = Grammar(terminals=set(self.tokens) if self.tokens else None)

        if self.precedence:
            # Normalize precedence specs
            formatted_prec: List[Tuple[str, List[str]]] = []
            for assoc, tok_list in self.precedence:
                formatted_prec.append((assoc, list(tok_list)))
            self._grammar.set_precedence(formatted_prec)

        # Register productions
        for lhs, rhs, func, prec, fn, line, action_style in self._collected_rules:
            bound_func = func.__get__(self, self.__class__)
            self._grammar.add_production(
                name=lhs,
                syms=rhs,
                func=bound_func,
                file=fn,
                line=line,
                precedence=prec,
                action_style=action_style,
            )

        self._grammar.set_start(self.start)
        self._table = LRTable(self._grammar)
        self._engine = LRParser(self._table, error_func=getattr(self, "error", None))

    @property
    def table(self) -> LRTable:
        return self._table

    @property
    def frozen(self) -> bool:
        return self._engine.frozen

    def freeze(self):
        """Use a read-only grammar snapshot for subsequent parses."""
        self._engine.freeze()
        return self

    def parse(
        self,
        token_stream: Union[Iterator[Any], Sequence[Any]],
        lexer: Any = None,
    ) -> Any:
        return self._engine.parse(token_stream, lexer=lexer, parser=self)


ModernParser = Parser
