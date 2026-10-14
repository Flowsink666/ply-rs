"""
SLY (Sylvester's Lex-Yacc) compatible lexer module.
Drop-in replacement for `from sly import Lexer`.
"""

import inspect
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, Union
from rply.core.lexer_engine import LexerEngine, Token as CoreToken, LexError
from rply.compat._tokens import (
    _DeferredTokenValue, _TokenValueStorage, _public_token_state, _value_property,
)
from rply.compat.sly.decorators import _decorator


class Token(_TokenValueStorage):
    """Representation of a SLY token."""

    __slots__ = ("type", "lineno", "index", "end")

    value = _value_property

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

    def __getstate__(self):
        return _public_token_state(self, ("type", "value", "lineno", "index", "end"))


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

    def __setitem__(self, key: str, value: Any) -> None:
        if isinstance(value, str):
            value = TokenStr(value, key, self.remap)
        elif callable(value) and isinstance(self.get(key), str):
            # SLY permits a pattern assignment followed by a token method.
            value.pattern = self[key]
        super().__setitem__(key, value)

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

        tokens_attr = getattr(cls, "tokens", set())
        valid_tokens = set(tokens_attr) if tokens_attr else set()
        inherited_rules = {}
        remap = {}
        for base in reversed(cls.__mro__[1:]):
            valid_tokens.update(getattr(base, "tokens", ()))
            remap.update(base.__dict__.get("_remap", {}))
            for key, pat, func in base.__dict__.get("_sly_rules", ()):
                inherited_rules.setdefault(key, [])
        rule_names = dict.fromkeys(
            key for base in cls.__mro__[1:]
            for key in base.__dict__.get("_sly_rule_names", ())
        )
        rule_names.update(attrs)
        # Resolve each inherited pattern through the MRO before applying overrides.
        for key in inherited_rules:
            for base in cls.__mro__[1:]:
                patterns = [pat for name, pat, _ in base.__dict__.get("_sly_rules", ()) if name == key]
                if patterns:
                    inherited_rules[key] = patterns
                    break

        for key in rule_names:
            if key.startswith("_") or key in ("tokens", "ignore", "literals", "error"):
                continue
            val = getattr(cls, key, None)

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
                    pat = getattr(val, "pattern", None)
                    if not pat and (key in valid_tokens or key.startswith("ignore_")):
                        pat = getattr(val, "__doc__", None)
                    if pat:
                        rules.append((key, pat.strip(), val))
                        valid_tokens.add(key)
                    elif key in inherited_rules:
                        rules.extend((key, pat, val) for pat in inherited_rules[key])
                    elif key in valid_tokens:
                        rules.append((key, "", val))

            # String attribute rules, e.g. NUMBER = r'\d+'
            elif isinstance(val, str):
                rules.append((key, val, None))
                valid_tokens.add(key)

        cls._sly_rules = rules  # type: ignore
        cls._sly_rule_names = tuple(dict.fromkeys(name for name, _, _ in rules))  # type: ignore
        remap.update(attrs.remap)
        cls._remap = remap  # type: ignore
        cls.tokens = {name for name in valid_tokens if not name.startswith("ignore_")}  # type: ignore
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
                sly_tok = Token(tok.type, _DeferredTokenValue(tok), tok.lineno, tok.lexpos, tok.end)
                old_index = sly_tok.index
                res = error_handler(sly_tok)
                if sly_tok.index != old_index:
                    self._engine.pos = sly_tok.index
                return res
            self._engine.set_error_func("INITIAL", wrap_err)

        ignored_tokens = {name[7:] for name, _, _ in self._sly_rules if name.startswith("ignore_")}
        token_funcs = {
            name[7:] if name.startswith("ignore_") else name: func.__get__(self, self.__class__)
            for name, _, func in self._sly_rules if func is not None
        }

        def make_wrapper(tname):
            def wrapper(core_tok: CoreToken):
                core_tok.type = self._remap.get((tname, core_tok.value), tname)
                action = token_funcs.get(core_tok.type)
                if action is not None:
                    sly_tok = Token(core_tok.type, core_tok.value, core_tok.lineno, core_tok.lexpos, core_tok.end)
                    res = action(sly_tok)
                    if res is None:
                        return None
                    core_tok.type = getattr(res, "type", core_tok.type)
                    core_tok.value = getattr(res, "value", res)
                    core_tok.lineno = getattr(res, "lineno", core_tok.lineno)
                    core_tok.lexpos = getattr(res, "index", core_tok.lexpos)
                    core_tok.end = getattr(res, "end", core_tok.end)
                return None if core_tok.type in ignored_tokens else core_tok
            return wrapper

        for name, pat, func in self._sly_rules:
            token_name = name[7:] if name.startswith("ignore_") else name
            needs_action = func is not None or token_name in ignored_tokens or any(
                source == token_name for source, _ in self._remap
            )
            bound_func = make_wrapper(token_name) if needs_action else None

            if pat:
                self._engine.add_rule("INITIAL", token_name, pat, bound_func)

    def tokenize(self, text: str) -> Iterator[Token]:
        """Tokenize input string and yield SLY Token instances."""
        self._engine.input(text)
        while True:
            core_tok = self._engine.token()
            if core_tok is None:
                break
            # A returned error token already uses SLY's public index field and
            # may still hold a deferred suffix for the consumer to inspect.
            is_sly_token = type(core_tok) is Token
            yield Token(
                tok_type=core_tok.type,
                value=core_tok._TokenValueStorage__value if is_sly_token else core_tok.value,
                lineno=core_tok.lineno,
                index=core_tok.index if is_sly_token else core_tok.lexpos,
                end=core_tok.end,
            )
