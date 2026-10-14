"""
SLY compatibility package.
"""

from ply_rs.compat.sly.lex import Lexer, Token
from ply_rs.compat.sly.yacc import Parser, _

__all__ = ["Lexer", "Token", "Parser", "_"]
