"""Grammar and pinned PLY construction for recovery comparisons."""

from types import SimpleNamespace

from ply_rs import Grammar, Token


STATEMENT_RULES = (
    ("s", ("START", "stmts"), lambda p: p[2]),
    ("stmts", ("stmts", "stmt"), lambda p: p[1] + [p[2]]),
    ("stmts", ("stmt",), lambda p: [p[1]]),
    ("stmt", ("ID", "SEMI"), lambda p: ("id", p[1])),
    ("stmt", ("error", "SEMI"), lambda p: ("error", p[1])),
)


def _grammar(terminals, rules, start, precedence=()):
    grammar = Grammar(set(terminals))
    grammar.set_precedence(list(precedence))
    for name, rhs, action in rules:
        grammar.add_production(name, list(rhs), action)
    grammar.set_start(start)
    return grammar


def _ply_action(action):
    def reduce(proxy):
        # PLY exposes the original token as error.value; ply-rs exposes its value.
        for symbol in proxy.slice[1:]:
            if symbol.type == "error" and hasattr(symbol.value, "value"):
                symbol.value = symbol.value.value
        proxy[0] = action(proxy)

    return reduce


def _ply_parser(yacc, table, errors):
    parser = yacc.LRParser(
        table, lambda token: errors.append(token.value if token is not None else "$end")
    )
    # Default reductions can change when errors are detected. This comparison
    # checks the explicit lookahead contract common to both runtimes.
    parser.disable_defaulted_states()
    return parser


def _shared_table_oracle(yacc, table, errors):
    productions = [
        SimpleNamespace(
            name=production.name,
            len=production.len,
            callable=_ply_action(production.func) if production.func is not None else None,
        )
        for production in table.productions
    ]
    return _ply_parser(yacc, SimpleNamespace(
        lr_action=table.action, lr_goto=table.goto, lr_productions=productions
    ), errors)


def _independent_oracle(yacc, terminals, rules, start, errors, precedence=()):
    grammar = yacc.Grammar(list(terminals))
    for token, (associativity, level) in precedence:
        grammar.set_precedence(token, associativity, level)
    actions = {}
    for index, (name, rhs, action) in enumerate(rules):
        action_name = f"action_{index}"
        actions[action_name] = _ply_action(action)
        grammar.add_production(name, list(rhs), func=action_name)
    grammar.set_start(start)
    table = yacc.LRTable(grammar)
    table.bind_callables(actions)
    return _ply_parser(yacc, table, errors)


def _run_ply_rs(parser, entries):
    try:
        result = parser.parse([Token(kind, value) for kind, value in entries])
    except SyntaxError:
        return False, None
    return True, result


def _run_ply(parser, entries):
    source = iter(SimpleNamespace(type=kind, value=value, lineno=1, lexpos=index)
                  for index, (kind, value) in enumerate(entries))
    result = parser.parse(lexer=SimpleNamespace(token=lambda: next(source, None)))
    return result is not None, result
