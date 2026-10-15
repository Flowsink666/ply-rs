"""Bounded public-operation traces checked against independent small models.

The lexer language deliberately has only single-character rules. Its model
contains no regular expressions, runtime imports, or parse tables. The parser
model tracks public bindings and freeze transitions, not LR implementation.
"""

from dataclasses import dataclass, replace
from itertools import product

import pytest

from ply_rs import Grammar, LexerEngine, LexError, LRParser, LRTable, Token
from ply_rs.core.lexer_config import LexerSnapshot
from tests.fixtures.adversarial import (
    CallbackFailure, FinalizingText, RecordingAction, lexer_state,
)


LEXER_OPERATIONS = (
    "token", "skip", "back", "push", "pop", "capture", "restore",
    "unicode", "empty", "invalid_input", "invalid_push", "invalid_restore",
)
PARSER_OPERATIONS = (
    "proxy", "values", "none", "assign", "block", "unblock", "freeze",
)


def token_value(token):
    if token is None:
        return None
    return token.type, token.value, token.lineno, token.lexpos, token.end


def observed(operation):
    try:
        return "return", operation()
    except LexError as error:
        return "raise", LexError, error.error_index, error.text
    except (ValueError, TypeError, SyntaxError, CallbackFailure) as error:
        return "raise", type(error)


@dataclass
class CharacterModel:
    """Reference contract for two states and a one-character token language."""

    data: str = ""
    pos: int = 0
    line: int = 1
    state: str = "INITIAL"
    stack: tuple = ()
    revision: int = 0
    saved: tuple = ("", 0, 1, "INITIAL", ())

    def snapshot(self):
        return self.data, self.pos, self.line, self.state, self.stack

    def state_value(self):
        return self.snapshot() + (len(self.data), self.revision)

    def feed(self, text):
        self.data, self.pos, self.line, self.state, self.stack = (
            text, 0, 1, "INITIAL", (),
        )
        self.revision += 1

    def step(self, operation):
        if operation == "token":
            while self.pos < len(self.data) and self.data[self.pos] in " \n":
                self.line += self.data[self.pos] == "\n"
                self.pos += 1
            if self.pos >= len(self.data):
                return "return", None
            char = self.data[self.pos]
            if char not in "a中+":
                return "raise", LexError, self.pos, self.data[self.pos:]
            start = self.pos
            self.pos += 1
            kind = "+" if char == "+" else self.state
            return "return", (kind, char, self.line, start, self.pos)
        if operation in ("skip", "back"):
            offset = 1 if operation == "skip" else -1
            if self.pos + offset < 0:
                return "raise", ValueError
            self.pos += offset
        elif operation == "push":
            self.stack += (self.state,)
            self.state = "ALT"
        elif operation == "pop":
            if not self.stack:
                return "raise", ValueError
            self.state, self.stack = self.stack[-1], self.stack[:-1]
        elif operation == "capture":
            self.saved = self.snapshot()
            return "return", self.saved
        elif operation == "restore":
            self.data, self.pos, self.line, self.state, self.stack = self.saved
            self.revision += 1
        elif operation == "unicode":
            self.feed("中\na+")
        elif operation == "empty":
            self.feed("")
        elif operation == "invalid_input":
            return "raise", TypeError
        elif operation in ("invalid_push", "invalid_restore"):
            return "raise", ValueError
        else:
            raise AssertionError(operation)
        return "return", None


def make_character_lexer():
    lexer = LexerEngine()
    lexer.add_state("ALT", "exclusive")
    for state in ("INITIAL", "ALT"):
        lexer.add_rule(state, state, "[a中]")
        lexer.set_ignore(state, " \n")
    lexer.set_literals("+")
    return lexer


def run_lexer_step(lexer, operation, saved):
    def invoke():
        if operation == "token":
            return token_value(lexer.token())
        if operation == "skip":
            return lexer.skip()
        if operation == "back":
            return lexer.skip(-1)
        if operation == "push":
            return lexer.push_state("ALT")
        if operation == "pop":
            return lexer.pop_state()
        if operation == "capture":
            saved[0] = lexer.capture_state()
            snapshot = saved[0]
            return (
                snapshot.data, snapshot.pos, snapshot.lineno,
                snapshot.current_state, snapshot.state_stack,
            )
        if operation == "restore":
            return lexer.restore_state(saved[0])
        if operation == "unicode":
            return lexer.input("中\na+")
        if operation == "empty":
            return lexer.input("")
        if operation == "invalid_input":
            return lexer.input(1)
        if operation == "invalid_push":
            return lexer.push_state("UNDEFINED")
        if operation == "invalid_restore":
            return lexer.restore_state(replace(saved[0], current_state="UNDEFINED"))
        raise AssertionError(operation)
    return observed(invoke)


@pytest.mark.parametrize("initial", ["a \n中+", "?a", ""])
def test_generated_lexer_operation_traces(initial):
    # 3 * 12**3 = 5184 traces; pytest collects only three parameter groups.
    for trace in product(LEXER_OPERATIONS, repeat=3):
        lexer = make_character_lexer()
        saved = [lexer.capture_state()]
        model = CharacterModel()
        lexer.input(initial)
        model.feed(initial)
        for index, operation in enumerate(trace):
            context = initial, trace[:index + 1]
            assert run_lexer_step(lexer, operation, saved) == model.step(operation), context
            assert lexer_state(lexer) == model.state_value(), context


@pytest.mark.parametrize("field,value,error", [
    ("data", 1, TypeError), ("pos", -1, ValueError),
    ("lineno", -1, ValueError), ("state_stack", ("UNDEFINED",), ValueError),
])
def test_failed_restore_preserves_sequence_and_revision(field, value, error):
    # Validate failures after progress and after a successful restore as well.
    for trace in product(("token", "push", "capture", "restore"), repeat=2):
        lexer = make_character_lexer()
        lexer.input("a中+")
        saved = [LexerSnapshot("a中+", 0, 1, "INITIAL", ())]
        for operation in trace:
            run_lexer_step(lexer, operation, saved)
            before = lexer_state(lexer)
            invalid = replace(saved[0], **{field: value})
            assert observed(lambda: lexer.restore_state(invalid)) == ("raise", error)
            assert lexer_state(lexer) == before, (field, trace, operation)


@pytest.mark.parametrize("from_error", [False, True])
@pytest.mark.parametrize("mode,replacement", [
    # These modes do not replace input, so replacement is not another dimension.
    pytest.param("return", "", id="return"),
    pytest.param("ignore", "", id="ignore"),
    pytest.param("raise", "", id="raise"),
    pytest.param("replace_ignore", "a中", id="replace-ignore-unicode"),
    pytest.param("replace_ignore", "", id="replace-ignore-empty"),
    pytest.param("replace_return", "a中", id="replace-return-unicode"),
    pytest.param("replace_return", "", id="replace-return-empty"),
    pytest.param("recursive", "a中", id="recursive-unicode"),
    pytest.param("recursive", "", id="recursive-empty"),
])
def test_callback_sequences_observe_committed_progress(from_error, replacement, mode):
    lexer = make_character_lexer()
    action = RecordingAction(mode, replacement, from_error)
    prefix = "?" if from_error else "@"
    if from_error:
        lexer.set_error_func("INITIAL", action)
    else:
        lexer.add_rule("INITIAL", "MARK", "@", action)
    lexer.input(prefix + "a")
    original = ("error" if from_error else "MARK",
                prefix + "a" if from_error else prefix, 1, 0, 2 if from_error else 1)
    first = observed(lambda: token_value(lexer.token()))
    if mode == "raise":
        assert first == ("raise", CallbackFailure)
        assert lexer.pos == (0 if from_error else 1)
        action.mode = "ignore"
        assert token_value(lexer.token()) == ("INITIAL", "a", 1, 1, 2)
        expected_revision, expected_data = 1, prefix + "a"
    elif mode == "return":
        assert first == ("return", original)
        assert token_value(lexer.token()) == ("INITIAL", "a", 1, 1, 2)
        expected_revision, expected_data = 1, prefix + "a"
    elif mode == "ignore":
        assert first == ("return", ("INITIAL", "a", 1, 1, 2))
        expected_revision, expected_data = 1, prefix + "a"
    else:
        expected_revision, expected_data = 2, replacement
        if mode == "replace_return":
            assert first == ("return", original)
            remaining = [token_value(token) for token in lexer.tokenize()]
        else:
            assert first == ("return", ("INITIAL", "a", 1, 0, 1) if replacement else None)
            remaining = [token_value(token) for token in lexer.tokenize()]
            if replacement:
                remaining.insert(0, first[1])
        assert remaining == [("INITIAL", char, 1, index, index + 1)
                             for index, char in enumerate(replacement)]
    assert lexer.token() is None
    assert lexer_state(lexer) == (
        expected_data, len(expected_data), 1, "INITIAL", (),
        len(expected_data), expected_revision,
    )
    assert action.calls[0] == original + ((prefix + "a", 0 if from_error else 1,
                                          1, "INITIAL", (), 2, 1),)
    assert len(action.calls) == (2 if mode == "raise" and from_error else 1)


@pytest.mark.parametrize("transition", ["input", "restore"])
def test_input_finalizer_can_start_following_generated_operations(transition):
    for trace in product(("token", "push", "pop", "capture", "restore"), repeat=2):
        lexer = make_character_lexer()
        calls = []

        def released():
            calls.append(lexer_state(lexer))
            lexer.input("中a+")

        lexer.input(FinalizingText("old", released))
        saved = [LexerSnapshot("a中+", 1, 7, "ALT", ("INITIAL",))]
        if transition == "input":
            lexer.input("a中+")
            expected_commit = ("a中+", 0, 1, "INITIAL", (), 3, 2)
        else:
            lexer.restore_state(saved[0])
            expected_commit = ("a中+", 1, 7, "ALT", ("INITIAL",), 3, 2)
        assert calls == [expected_commit], (transition, trace)
        model = CharacterModel("中a+", revision=3)
        model.saved = ("a中+", 1, 7, "ALT", ("INITIAL",))
        assert lexer_state(lexer) == model.state_value()
        for index, operation in enumerate(trace):
            context = transition, trace[:index + 1]
            assert run_lexer_step(lexer, operation, saved) == model.step(operation), context
            assert lexer_state(lexer) == model.state_value(), context


def test_generated_parser_binding_and_freeze_traces():
    # 7**4 = 2401 traces. The expected value follows the binding contract only.
    for trace in product(PARSER_OPERATIONS, repeat=4):
        events = []

        def proxy_action(p):
            events.append(("proxy", p[1], p.parser is parser))
            return p[1] + 10

        def values_action(value):
            events.append(("values", value))
            return value + 20

        def assign_action(p):
            events.append(("assign", p[1], p.parser is parser))
            p[0] = p[1] + 30

        bindings = {
            "proxy": (proxy_action, "proxy", 17, [("proxy", 7, True)]),
            "values": (values_action, "values", 27, [("values", 7)]),
            "none": (None, "proxy", 7, []),
            "assign": (assign_action, "proxy", 37, [("assign", 7, True)]),
        }
        grammar = Grammar({"N"})
        grammar.add_production("s", ["N"], None, action_style="proxy")
        grammar.set_start("s")
        table = LRTable(grammar)
        parser = LRParser(table)
        shift = table.action[0]["N"]
        binding, blocked, frozen_binding = "none", False, None
        for index, operation in enumerate(trace):
            context = trace[:index + 1]
            if operation in bindings:
                binding = operation
                table.productions[1].func, table.productions[1].action_style = bindings[binding][:2]
            elif operation in ("block", "unblock"):
                blocked = operation == "block"
                table.action[0]["N"] = None if blocked else shift
            else:
                assert operation == "freeze"
                assert parser.freeze() is parser
                if frozen_binding is None:
                    frozen_binding = binding, blocked
            active_binding, active_block = frozen_binding or (binding, blocked)
            events.clear()
            actual = observed(lambda: parser.parse([Token("N", 7)]))
            expected = ("raise", SyntaxError) if active_block else (
                "return", bindings[active_binding][2],
            )
            assert actual == expected, context
            assert events == ([] if active_block else bindings[active_binding][3]), context
            assert parser.frozen == (frozen_binding is not None), context
