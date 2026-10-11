"""
SLY (Sylvester's Lex-Yacc) compatible lexer module.
Drop-in replacement for `from sly import Lexer`.
"""

import inspect
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, Union
from rply.core.lexer_engine import LexerEngine, Token as CoreToken, LexError
from rply.compat.sly.decorators import _decorator


class Token:
    """Representation of a SLY token."""

    __slots__ = ("type", "value", "lineno", "index", "end")

    def __init__(self, tok_type: str = "", value: Any = None, lineno: int = 1, index: int = 0, end: int = 0):
        self.type = tok_type
        self.value = value
        self.lineno = lineno
        self.index = index
        self.end = end

    def __repr__(self) -> str:
        return f"Token(type={self.type!r}, value={self.value!r}, lineno={self.lineno}, index={self.index}, end={self.end})"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Token):
            return self.type == other.type and self.value == other.value
        return False


class TokenStr(str):
    """String token value that supports remapping or unquoted token references."""

    @staticmethod
    def __new__(cls, value: str, key: Optional[str] = None, remap: Optional[Dict[Any, Any]] = None):
        self = super().__new__(cls, value)
        self.key = key
        self.remap = remap
        return self

    def __setitem__(self, key: Any, value: Any) -> None:
        if self.remap is not None and self.key is not None:
            self.remap[(self.key, key)] = value


class LexerMetaDict(dict):
    """Namespace dictionary that allows unquoted token references like tokens = { ID, NUMBER }."""

    def __init__(self):
        super().__init__()
        self.remap: Dict[Any, Any] = {}

    def __getitem__(self, key: str) -> Any:
        if key not in self and key.split("ignore_")[-1].isupper() and not key.startswith("_"):
            return TokenStr(key, key, self.remap)
        return super().__getitem__(key)


class LexerMeta(type):
    """Metaclass that collects token rules and enables SLY metaclass syntax."""

    @classmethod
    def __prepare__(mcs, name: str, bases: Tuple[type, ...]) -> LexerMetaDict:
        namespace = LexerMetaDict()
        namespace["_"] = _decorator
        return namespace

    def __new__(mcs, name: str, bases: Tuple[type, ...], attrs: Dict[str, Any]) -> type:
        cls = super().__new__(mcs, name, bases, attrs)
        rules: List[Tuple[str, str, Optional[Callable[..., Any]]]] = []

        tokens_attr = attrs.get("tokens", set())
        valid_tokens = set(tokens_attr) if tokens_attr else set()

        for key, val in attrs.items():
            if key.startswith("_") or key in ("tokens", "ignore", "literals"):
                continue

            # Method rules
            if callable(val) and not inspect.isclass(val):
                # Check for rules attribute from @_ decorator
                func_rules = getattr(val, "rules", None)
                if func_rules:
                    for pat_str in func_rules:
                        rules.append((key, pat_str.strip(), val))
                    valid_tokens.add(key)
                else:
                    # Function rule, docstring or attribute pattern can define regex
                    pat = getattr(val, "pattern", getattr(val, "__doc__", None))
                    if pat:
                        rules.append((key, pat.strip(), val))
                        valid_tokens.add(key)
                    elif key.startswith("ignore_"):
                        # Method for ignore rule
                        pass
                    elif key == "error":
                        pass
                    else:
                        rules.append((key, "", val))

            # String attribute rules, e.g. NUMBER = r'\d+'
            elif isinstance(val, str):
                rules.append((key, val, None))
                valid_tokens.add(key)

        cls._sly_rules = rules  # type: ignore
        cls.tokens = valid_tokens  # type: ignore
        return cls


class Lexer(metaclass=LexerMeta):
    """
    SLY-compatible Lexer class.
    """

    tokens: Set[str] = set()
    ignore: str = ""
    literals: Union[str, Set[str], List[str]] = ""
    _sly_rules: List[Any] = []

    @property
    def index(self) -> int:
        return self._engine.pos

    @index.setter
    def index(self, val: int) -> None:
        self._engine.pos = val

    @property
    def lineno(self) -> int:
        return self._engine.lineno

    @lineno.setter
    def lineno(self, val: int) -> None:
        self._engine.lineno = val

    def __init__(self):
        self._engine = LexerEngine()
        if self.ignore:
            self._engine.set_ignore("INITIAL", self.ignore)
        if self.literals:
            self._engine.set_literals(self.literals)

        # Hook up error handler if defined
        error_handler = getattr(self, "error", None)
        if callable(error_handler):
            def wrap_err(tok: CoreToken):
                sly_tok = Token(tok.type, tok.value, tok.lineno, tok.lexpos, tok.end)
                old_index = sly_tok.index
                res = error_handler(sly_tok)
                if sly_tok.index != old_index:
                    self._engine.pos = sly_tok.index
                return res
            self._engine.set_error_func("INITIAL", wrap_err)

        for name, pat, func in self._sly_rules:
            bound_func = None
            if func is not None:
                orig_method = func.__get__(self, self.__class__)
                def make_wrapper(m, tname):
                    def wrapper(core_tok: CoreToken):
                        sly_tok = Token(tname, core_tok.value, core_tok.lineno, core_tok.lexpos, core_tok.end)
                        res = m(sly_tok)
                        if res is None:
                            return None
                        core_tok.type = getattr(res, "type", tname)
                        core_tok.value = getattr(res, "value", res)
                        core_tok.lineno = getattr(res, "lineno", core_tok.lineno)
                        return core_tok
                    return wrapper
                bound_func = make_wrapper(orig_method, name)

            if pat:
                self._engine.add_rule("INITIAL", name, pat, bound_func)

    def tokenize(self, text: str) -> Iterator[Token]:
        """Tokenize input string and yield SLY Token instances."""
        self._engine.input(text)
        while True:
            core_tok = self._engine.token()
            if core_tok is None:
                break
            yield Token(
                tok_type=core_tok.type,
                value=core_tok.value,
                lineno=core_tok.lineno,
                index=core_tok.lexpos,
                end=core_tok.end,
            )
