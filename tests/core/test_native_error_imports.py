"""Native scans resolve the current error class only when raising LexError."""

import importlib.util

import pytest

from tests.core.test_backends import run_python


def run_native(mode, source):
    if importlib.util.find_spec("rply._core_rs") is None:
        pytest.skip("native extension unavailable")
    result = run_python(mode, source)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", ["rust", "auto"])
def test_successful_native_scans_do_not_resolve_error_class(mode):
    run_native(mode, r'''
import builtins
from rply import LexerEngine

lexer = LexerEngine()
assert lexer.backend == 'rust'
lexer.add_rule('INITIAL', 'WORD', r'\w+')
lexer.set_literals('+')
lexer.set_ignore('INITIAL', ' \n')
original_import = builtins.__import__
imports = []
def tracked_import(name, *args, **kwargs):
    if name == 'rply.core.lexer_engine':
        imports.append(name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = tracked_import
try:
    assert [tok.value for tok in lexer.tokenize('abc + 中文\n')] == [
        'abc', '+', '中文',
    ]
    assert lexer.token() is None
    assert list(lexer.tokenize(' \n')) == []
finally:
    builtins.__import__ = original_import
assert imports == [], imports
''')


@pytest.mark.parametrize("mode", ["rust", "auto"])
def test_native_handlers_and_callback_failures_do_not_resolve_error_class(mode):
    run_native(mode, r'''
import builtins
from rply import LexerEngine

lexer = LexerEngine()
assert lexer.backend == 'rust'
handled = []
lexer.set_error_func('INITIAL', lambda tok: handled.append(tok.lexpos))
failure = RuntimeError('callback failure')
def fail(tok):
    raise failure
original_import = builtins.__import__
def blocked_import(name, *args, **kwargs):
    if name == 'rply.core.lexer_engine':
        raise AssertionError('handled errors must not import LexError')
    return original_import(name, *args, **kwargs)
builtins.__import__ = blocked_import
try:
    assert list(lexer.tokenize('?？')) == []
    assert handled == [0, 1]
    lexer.set_error_func('INITIAL', fail)
    lexer.input('?')
    try:
        lexer.token()
    except RuntimeError as error:
        assert error is failure
    else:
        raise AssertionError('error-handler failure was swallowed')
    lexer.add_rule('INITIAL', 'WORD', r'\w+', fail)
    lexer.input('word')
    try:
        lexer.token()
    except RuntimeError as error:
        assert error is failure
    else:
        raise AssertionError('rule-action failure was swallowed')
finally:
    builtins.__import__ = original_import
''')


@pytest.mark.parametrize("mode", ["rust", "auto"])
@pytest.mark.parametrize("empty_rule", [False, True])
def test_native_errors_use_the_current_monkeypatched_class(mode, empty_rule):
    run_native(mode, f"empty_rule = {empty_rule!r}\n" + r'''
import rply.core.lexer_engine as lexer_module
from rply import LexerEngine, LexError

lexer = LexerEngine()
assert lexer.backend == 'rust'
if empty_rule:
    lexer.add_rule('INITIAL', 'EMPTY', r'')
class FirstError(LexError):
    pass
class SecondError(LexError):
    pass
for expected in [FirstError, SecondError]:
    lexer_module.LexError = expected
    lexer.input('a？旧')
    lexer.pos = 1
    lexer.lineno = 7
    try:
        lexer.token()
    except expected as error:
        assert type(error) is expected
        assert error.text == '？旧' and error.error_index == 1
        if empty_rule:
            assert str(error) == 'Rule "EMPTY" matched an empty string at pos 1'
        else:
            assert str(error) == "Illegal character '？' at line 7, pos 1"
    else:
        raise AssertionError('expected LexError was not raised')
''')


@pytest.mark.parametrize("mode", ["rust", "auto"])
def test_discarded_action_can_change_error_class_during_the_same_scan(mode):
    run_native(mode, r'''
import rply.core.lexer_engine as lexer_module
from rply import LexerEngine, LexError

lexer = LexerEngine()
assert lexer.backend == 'rust'
class CurrentError(LexError):
    pass
def discard(tok):
    lexer_module.LexError = CurrentError
lexer.add_rule('INITIAL', 'WORD', r'\w+', discard)
lexer.input('word?')
try:
    lexer.token()
except CurrentError as error:
    assert error.text == '?' and error.error_index == 4
    assert str(error) == "Illegal character '?' at line 1, pos 4"
else:
    raise AssertionError('error-class change was not observed')
''')


@pytest.mark.parametrize("mode", ["rust", "auto"])
@pytest.mark.parametrize("empty_rule", [False, True])
@pytest.mark.parametrize("reentry", ["import", "lookup", "factory"])
def test_error_resolution_reentry_preserves_error_and_replacement_input(
    mode, empty_rule, reentry,
):
    run_native(mode, f"empty_rule = {empty_rule!r}\nreentry = {reentry!r}\n" + r'''
import builtins
import types
import rply.core.lexer_engine as lexer_module
from rply import LexerEngine, LexError

lexer = LexerEngine()
assert lexer.backend == 'rust'
if empty_rule:
    lexer.add_rule('INITIAL', 'EMPTY', r'')
lexer.input('a？旧')
lexer.pos = 1
lexer.lineno = 7
calls = []
def replace_input():
    calls.append(reentry)
    lexer.input('replacement')
class CurrentError(LexError):
    def __init__(self, *args):
        if reentry == 'factory':
            replace_input()
        super().__init__(*args)
lexer_module.LexError = CurrentError
original_import = builtins.__import__
original_module_class = lexer_module.__class__
class HookedModule(types.ModuleType):
    def __getattribute__(self, name):
        if name == 'LexError' and reentry == 'lookup':
            replace_input()
        return super().__getattribute__(name)
def hooked_import(name, *args, **kwargs):
    if name == 'rply.core.lexer_engine' and reentry == 'import':
        replace_input()
    return original_import(name, *args, **kwargs)
lexer_module.__class__ = HookedModule
builtins.__import__ = hooked_import
try:
    try:
        lexer.token()
    except CurrentError as error:
        assert error.text == '？旧' and error.error_index == 1
        if empty_rule:
            assert str(error) == 'Rule "EMPTY" matched an empty string at pos 1'
        else:
            assert str(error) == "Illegal character '？' at line 7, pos 1"
    else:
        raise AssertionError('expected LexError was not raised')
finally:
    builtins.__import__ = original_import
    lexer_module.__class__ = original_module_class
assert calls == [reentry], calls
assert (lexer.data, lexer.pos, lexer.lineno) == ('replacement', 0, 1)
''')
