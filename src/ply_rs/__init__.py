"""
ply-rs: Modern successor to PLY and SLY.
"""

from ply_rs.core.grammar import Grammar, Production, GrammarError
from ply_rs.core.symbols import GrammarSymbol, EOF_SYMBOL, ERROR_SYMBOL
from ply_rs.core.lr_table import LRTable, ConflictRecord
from ply_rs.core.lr_parser import LRParser, ProductionProxy, ParserError
from ply_rs.core.lexer_engine import LexerEngine, Token, LexError
from ply_rs.core.diagnostics import DiagnosticsFormatter

from ply_rs.modern.lexer import Lexer, token
from ply_rs.modern.parser import Parser, rule

from ply_rs.compat import ply, sly

__version__ = "0.1.0"

__all__ = [
    # Modern API
    "Lexer",
    "Parser",
    "token",
    "rule",
    # Core types
    "Grammar",
    "Production",
    "GrammarSymbol",
    "EOF_SYMBOL",
    "ERROR_SYMBOL",
    "LRTable",
    "ConflictRecord",
    "LRParser",
    "ProductionProxy",
    "LexerEngine",
    "Token",
    "DiagnosticsFormatter",
    # Exceptions
    "GrammarError",
    "ParserError",
    "LexError",
    # Compatibility packages
    "ply",
    "sly",
    "__version__",
]
