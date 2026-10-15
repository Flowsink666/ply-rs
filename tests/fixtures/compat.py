"""Shared PLY/SLY lexer construction and lazy source observations."""

from ply_rs.compat.ply import lex as ply_lex
from ply_rs.compat.sly import Lexer as SlyLexer


class TrackedText(str):
    def __new__(cls, value):
        result = super().__new__(cls, value)
        result.slices = []
        result.on_slice = None
        return result

    def __getitem__(self, key):
        if isinstance(key, slice):
            self.slices.append(key)
            if self.on_slice is not None:
                self.on_slice()
        return super().__getitem__(key)


def make_lexer(adapter, recover):
    if adapter == "ply":
        return ply_lex.lex(module={
            "tokens": ("WORD",), "t_WORD": r"[a-z]+", "t_ignore": " ",
            "t_error": lambda tok: recover(tok, tok.lexer),
        })

    class Words(SlyLexer):
        tokens = {"WORD"}
        WORD = r"[a-z]+"
        ignore = " "

        def error(self, tok):
            return recover(tok, self)

    return Words()


def scan(adapter, lexer, source):
    if adapter == "ply":
        lexer.input(source)
        return list(lexer)
    return list(lexer.tokenize(source))


def token_position(adapter, tok):
    return tok.lexpos if adapter == "ply" else tok.index
