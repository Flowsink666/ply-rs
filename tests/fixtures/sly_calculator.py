"""Build one SLY specification against either compatibility implementation."""


def build_calculator(api):
    class SlyCalcLexer(api.Lexer):
        tokens = {NUMBER, PLUS, MINUS, TIMES, DIVIDE, LPAREN, RPAREN}
        ignore = " \t"

        PLUS = r"\+"
        MINUS = r"-"
        TIMES = r"\*"
        DIVIDE = r"/"
        LPAREN = r"\("
        RPAREN = r"\)"

        @_(r"\d+")
        def NUMBER(self, t):
            t.value = int(t.value)
            return t


    class SlyCalcParser(api.Parser):
        tokens = SlyCalcLexer.tokens

        precedence = (
            ("left", PLUS, MINUS),
            ("left", TIMES, DIVIDE),
        )

        @_("expr PLUS expr")
        def expr(self, p):
            return p.expr0 + p.expr1

        @_("expr MINUS expr")
        def expr(self, p):
            return p.expr0 - p.expr1

        @_("expr TIMES expr")
        def expr(self, p):
            return p.expr0 * p.expr1

        @_("expr DIVIDE expr")
        def expr(self, p):
            return p.expr0 // p.expr1

        @_("LPAREN expr RPAREN")
        def expr(self, p):
            return p.expr

        @_("NUMBER")
        def expr(self, p):
            return p.NUMBER

    return SlyCalcLexer, SlyCalcParser
