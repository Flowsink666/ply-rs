"""
PLY (Python Lex-Yacc) compatible lexer module.
Drop-in replacement for `import ply.lex as lex`.
"""

import inspect
import sys
import types
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from rply.core.lexer_engine import LexerEngine, Token as CoreToken, LexError
from rply.compat._tokens import (
    _DeferredTokenValue, _TokenValueStorage, _public_token_state, _value_property,
)
from rply.compat.ply._definitions import definition_namespace


class _RuleCallback:
    """Keep built rule metadata separate from mutable action definitions."""

    def __init__(self, function, pattern, code, name):
        self.function = function
        self.__doc__ = pattern
        self.__code__ = code
        self.__name__ = name

    @classmethod
    def freeze(cls, function):
        if isinstance(function, cls):
            return function
        return cls(
            function, getattr(function, "__doc__", None),
            getattr(function, "__code__", None), getattr(function, "__name__", ""),
        )

    def rebind(self, object):
        return type(self)(
            getattr(object, self.__name__), self.__doc__, self.__code__, self.__name__,
        )

    def __call__(self, tok):
        return self.function(tok)


class LexToken(_TokenValueStorage):
    """Representation of a PLY LexToken."""

    __slots__ = ("type", "lineno", "lexpos", "lexer")

    value = _value_property

    def __init__(self):
        self.type: str = ""
        self.value: Any = None
        self.lineno: int = 1
        self.lexpos: int = 0
        self.lexer: Optional[Any] = None

    def __str__(self) -> str:
        return f"LexToken({self.type},{self.value!r},{self.lineno},{self.lexpos})"

    def __repr__(self) -> str:
        return str(self)

    def __getstate__(self):
        return _public_token_state(self, ("type", "value", "lineno", "lexpos", "lexer"))


class Lexer:
    """PLY-compatible Lexer object."""

    def __init__(self, engine: LexerEngine):
        self._engine = engine
        self._lex_spec = {}
        self._reflags = 0

    @property
    def lineno(self) -> int:
        return self._engine.lineno

    @lineno.setter
    def lineno(self, val: int) -> None:
        self._engine.lineno = val

    @property
    def lexpos(self) -> int:
        return self._engine.pos

    @lexpos.setter
    def lexpos(self, val: int) -> None:
        self._engine.pos = val

    @property
    def lexdata(self) -> str:
        return self._engine.data

    @property
    def lexlen(self) -> int:
        return self._engine.length

    def input(self, s: str) -> None:
        self._engine.input(s)

    def begin(self, state: str) -> None:
        self._engine.begin(state)

    def push_state(self, state: str) -> None:
        self._engine.push_state(state)

    def pop_state(self) -> None:
        self._engine.pop_state()

    def current_state(self) -> str:
        return self._engine.current_state

    def skip(self, n: int = 1) -> None:
        self._engine.skip(n)

    def token(self) -> Optional[LexToken]:
        core_tok = self._engine.token()
        if core_tok is None:
            return None

        # Convert CoreToken to LexToken
        tok = LexToken()
        tok.type = core_tok.type
        # Error handlers may return the compatibility token itself. Copy its
        # storage without forcing an unread suffix during outward conversion.
        tok._TokenValueStorage__value = (
            core_tok._TokenValueStorage__value if type(core_tok) is LexToken else core_tok.value
        )
        tok.lineno = core_tok.lineno
        tok.lexpos = core_tok.lexpos
        tok.lexer = self
        return tok

    def __iter__(self):
        while True:
            t = self.token()
            if t is None:
                break
            yield t

    def clone(self, object: Any = None) -> "Lexer":
        definitions = dict(self._lex_spec)
        if object is not None:
            for name, value in definitions.items():
                if name.startswith("t_") and callable(value):
                    definitions[name] = value.rebind(object)
        # Re-register callbacks so their lexer owner is the new wrapper.
        cloned = lex(module=definitions, reflags=self._reflags)
        cloned._engine.restore_state(self._engine.capture_state())
        return cloned


def lex(
    module: Any = None,
    object: Any = None,
    debug: bool = False,
    optimize: bool = False,
    lextab: str = "lextab",
    reflags: int = 0,
    nowarn: bool = False,
    **kwargs: Any,
) -> Lexer:
    """
    Build a PLY-compatible lexer from a module or object definitions.
    """
    if module is None and object is None:
        # Inspect caller's frame
        frame = sys._getframe(1)
        mod_dict = frame.f_globals
    elif module is not None:
        mod_dict = definition_namespace(module)
    else:
        mod_dict = definition_namespace(object)

    tokens = tuple(mod_dict.get("tokens", ()))
    literals = tuple(mod_dict.get("literals", ""))
    states = tuple(tuple(spec) for spec in mod_dict.get("states", ()))

    engine = LexerEngine()
    ply_lexer = Lexer(engine)
    ply_lexer._lex_spec = {
        name: _RuleCallback.freeze(value) if callable(value) else value
        for name, value in mod_dict.items()
        if name.startswith("t_") or name in {"tokens", "literals", "states"}
    }
    ply_lexer._lex_spec["tokens"] = tokens
    ply_lexer._lex_spec["literals"] = literals
    ply_lexer._lex_spec["states"] = states
    ply_lexer._reflags = reflags
    if literals:
        engine.set_literals(literals)

    # Register states
    # states = (('name', 'inclusive'|'exclusive'), ...)
    for state_spec in states:
        sname, skind = state_spec[0], state_spec[1]
        engine.add_state(sname, skind)

    # Separate functions and strings
    # In PLY: Functions are ordered by source line number.
    # Strings are ordered by descending length of regex pattern.
    func_rules: List[Tuple[str, str, Callable[..., Any], int]] = []
    str_rules: List[Tuple[str, str, str]] = []

    state_names = {"INITIAL": "inclusive"}
    for sspec in states:
        state_names[sspec[0]] = sspec[1]

    def _statetoken(s: str) -> Tuple[Tuple[str, ...], str]:
        parts = s.split("_")
        i = 1
        for i, part in enumerate(parts[1:], 1):
            if part not in state_names and part != "ANY":
                break
        if i > 1:
            st = tuple(parts[1:i])
        else:
            st = ("INITIAL",)
        if "ANY" in st:
            st = tuple(state_names.keys())
        tok_name = "_".join(parts[i:])
        return st, tok_name

    for name, val in mod_dict.items():
        if not name.startswith("t_"):
            continue

        target_states, rule_name = _statetoken(name)

        if rule_name == "ignore":
            if isinstance(val, str):
                for st in target_states:
                    engine.set_ignore(st, val)
            continue

        if rule_name == "error":
            if callable(val):
                def make_err_func(fn):
                    def err_handler(core_tok: CoreToken):
                        ptok = LexToken()
                        ptok.type = "error"
                        ptok._TokenValueStorage__value = _DeferredTokenValue(core_tok)
                        ptok.lineno = core_tok.lineno
                        ptok.lexpos = core_tok.lexpos
                        ptok.lexer = ply_lexer
                        return fn(ptok)
                    return err_handler
                for st in target_states:
                    engine.set_error_func(st, make_err_func(val))
            continue

        if callable(val):
            # Function rule
            try:
                line = val.__code__.co_firstlineno
            except AttributeError:
                line = 0
            doc = val.__doc__
            if doc:
                for st in target_states:
                    func_rules.append((st, rule_name, val, line))
        elif isinstance(val, str):
            # String regex rule
            for st in target_states:
                str_rules.append((st, rule_name, val))

    # Sort function rules by line number
    func_rules.sort(key=lambda r: r[3])
    # Sort string rules by descending pattern length
    str_rules.sort(key=lambda r: len(r[2]), reverse=True)

    # Register function rules
    for state, rname, fn, _ in func_rules:
        doc_pattern = fn.__doc__.strip()
        def make_wrapper(orig_fn, tok_name):
            def wrapper(core_tok: CoreToken):
                ptok = LexToken()
                ptok.type = tok_name
                ptok.value = core_tok.value
                ptok.lineno = core_tok.lineno
                ptok.lexpos = core_tok.lexpos
                ptok.lexer = ply_lexer
                res = orig_fn(ptok)
                if res is None:
                    return None
                # Write back modifications
                core_tok.type = res.type
                core_tok.value = res.value
                core_tok.lineno = res.lineno
                core_tok.lexpos = res.lexpos
                return core_tok
            return wrapper

        engine.add_rule(state, rname, doc_pattern, make_wrapper(fn, rname), flags=reflags)

    # Register string rules
    for state, rname, pat in str_rules:
        engine.add_rule(state, rname, pat, None, flags=reflags)

    return ply_lexer
