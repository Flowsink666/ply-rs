"""Error recovery retains token behavior without eager suffix copies."""

import gc
import weakref

import pytest

from ply_rs import LexerEngine, Token


class TrackedText(str):
    def __new__(cls, value):
        result = super().__new__(cls, value)
        result.slices = []
        return result

    def __getitem__(self, key):
        if isinstance(key, slice):
            self.slices.append(key)
        return super().__getitem__(key)


def test_recovery_only_materializes_suffixes_on_value_access():
    lexer = LexerEngine()
    saved = []
    lexer.set_error_func("INITIAL", saved.append)
    source = TrackedText("?" * 4096)
    assert list(lexer.tokenize(source)) == []
    assert len(saved) == len(source)
    # Inspecting location metadata and saving tokens must not slice the source.
    assert [(tok.lexpos, tok.end) for tok in saved] == [
        (index, len(source)) for index in range(len(source))
    ]
    assert source.slices == []
    lexer.input("replacement")
    assert saved[17].value == "?" * (len(source) - 17)
    assert isinstance(saved[17].value, str)
    assert source.slices == [slice(17, None)]
    # The computed suffix is cached, and overwriting value does not slice.
    saved[18].value = "fixed"
    assert saved[18].value == "fixed"
    assert source.slices == [slice(17, None)]


@pytest.mark.parametrize("assigned", [None, "edited", ["edited"]])
@pytest.mark.parametrize("replace_input", [False, True])
def test_error_value_read_preserves_assignment_from_source_slice(assigned, replace_input):
    lexer = LexerEngine()
    saved = []
    slices = []
    lexer.set_error_func("INITIAL", saved.append)

    class ReentrantText(str):
        def __getitem__(self, key):
            if isinstance(key, slice):
                slices.append(key)
                saved[0].value = assigned
                if replace_input:
                    lexer.input("replacement")
            return super().__getitem__(key)

    source = ReentrantText("？旧")
    assert list(lexer.tokenize(source)) == []
    assert slices == []
    tok = saved[0]
    assert tok.value is assigned
    assert tok.value is assigned
    assert slices == [slice(0, None)]
    assert (tok.lexpos, tok.end) == (0, 2)
    if replace_input:
        assert (lexer.data, lexer.length, lexer.pos) == ("replacement", 11, 0)
    else:
        assert (lexer.data, lexer.length, lexer.pos) == (source, 2, 2)


@pytest.mark.parametrize("replace_input", [False, True])
def test_source_slice_failure_propagates_and_preserves_assigned_error_value(replace_input):
    lexer = LexerEngine()
    saved = []
    slices = []
    assigned = ["edited"]
    failure = RuntimeError("source slice failed after assigning value")
    lexer.set_error_func("INITIAL", saved.append)

    class ReentrantText(str):
        def __getitem__(self, key):
            if isinstance(key, slice):
                slices.append(key)
                saved[0].value = assigned
                if replace_input:
                    lexer.input("replacement")
                raise failure
            return super().__getitem__(key)

    assert list(lexer.tokenize(ReentrantText("？旧"))) == []
    tok = saved[0]
    with pytest.raises(RuntimeError) as caught:
        _ = tok.value
    assert caught.value is failure
    assert tok.value is assigned
    assert tok.value is assigned
    assert slices == [slice(0, None)]
    assert (tok.lexpos, tok.end) == (0, 2)
    if replace_input:
        assert (lexer.data, lexer.length, lexer.pos) == ("replacement", 11, 0)


def test_error_value_read_keeps_original_suffix_when_source_slice_replaces_input():
    lexer = LexerEngine()
    saved = []
    slices = []
    lexer.set_error_func("INITIAL", saved.append)

    class ReentrantText(str):
        def __getitem__(self, key):
            if isinstance(key, slice):
                slices.append(key)
                lexer.input("replacement")
            return super().__getitem__(key)

    assert list(lexer.tokenize(ReentrantText("？旧"))) == []
    tok = saved[0]
    assert tok.value == "？旧"
    assert tok.value == "？旧"
    assert slices == [slice(0, None)]
    assert (tok.lexpos, tok.end) == (0, 2)
    assert (lexer.data, lexer.length, lexer.pos) == ("replacement", 11, 0)


@pytest.mark.parametrize("reassign", [False, True])
def test_error_value_assignment_commits_before_original_source_finalizer(reassign):
    lexer = LexerEngine()
    saved = []
    observed = []
    lexer.set_error_func("INITIAL", saved.append)

    class FinalizingText(str):
        def __del__(self):
            observed.append(saved[0].value)
            if reassign:
                saved[0].value = "finalizer assignment"

    lexer.input(FinalizingText("?"))
    assert lexer.token() is None
    lexer.input("replacement")
    assert observed == []
    saved[0].value = "assigned"
    assert observed == ["assigned"]
    assert saved[0].value == ("finalizer assignment" if reassign else "assigned")
    assert (saved[0].lexpos, saved[0].end) == (0, 1)
    assert (lexer.data, lexer.length, lexer.pos) == ("replacement", 11, 0)


@pytest.mark.parametrize("replace_input", [False, True])
@pytest.mark.parametrize("text", ["?old", "？旧文"])
def test_saved_error_value_survives_input_or_snapshot_changes(replace_input, text):
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "WORD", r"\w+")
    replacement = LexerEngine()
    replacement.input("新文本")
    saved = []

    def recover(tok):
        saved.append(tok)
        if replace_input:
            tok.lexer.input("新文本")
        else:
            tok.lexer.restore_state(replacement.capture_state())

    lexer.set_error_func("INITIAL", recover)
    assert [tok.value for tok in lexer.tokenize(text)] == ["新文本"]
    assert len(saved) == 1
    assert saved[0].value == text
    assert saved[0].end == len(text)
    assert saved[0].lexer is lexer
    assert saved[0] == Token("error", text)


def test_error_value_assignment_does_not_change_location_metadata():
    lexer = LexerEngine()
    lexer.set_error_func("INITIAL", lambda tok: tok)
    lexer.input("中文？")
    lexer.pos = 2
    tok = lexer.token()
    assert tok.value == "？"
    assert repr(tok) == "Token(type='error', value='？', lineno=1, pos=2)"
    tok.value = None
    assert tok.value is None
    tok.value = "fixed"
    tok.index = 99
    assert tok.value == "fixed"
    assert (tok.lexpos, tok.end) == (99, 3)


def test_changing_error_position_keeps_the_original_unread_suffix():
    lexer = LexerEngine()
    lexer.set_error_func("INITIAL", lambda tok: tok)
    lexer.input("中文？旧")
    lexer.pos = 2
    tok = lexer.token()
    tok.index = 0
    tok.end = 99
    lexer.input("new")
    assert tok.value == "？旧"
    assert (tok.lexpos, tok.end) == (0, 99)


def test_original_input_reference_participates_in_garbage_collection():
    lexer = LexerEngine()
    source = TrackedText("?")
    source.owner = lexer
    lexer.input(source)
    reference = weakref.ref(lexer)
    del lexer, source
    gc.collect()
    assert reference() is None


def test_unread_error_token_survives_callback_rule_registration():
    lexer = LexerEngine()
    saved = []

    def recover(tok):
        saved.append(tok)
        tok.lexer.add_rule("INITIAL", "WORD", r"(?=a)\w+")
        tok.lexer.input("abc")

    lexer.set_error_func("INITIAL", recover)
    assert [tok.value for tok in lexer.tokenize("?旧输入")] == ["abc"]
    assert len(saved) == 1 and saved[0].value == "?旧输入"


@pytest.mark.parametrize("from_error", [False, True])
def test_false_valued_callable_is_still_executed(from_error):
    calls = []

    class Action:
        def __bool__(self):
            raise AssertionError("callable truthiness must not be inspected")

        def __call__(self, tok):
            calls.append(tok.value)
            tok.value = "handled"
            if from_error:
                tok.lexer.skip()
            return tok

    lexer = LexerEngine()
    if from_error:
        lexer.set_error_func("INITIAL", Action())
    else:
        lexer.add_rule("INITIAL", "MARK", r"\?", Action())
    assert [tok.value for tok in lexer.tokenize("?")] == ["handled"]
    assert calls == ["?"]


def test_error_token_reuses_input_length_without_calling_subclass_len():
    lexer = LexerEngine()
    calls = []

    class ReentrantText(str):
        armed = False

        def __len__(self):
            if self.armed:
                calls.append("len")
                lexer.input("replacement")
            return super().__len__()

    source = ReentrantText("？旧")
    lexer.set_error_func("INITIAL", lambda tok: tok)
    lexer.input(source)
    source.armed = True
    tok = lexer.token()
    assert calls == []
    assert tok.value == "？旧"
    assert (tok.lexpos, tok.end) == (0, 2)
    assert (lexer.data, lexer.length, lexer.pos) == ("？旧", 2, 0)


@pytest.mark.parametrize("text", ["word", "中文"])
def test_input_counts_string_content_without_subclass_length_hooks(text):
    class HookedText(str):
        def __len__(self):
            raise RuntimeError("input must not execute a length hook")

    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "WORD", r"\w+")
    lexer.input(HookedText(text))
    assert lexer.length == len(text)
    tok = lexer.token()
    assert (tok.value, tok.lexpos, tok.end) == (text, 0, len(text))
    assert lexer.token() is None


def test_releasing_input_allows_reentrant_input_destructor():
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "WORD", r"[a-z]+")
    calls = []

    class ReentrantText(str):
        def __del__(self):
            calls.append("del")
            lexer.input("replacement")

    lexer.input(ReentrantText("？旧"))
    lexer.input("?outer")
    assert calls == ["del"]
    assert (lexer.data, lexer.length, lexer.pos) == ("replacement", 11, 0)
    assert [tok.value for tok in lexer.tokenize()] == ["replacement"]


def test_restoring_state_finishes_before_source_destructor_reentry():
    lexer = LexerEngine()
    lexer.add_state("inside", "exclusive")
    lexer.add_rule("INITIAL", "WORD", r"[a-z]+")
    donor = LexerEngine()
    donor.add_state("inside", "exclusive")
    donor.input("ABC")
    donor.pos = 1
    donor.lineno = 7
    donor.begin("inside")
    donor.state_stack = ["INITIAL"]
    state = donor.capture_state()
    calls = []

    class ReentrantText(str):
        def __del__(self):
            calls.append("del")
            lexer.input("replacement")

    lexer.input(ReentrantText("？旧"))
    lexer.restore_state(state)
    assert calls == ["del"]
    assert (lexer.data, lexer.length, lexer.pos, lexer.lineno) == (
        "replacement", 11, 0, 1,
    )
    assert lexer.current_state == "INITIAL"
    assert lexer.state_stack == []
    assert [tok.value for tok in lexer.tokenize()] == ["replacement"]
