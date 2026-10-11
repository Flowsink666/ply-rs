"""
SLY compatibility package.
"""

from rply.compat.sly.lex import Lexer, Token
from rply.compat.sly.yacc import Parser, _

__all__ = ["Lexer", "Token", "Parser", "_"]
