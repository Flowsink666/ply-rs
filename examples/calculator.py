"""Modern API calculator. Run after installing the project in a virtualenv."""

from rply import Lexer, Parser, rule, token


class CalculatorLexer(Lexer):
    ignore = " \t"
    literals = {"(", ")"}
    PLUS = r"\+"
    TIMES = r"\*"

    @token(r"\d+")
    def NUMBER(self, tok):
        tok.value = int(tok.value)
        return tok


class CalculatorParser(Parser):
    tokens = CalculatorLexer.tokens
    precedence = [("left", ["PLUS"]), ("left", ["TIMES"])]

    @rule("expr : expr PLUS expr")
    def add(self, p):
        return p[1] + p[3]

    @rule("expr : expr TIMES expr")
    def multiply(self, p):
        return p[1] * p[3]

    @rule("expr : '(' expr ')'")
    def group(self, p):
        return p[2]

    @rule("expr : NUMBER")
    def number(self, p):
        return p[1]


if __name__ == "__main__":
    lexer = CalculatorLexer()
    parser = CalculatorParser()
    print(parser.parse(lexer.tokenize("(2 + 3) * 4")))  # 20
