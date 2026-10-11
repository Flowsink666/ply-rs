"""
rply: Modern successor to PLY and SLY.
"""

from rply.core.grammar import Grammar, Production, GrammarError
from rply.core.symbols import GrammarSymbol, EOF_SYMBOL, ERROR_SYMBOL
from rply.core.lr_table import LRTable, ConflictRecord
from rply.core.lr_parser import LRParser, ProductionProxy, ParserError
from rply.core.lexer_engine import LexerEngine, Token, LexError
from rply.core.diagnostics import DiagnosticsFormatter

from rply.modern.lexer import Lexer, token
from rply.modern.parser import Parser, rule

from rply.compat import ply, sly

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
