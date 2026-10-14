"""
Core parsing and lexing infrastructure.
"""

from ply_rs.core.symbols import GrammarSymbol, EOF_SYMBOL, ERROR_SYMBOL, START_SYMBOL
from ply_rs.core.grammar import Grammar, Production, LRItem, GrammarError
from ply_rs.core.lr_table import LRTable, ConflictRecord
from ply_rs.core.lr_parser import LRParser, ProductionProxy, ParsedSymbol, ParserError
from ply_rs.core.lexer_engine import LexerEngine, Token, LexRule, LexError
from ply_rs.core.diagnostics import DiagnosticsFormatter

__all__ = [
    "GrammarSymbol",
    "EOF_SYMBOL",
    "ERROR_SYMBOL",
    "START_SYMBOL",
    "Grammar",
    "Production",
    "LRItem",
    "GrammarError",
    "LRTable",
    "ConflictRecord",
    "LRParser",
    "ProductionProxy",
    "ParsedSymbol",
    "ParserError",
    "LexerEngine",
    "Token",
    "LexRule",
    "LexError",
    "DiagnosticsFormatter",
]
