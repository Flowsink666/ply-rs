"""Modern wrappers forward lexer state, freezing, and action ownership."""

from types import SimpleNamespace

import pytest

from ply_rs import Lexer, Parser, Token, token, rule


def test_modern_states_ignore_errors_and_snapshot_forwarding():
    class Words(Lexer):
        states = (("inside", "exclusive"),)
        ignore = " "
        ignore_inside = "\t"

        @token('"')
        def OPEN(self, tok):
            assert tok.lexer is self
            self.push_state("inside")

        @token('"', state="inside")
        def CLOSE(self, tok):
            self.pop_state()

        @token("[a-z]+", state="inside")
        def WORD(self, tok):
            return tok

        NUMBER = r"\d+"

        def error_inside(self, tok):
            self.errors.append(tok.lexpos)
            self.skip()

    lexer = Words()
    lexer.errors = []
    lexer.input('1 "ab?cd\t" 2')
    assert lexer.token().value == "1"
    assert lexer.token().value == "ab"
    assert lexer.current_state == "inside" and lexer.state_stack == ["INITIAL"]
    snapshot = lexer.capture_state()
    assert [t.value for t in lexer.tokenize()] == ["cd", "2"]
    lexer.restore_state(snapshot)
    assert [t.value for t in lexer.tokenize()] == ["cd", "2"]
    assert lexer.errors == [5, 5]
    assert lexer.current_state == "INITIAL" and lexer.state_stack == []
    assert "ignore_inside" not in Words.tokens


def test_modern_frozen_class_default_and_explicit_override():
    class Static(Parser):
        tokens = {"N"}
        frozen = True

        @rule("s : N")
        def value(self, p):
            return p[1]

    class Child(Static):
        pass

    parser = Child()
    editable = Child(frozen=False)
    assert parser.frozen and not editable.frozen
    parser.table.action[0]["N"] = None
    assert parser.parse([Token("N", 7)]) == 7
    assert editable.freeze().frozen


@pytest.mark.parametrize("frozen", [False, True])
def test_modern_actions_receive_public_owner_and_independent_recursive_context(frozen):
    class OwnerParser(Parser):
        tokens = {"N"}

        @rule("s : N")
        def value(self, p):
            assert p.parser is self
            if p[1] == 2:
                inner = self.parse([Token("N", 1)], lexer=SimpleNamespace(name="inner"))
                return p.lexer.name, inner, self.increment
            return p.lexer.name, self.increment

    parser = OwnerParser()
    parser.increment = 10
    if frozen:
        assert parser.freeze() is parser
    assert parser.frozen is frozen
    assert parser.parse([Token("N", 2)], lexer=SimpleNamespace(name="outer")) == (
        "outer", ("inner", 10), 10,
    )
    parser.increment = 20
    assert parser.parse([Token("N", 1)], lexer=SimpleNamespace(name="changed")) == ("changed", 20)
