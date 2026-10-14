"""
PLY (Python Lex-Yacc) compatible yacc module.
Drop-in replacement for `import ply.yacc as yacc`.
"""

import builtins
import inspect
import sys
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from ply_rs.core.grammar import Grammar, GrammarError
from ply_rs.core.lr_table import LRTable
from ply_rs.core.lr_parser import LRParser, ProductionProxy
from ply_rs.compat.ply.lex import LexToken, Lexer as PlyLexer
from ply_rs.compat.ply._definitions import definition_namespace
from ply_rs.core.symbols import EOF_SYMBOL


class YaccError(Exception):
    """Exception raised by yacc."""
    pass


class YaccProduction:
    """Wrapper around ProductionProxy for PLY."""

    def __init__(self, proxy: ProductionProxy):
        self._proxy = proxy

    @property
    def lexer(self) -> Any:
        return self._proxy.lexer

    @property
    def parser(self) -> Any:
        return self._proxy.parser

    @property
    def slice(self) -> List[Any]:
        return self._proxy.slice

    def __getitem__(self, n: Union[int, builtins.slice]) -> Any:
        return self._proxy[n]

    def __setitem__(self, n: int, v: Any) -> None:
        self._proxy[n] = v

    def __len__(self) -> int:
        return len(self._proxy)

    def lineno(self, n: int) -> int:
        return self._proxy.lineno(n)

    def set_lineno(self, n: int, lineno: int) -> None:
        self._proxy.set_lineno(n, lineno)

    def linespan(self, n: int) -> Tuple[int, int]:
        return self._proxy.linespan(n)

    def lexpos(self, n: int) -> int:
        return self._proxy.lexpos(n)

    def set_lexpos(self, n: int, lexpos: int) -> None:
        self._proxy.set_lexpos(n, lexpos)

    def lexspan(self, n: int) -> Tuple[int, int]:
        return self._proxy.lexspan(n)

    def error(self) -> None:
        self._proxy.error()


class YaccParser:
    """PLY-compatible Parser object returned by yacc.yacc()."""

    def __init__(self, engine: LRParser, table: LRTable, default_lexer: Any = None):
        self._engine = engine
        self._table = table
        self._default_lexer = default_lexer

    def parse(
        self,
        input: Optional[str] = None,
        lexer: Optional[Any] = None,
        debug: bool = False,
        tracking: bool = False,
        tokenfunc: Optional[Callable[[], Any]] = None,
    ) -> Any:
        active_lexer = lexer or self._default_lexer

        if tokenfunc is not None:
            # Custom token provider function
            def token_gen():
                while True:
                    tok = tokenfunc()
                    if tok is None:
                        break
                    yield tok
            return self._engine.parse(token_gen(), lexer=active_lexer, tracking=tracking, parser=self,
                                      recovery_style="ply")

        if input is not None:
            if active_lexer is None:
                raise YaccError("Cannot parse string input without a lexer specified")
            active_lexer.input(input)

        if active_lexer is None:
            raise YaccError("No token source or input provided to parse()")

        return self._engine.parse(active_lexer, lexer=active_lexer, tracking=tracking, parser=self,
                                  recovery_style="ply")

    def errok(self):
        self._engine.errok()

    def restart(self):
        self._engine.restart()

    def token(self):
        return self._engine.token()


def _parse_ply_docstring(doc: str) -> List[Tuple[str, List[str]]]:
    """
    Parse a PLY grammar rule docstring into a list of (LHS, [RHS...]) rules.
    Supports multiline docstrings and '|' alternatives.
    Example:
        '''expr : expr PLUS expr
                | expr MINUS expr'''
    """
    rules: List[Tuple[str, List[str]]] = []
    lines = [line.strip() for line in doc.splitlines() if line.strip()]

    current_lhs: Optional[str] = None
    for line in lines:
        parts = line.split()
        if not parts:
            continue

        if len(parts) >= 2 and parts[1] in (":", "::="):
            current_lhs = parts[0]
            rhs = parts[2:]
            rules.append((current_lhs, rhs))
        elif parts[0] == "|" and current_lhs is not None:
            rhs = parts[1:]
            rules.append((current_lhs, rhs))
        elif ":" in line:
            lhs_part, rhs_part = line.split(":", 1)
            current_lhs = lhs_part.strip()
            rhs = rhs_part.strip().split()
            rules.append((current_lhs, rhs))
        else:
            raise GrammarError(f"Malformed grammar rule line: {line!r}")

    return rules


def yacc(
    method: str = "LALR",
    debug: bool = False,
    module: Any = None,
    tabmodule: str = "parsetab",
    start: Optional[str] = None,
    check_recursion: bool = True,
    optimize: bool = False,
    write_tables: bool = True,
    debugfile: str = "parser.out",
    errorlog: Any = None,
    **kwargs: Any,
) -> YaccParser:
    """
    Build a PLY-compatible parser from a module or globals.
    """
    if module is None:
        frame = sys._getframe(1)
        mod_dict = frame.f_globals
    else:
        mod_dict = definition_namespace(module)

    tokens = mod_dict.get("tokens", ())
    precedence = mod_dict.get("precedence", ())
    start_symbol = start or mod_dict.get("start", None)
    error_func = mod_dict.get("p_error", None)

    grammar = Grammar(terminals=set(tokens) if tokens else None)

    # Precedence definition
    # precedence = (('left', 'PLUS', 'MINUS'), ('left', 'TIMES', 'DIVIDE'), ...)
    if precedence:
        prec_specs: List[Tuple[str, List[str]]] = []
        for prec_entry in precedence:
            assoc = prec_entry[0]
            toks = list(prec_entry[1:])
            prec_specs.append((assoc, toks))
        grammar.set_precedence(prec_specs)

    # Collect p_* functions
    p_funcs: List[Tuple[str, Callable[..., Any], int]] = []
    for name, val in mod_dict.items():
        if name.startswith("p_") and name != "p_error" and callable(val):
            try:
                line = val.__code__.co_firstlineno
            except AttributeError:
                line = 0
            p_funcs.append((name, val, line))

    # Order functions by source line number
    p_funcs.sort(key=lambda item: item[2])

    for fname, func, fline in p_funcs:
        doc = func.__doc__
        if not doc:
            raise GrammarError(f"Function {fname!r} requires a grammar docstring")

        parsed_rules = _parse_ply_docstring(doc)
        filename = inspect.getsourcefile(func) or ""

        for lhs, rhs in parsed_rules:
            # Check for %prec
            rule_prec = None
            clean_rhs = list(rhs)
            if "%prec" in clean_rhs:
                idx = clean_rhs.index("%prec")
                if idx + 1 < len(clean_rhs):
                    rule_prec = clean_rhs[idx + 1]
                    del clean_rhs[idx : idx + 2]

            def make_action(target_func):
                def action(proxy: ProductionProxy):
                    yacc_prod = YaccProduction(proxy)
                    target_func(yacc_prod)
                    return yacc_prod[0]
                return action

            grammar.add_production(
                name=lhs,
                syms=clean_rhs,
                func=make_action(func),
                file=filename,
                line=fline,
                precedence=rule_prec,
            )

    grammar.set_start(start_symbol)
    table = LRTable(grammar)

    wrapped_err_func = None
    if error_func:
        def err_wrapper(tok: Any):
            if tok.type == EOF_SYMBOL:
                return error_func(None)
            ptok = LexToken()
            ptok.type = tok.type
            ptok.value = tok.value
            ptok.lineno = tok.lineno
            ptok.lexpos = tok.lexpos
            ptok.lexer = parser_object._engine._active_session()[2].lexer
            return error_func(ptok)
        wrapped_err_func = err_wrapper

    engine = LRParser(table, error_func=wrapped_err_func)
    parser_object = YaccParser(engine, table)
    return parser_object
