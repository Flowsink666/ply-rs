"""PLY calculator specification shared with reference comparisons."""

# --- PLY Lexer specification ---
tokens = (
    "NUMBER",
    "PLUS",
    "MINUS",
    "TIMES",
    "DIVIDE",
    "LPAREN",
    "RPAREN",
)

t_PLUS = r"\+"
t_MINUS = r"-"
t_TIMES = r"\*"
t_DIVIDE = r"/"
t_LPAREN = r"\("
t_RPAREN = r"\)"
t_ignore = " \t"


def t_NUMBER(t):
    r"\d+"
    t.value = int(t.value)
    return t


def t_error(t):
    t.lexer.skip(1)


# --- PLY Parser specification ---
precedence = (
    ("left", "PLUS", "MINUS"),
    ("left", "TIMES", "DIVIDE"),
    ("right", "UMINUS"),
)


def p_expr_binop(p):
    """expr : expr PLUS expr
            | expr MINUS expr
            | expr TIMES expr
            | expr DIVIDE expr"""
    if p[2] == "+":
        p[0] = p[1] + p[3]
    elif p[2] == "-":
        p[0] = p[1] - p[3]
    elif p[2] == "*":
        p[0] = p[1] * p[3]
    elif p[2] == "/":
        p[0] = p[1] // p[3]


def p_expr_uminus(p):
    "expr : MINUS expr %prec UMINUS"
    p[0] = -p[2]


def p_expr_group(p):
    "expr : LPAREN expr RPAREN"
    p[0] = p[2]


def p_expr_number(p):
    "expr : NUMBER"
    p[0] = p[1]


def p_error(p):
    raise SyntaxError(f"Syntax error in PLY parser at {p}")
