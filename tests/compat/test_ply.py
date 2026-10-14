"""PLY compatibility tests."""

from rply.compat.ply import lex, yacc
from tests.fixtures import ply_calculator as spec
import pytest


def test_ply_compat_calculator():
    lexer = lex.lex(module=spec)
    parser = yacc.yacc(module=spec)

    assert parser.parse("3 + 4 * 10", lexer=lexer) == 43
    assert parser.parse("(3 + 4) * 10", lexer=lexer) == 70
    assert parser.parse("-5 + 10", lexer=lexer) == 5
    assert parser.parse("100 / 10 / 2", lexer=lexer) == 5


def test_ply_lexer_iteration():
    lexer = lex.lex(module=spec)
    lexer.input("123 + 456")
    toks = list(lexer)
    assert len(toks) == 3
    assert toks[0].type == "NUMBER" and toks[0].value == 123
    assert toks[1].type == "PLUS"
    assert toks[2].type == "NUMBER" and toks[2].value == 456


def test_ply_callback_owns_public_lexer():
    owners = []

    def t_NUMBER(tok):
        r"\d+"
        owners.append(tok.lexer)
        if tok.lexpos == 0:
            tok.lexer.skip(1)
        return tok

    lexer = lex.lex(module={"tokens": ("NUMBER",), "t_NUMBER": t_NUMBER})
    lexer.input("12?34")
    assert [t.value for t in lexer] == ["12", "34"]
    assert owners == [lexer, lexer]


def test_ply_embedded_action_reads_previous_stack_value():
    def p_result(p):
        "result : NUMBER seen"
        p[0] = p[2]

    def p_seen(p):
        "seen :"
        p[0] = p[-1]

    lexer = lex.lex(module={"tokens": ("NUMBER",), "t_NUMBER": r"\d+"})
    parser = yacc.yacc(module={
        "tokens": ("NUMBER",), "p_result": p_result, "p_seen": p_seen,
    })
    assert parser.parse("42", lexer=lexer) == "42"


@pytest.mark.parametrize("argument", ["object", "module"])
def test_ply_instance_lexer_definitions_include_bound_methods(argument):
    class Spec:
        tokens = ("N",)
        offset = 10

        def t_N(self, tok):
            r"\d+"
            tok.value = int(tok.value) + self.offset
            return tok

    lexer = lex.lex(**{argument: Spec()})
    lexer.input("12")
    assert lexer.token().value == 22


def test_ply_instance_parser_actions_receive_public_context():
    class Spec:
        tokens = ("N",)

        def p_expr(self, p):
            "expr : N"
            assert p.parser is parser
            p[0] = (p.lexer.lexdata, p.parser.offset, p[1])

    lexer = lex.lex(module={"tokens": ("N",), "t_N": r"\d+"})
    parser = yacc.yacc(module=Spec())
    parser.offset = 7
    assert parser.parse("12", lexer=lexer) == ("12", 7, "12")


def test_ply_clone_preserves_position_and_isolates_callback_owner_and_state():
    owners = []

    def t_N(tok):
        r"\d+"
        owners.append(tok.lexer)
        return tok

    lexer = lex.lex(module={
        "tokens": ("N",), "t_N": t_N, "t_ignore": " ",
        "t_other_ignore": " ",
        "states": (("other", "inclusive"),),
    })
    lexer.input("1 2")
    assert lexer.token().value == "1"
    lexer.lineno = 9
    lexer.push_state("other")
    clone = lexer.clone()
    assert (clone.lexpos, clone.lineno, clone.current_state()) == (1, 9, "other")
    clone.pop_state()
    assert clone.current_state() == "INITIAL"
    assert lexer.current_state() == "other"
    assert clone.token().value == "2"
    assert owners[-1] is clone
    assert lexer.token().value == "2"
    assert owners[-1] is lexer
    clone.input("99")
    assert lexer.token() is None
    assert clone.token().value == "99"


def test_ply_clone_rebinds_object_actions():
    class Spec:
        tokens = ("N",)

        def __init__(self, offset):
            self.offset = offset

        def t_N(self, tok):
            r"\d+"
            tok.value = int(tok.value) + self.offset
            return tok

    lexer = lex.lex(object=Spec(10))
    lexer.input("1")
    clone = lexer.clone(object=Spec(20))
    assert clone.token().value == 21
    assert lexer.token().value == 11


def test_ply_eof_error_receives_none():
    errors = []

    def p_expr(p):
        "expr : N N"
        p[0] = p[1]

    def p_error(tok):
        errors.append(tok)
        return "eof" if tok is None else "token"

    lexer = lex.lex(module={"tokens": ("N",), "t_N": r"\d+"})
    parser = yacc.yacc(module={"tokens": ("N",), "p_expr": p_expr, "p_error": p_error})
    assert parser.parse("12", lexer=lexer) == "eof"
    assert errors == [None]


def test_ply_clone_keeps_built_patterns_after_action_metadata_changes():
    def number(tok):
        r"\d+"
        return tok

    lexer = lex.lex(module={"tokens": ("N",), "t_N": number})
    lexer.input("12")
    number.__doc__ = "[a-z]+"
    clone = lexer.clone()
    assert clone.token().value == "12"
    assert lexer.token().value == "12"


def test_ply_clone_rebinding_keeps_patterns_and_original_rule_order():
    class Spec:
        tokens = ("WORD", "LONG")

        def t_WORD(self, tok):
            r"a"
            return tok

        def t_LONG(self, tok):
            r"ab"
            return tok

    class Other:
        # Replacement actions do not need patterns, and their order is different.
        def t_LONG(self, tok):
            tok.value = "long"
            return tok

        def t_WORD(self, tok):
            tok.value = "word"
            return tok

    lexer = lex.lex(object=Spec())
    lexer.input("ab")
    clone = lexer.clone(object=Other())
    assert (clone.token().type, clone.lexpos) == ("WORD", 1)
    clone.input("ab")
    assert clone.token().value == "word"
    # A clone of a clone keeps the same frozen patterns without wrapper chains.
    clone.input("ab")
    assert clone.clone().token().value == "word"


def test_ply_clone_keeps_built_literals_after_definition_list_changes():
    literals = ["+"]
    lexer = lex.lex(module={"tokens": ("N",), "t_N": "[0-9]+", "literals": literals})
    lexer.input("+")
    literals[:] = ["-"]
    assert lexer.clone().token().type == "+"


def test_ply_clone_preserves_states_defined_by_an_iterator():
    lexer = lex.lex(module={
        "tokens": ("N",), "states": iter([("extra", "exclusive")]),
        "t_extra_N": r"\d+",
    })
    lexer.input("12")
    lexer.begin("extra")
    assert lexer.clone().token().value == "12"
    assert lexer.token().value == "12"


def test_ply_multi_underscore_and_state_rules():
    def t_string_content(tok):
        r"[^\"]+"
        tok.type = "STR"
        return tok

    def t_begin_string(tok):
        r"\""
        tok.lexer.begin("string")

    def t_string_end(tok):
        r"\""
        tok.lexer.begin("INITIAL")

    lexer = lex.lex(module={
        "tokens": ("STR", "NUM"),
        "states": (("string", "exclusive"),),
        "t_NUM": r"\d+",
        "t_ignore": " ",
        "t_begin_string": t_begin_string,
        "t_string_content": t_string_content,
        "t_string_end": t_string_end,
    })
    lexer.input('123 "hello" 456')
    toks = [(t.type, t.value) for t in lexer]
    assert toks == [("NUM", "123"), ("STR", "hello"), ("NUM", "456")]
