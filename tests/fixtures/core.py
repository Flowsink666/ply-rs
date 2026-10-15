"""Fresh single-production tables and parsers for core contract tests."""

from ply_rs import Grammar, LRParser, LRTable


def table_for(rhs, action=None, action_style="auto", *, start="s", terminals=()):
    grammar = Grammar(set(rhs) | set(terminals))
    grammar.add_production(start, rhs, func=action, action_style=action_style)
    grammar.set_start(start)
    return LRTable(grammar)


def parser_for(rhs, action=None, action_style="auto", *, start="s", terminals=(),
               frozen=False):
    # Always construct independent mutable objects; tests intentionally edit them.
    return LRParser(
        table_for(rhs, action, action_style, start=start, terminals=terminals),
        frozen=frozen,
    )
