"""Verify an installed wheel in an isolated interpreter outside the checkout.

Only the standard library is needed. Run ``python -I verify_wheel.py --all``
from a temporary directory using a fresh virtual environment containing the
wheel. Each backend runs in its own subprocess before importing rply.
"""

import argparse
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import warnings
import weakref


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def verify_origin(module):
    prefix = Path(sys.prefix).resolve()
    origin = Path(module.__file__).resolve()
    require(sys.prefix != sys.base_prefix, "verification must run in a fresh virtual environment")
    require(prefix in origin.parents, f"rply came from {origin}, outside the virtual environment")
    require(sys.flags.isolated, "invoke verification with python -I")
    require("PYTHONPATH" not in os.environ, "clear PYTHONPATH before verifying the wheel")
    checkout = os.environ.get("RPLY_CHECKOUT")
    if checkout:
        root = Path(checkout).resolve()
        source = root / "src"
        require(source != origin and source not in origin.parents, f"rply came from checkout/src: {origin}")
        cwd = Path.cwd().resolve()
        require(root != cwd and root not in cwd.parents, "run verification outside the checkout")
    return str(origin)


def verify_loaded_origins(modules=None, installed_roots=None):
    """Reject mixed source/wheel imports, including a redirected extension."""
    if modules is None:
        modules = sys.modules
    if installed_roots is None:
        installed_roots = [sysconfig.get_path("purelib"), sysconfig.get_path("platlib")]
    roots = {Path(path).resolve() for path in installed_roots}
    checkout = os.environ.get("RPLY_CHECKOUT")
    source = Path(checkout).resolve() / "src" if checkout else None
    for name, module in modules.items():
        if name != "rply" and not name.startswith("rply."):
            continue
        filename = getattr(module, "__file__", None)
        if filename is None:
            continue
        origin = Path(filename).resolve()
        require(any(root in origin.parents for root in roots),
                f"{name} came from {origin}, outside installed site-packages")
        if source is not None:
            require(source != origin and source not in origin.parents,
                    f"{name} came from checkout/src: {origin}")


def verify(backend):
    os.environ["RPLY_BACKEND"] = backend
    import rply
    from rply import Grammar, Lexer, LexerEngine, LRParser, LRTable, Parser, Token, rule, token
    from rply.compat import ply, sly

    origin = verify_origin(rply)
    expected_backend = "python" if backend == "python" else "rust"
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "N", r"\d+")
    lexer.set_ignore("INITIAL", " ")
    lexer.input("12 34")
    first = lexer.token()
    snapshot = lexer.capture_state()
    second = lexer.token()
    lexer.restore_state(snapshot)
    repeated = lexer.token()
    require((first.value, first.lexpos) == ("12", 0), "lexer token/value/position contract")
    require((second.value, repeated.value, repeated.lexpos) == ("34", "34", 3), "snapshot replay contract")

    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"], lambda p: p[1] + 1)
    grammar.set_start("s")
    table = LRTable(grammar)
    dynamic = LRParser(table)
    frozen = LRParser(table, frozen=True)
    require(lexer.backend == table.backend == dynamic.backend == frozen.backend == expected_backend,
            f"basic components did not select {expected_backend} under {backend}")
    require(dynamic.parse([Token("N", 4)]) == 5, "basic core parse")
    table.productions[1].func = lambda p: p[1] + 10
    require(dynamic.parse([Token("N", 4)]) == 14, "dynamic action edits must remain visible")
    require(frozen.parse([Token("N", 4)]) == 5, "frozen action binding must remain detached")
    try:
        frozen.action[0]["N"] = None
    except TypeError:
        pass
    else:
        raise AssertionError("frozen ACTION row accepted mutation")

    calls = []
    failure = RuntimeError("installed token value getter failure")

    class BrokenToken:
        type = "N"

        @property
        def value(self):
            raise failure

    table.productions[1].func = lambda p: calls.append(p[1])
    try:
        dynamic.parse([BrokenToken()])
    except RuntimeError as error:
        require(error is failure, "token getter exception identity changed")
    else:
        raise AssertionError("token getter exception was swallowed")
    require(calls == [], "action executed after token conversion failed")

    from rply.backends.python.lexer import PythonLexerEngine
    from rply.core.parser_context import ParseContext

    python_lexer = PythonLexerEngine()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        python_lexer.add_rule("INITIAL", "SET", r"[a&&b]")
        python_lexer.add_rule("INITIAL", "X", "x")
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        require([(tok.type, tok.value) for tok in python_lexer.tokenize("&x")]
                == [("SET", "&"), ("X", "x")], "grouped scanning repeated regex warning")

    cached_lexer = LexerEngine()
    cached_lexer.add_rule("INITIAL", "WORD", "[a-z]+")
    require(next(cached_lexer.tokenize("a")).value == "a", "warm scanner")
    cached_lexer.set_ignore("INITIAL", "#\n")
    cached_lexer.add_rule("INITIAL", "PAIR", "ab", priority=10)
    require([(tok.type, tok.value, tok.lineno, tok.lexpos)
             for tok in cached_lexer.tokenize("#\nab")] == [("PAIR", "ab", 2, 2)],
            "warm scanner did not refresh ignored characters and rule priority")
    cached_lexer.add_state("inner", "exclusive")
    cached_lexer.add_rule("inner", "INNER", "x")
    reentry_calls = []

    def scan_inner(tok):
        tok.lexer.set_ignore("INITIAL", "!\n")
        tok.lexer.begin("inner")
        reentry_calls.append(tok.lexer.token().type)
        tok.lexer.begin("INITIAL")

    cached_lexer.add_rule("INITIAL", "CHANGE", "@", scan_inner)
    require([(tok.value, tok.lineno, tok.lexpos)
             for tok in cached_lexer.tokenize("@x!\na")] == [("a", 2, 4)],
            "scanner resumed with stale state or ignored characters after reentry")
    require(reentry_calls == ["INNER"], "scanner reentry callback replay")

    hook_calls = []
    original_reduce = ParseContext.reduce

    def hooked_reduce(self, production, lhs, popped, stack):
        hook_calls.append([symbol.value for symbol in popped])
        return original_reduce(self, production, lhs, popped, stack)

    hooked_grammar = Grammar({"N"})
    hooked_grammar.add_production("s", ["N"], lambda p: p[1])
    hooked_grammar.set_start("s")
    hooked_parser = LRParser(LRTable(hooked_grammar))
    ParseContext.reduce = hooked_reduce
    try:
        require(hooked_parser.parse([Token("N", 7)]) == 7, "shared reduction hook result")
        require(hook_calls == [[7]], "shared reduction hook received a changed protocol")
    finally:
        ParseContext.reduce = original_reduce

    for arity in range(6):
        expected_values = tuple(object() for _ in range(arity))
        value_calls = []

        def value_action(*args):
            value_calls.append(args)
            return None

        value_grammar = Grammar({"N"})
        value_grammar.add_production("s", ["N"] * arity, value_action, action_style="values")
        value_grammar.set_start("s")
        value_parser = LRParser(LRTable(value_grammar), frozen=True)
        result = value_parser.parse([Token("N", value) for value in expected_values])
        require(result is (expected_values[0] if arity else None), "value action default changed")
        require(len(value_calls) == 1 and len(value_calls[0]) == arity,
                "value action arity or callback count changed")
        require(all(actual is expected for actual, expected in zip(value_calls[0], expected_values)),
                "value action argument identity changed")

    release_trace, release_proxies = [], []

    class DefaultValue:
        def __del__(self):
            try:
                release_trace.append(("released", release_proxies[0][0]))
            except Exception as error:
                release_trace.append(("release-error", type(error).__name__))

    def replace_default(proxy):
        release_proxies.append(proxy)
        proxy[1] = None
        proxy[0] = 0
        release_trace.append("action-end")
        return 0

    release_grammar = Grammar({"N"})
    release_grammar.add_production("s", ["N"], replace_default)
    release_grammar.set_start("s")
    release_parser = LRParser(LRTable(release_grammar))

    def release_tokens():
        yield Token("N", DefaultValue())

    require(release_parser.parse(release_tokens()) == 0, "default value replacement result")
    expected_release_trace = (["action-end", ("released", 0)] if expected_backend == "rust"
                              else [("released", 0), "action-end"])
    require(release_trace == expected_release_trace, "default value release timing or LHS borrow changed")

    for failed in (False, True):
        slice_trace = []
        slice_failure = RuntimeError("installed slice action failed")

        class SliceValue:
            def __init__(self, number):
                self.number = number

            def __del__(self):
                slice_trace.append(self.number)

        def delete_slice(proxy):
            del proxy.slice[1:]
            proxy[0] = 0
            slice_trace.append("action-end")
            if failed:
                raise slice_failure
            return 0

        slice_grammar = Grammar({"N"})
        slice_grammar.add_production("s", ["N", "N", "N"], delete_slice)
        slice_grammar.set_start("s")
        slice_parser = LRParser(LRTable(slice_grammar))

        def slice_tokens():
            for number in (1, 2, 3):
                yield Token("N", SliceValue(number))

        try:
            result = slice_parser.parse(slice_tokens())
        except RuntimeError as error:
            require(failed and error is slice_failure, "slice exception identity changed")
        else:
            require(not failed and result == 0, "slice exception lost or result changed")
        if failed:
            expected_trace = ["action-end"] if expected_backend == "rust" else [3, 2, "action-end"]
            require(slice_trace == expected_trace, "RHS traceback lifetime changed")
            slice_failure.__traceback__ = None
        expected_trace = (["action-end", 3, 2, 1] if expected_backend == "rust"
                          else [3, 2, "action-end", 1])
        require(slice_trace == expected_trace, "slice RHS release order changed")

    recovery = Grammar({"START", "N", "BAD", "SEMI"})
    recovery.add_production("s", ["START", "stmts"], lambda p: p[2])
    recovery.add_production("stmts", ["stmts", "stmt"], lambda p: p[1] + [p[2]])
    recovery.add_production("stmts", ["stmt"], lambda p: [p[1]])
    recovery.add_production("stmt", ["N", "SEMI"], lambda p: ("n", p[1]))
    recovery.add_production("stmt", ["error", "SEMI"], lambda p: ("error", p[1]))
    recovery.set_start("s")
    errors = []
    recovering = LRParser(LRTable(recovery), error_func=lambda t: errors.append(t.value), frozen=True)
    types = ("START", "BAD", "SEMI", "N", "SEMI", "BAD", "SEMI")
    result = recovering.parse([Token(kind, index) for index, kind in enumerate(types)])
    require(result == [("error", 1), ("n", 3), ("error", 5)], "recovery lost a completed statement")
    require(errors == [1, 5], "recovery callback suppression contract")

    class NumberLexer(Lexer):
        @token(r"\d+")
        def N(self, tok):
            tok.value = int(tok.value)
            return tok

    class NumberParser(Parser):
        tokens = {"N"}

        @rule("s : N")
        def number(self, proxy):
            require(proxy.parser is self, "modern parser callback owner")
            return proxy[1] + 1

    require(NumberParser().freeze().parse(NumberLexer().tokenize("41")) == 42, "modern API")

    def p_number(proxy):
        "s : N"
        proxy[0] = int(proxy[1]) + 1

    compat_lexer = ply.lex.lex(module={"tokens": ("N",), "t_N": r"\d+"})
    compat_parser = ply.yacc.yacc(module={"tokens": ("N",), "p_number": p_number})
    require(compat_parser.parse("41", lexer=compat_lexer) == 42, "PLY compatibility API")

    class SlyLexer(sly.Lexer):
        tokens = {N}
        N = r"\d+"

    class SlyParser(sly.Parser):
        tokens = SlyLexer.tokens

        @_("N")
        def s(self, proxy):
            return int(proxy[0]) + 1

    require(SlyParser().parse(SlyLexer().tokenize("41")) == 42, "SLY compatibility API")

    class TrackedText(str):
        def __new__(cls, value):
            result = super().__new__(cls, value)
            result.slices = []
            return result

        def __getitem__(self, key):
            if isinstance(key, slice):
                self.slices.append(key)
            return super().__getitem__(key)

    saved_errors = []
    ply_errors = ply.lex.lex(module={"tokens": (), "t_error": saved_errors.append})
    bad_input = TrackedText("?" * 128)
    ply_errors.input(bad_input)
    require(list(ply_errors) == [], "PLY error recovery output")
    require(len(saved_errors) == 128 and not bad_input.slices, "PLY eager error suffix copies")
    ply_errors.input("replacement")
    require(saved_errors[17].value == "?" * 111, "PLY saved error suffix")
    require(bad_input.slices == [slice(17, None)], "PLY error value was not cached")

    saved_errors = []

    class SlyErrors(sly.Lexer):
        tokens = set()

        def error(self, tok):
            saved_errors.append(tok)

    bad_input = TrackedText("?" * 128)
    require(list(SlyErrors().tokenize(bad_input)) == [], "SLY error recovery output")
    require(len(saved_errors) == 128 and not bad_input.slices, "SLY eager error suffix copies")
    require(saved_errors[17].value == "?" * 111, "SLY saved error suffix")
    require(bad_input.slices == [slice(17, None)], "SLY error value was not cached")
    import copy
    import pickle
    copied_error = copy.deepcopy(saved_errors[18])
    restored_error = pickle.loads(pickle.dumps(saved_errors[19]))
    require(type(copied_error) is type(restored_error) is type(saved_errors[18]),
            "SLY copied/serialized public token type")
    require(copied_error.value == "?" * 110 and restored_error.value == "?" * 109,
            "SLY copied/serialized unread error suffix")

    class OpaqueValue:
        @property
        def __class__(self):
            raise AssertionError("stored value class was inspected")

    for compat_token in (ply.lex.LexToken(), type(saved_errors[18])()):
        opaque = OpaqueValue()
        compat_token.value = opaque
        require(compat_token.value is opaque, "compat token changed an opaque stored value")

    for token_class in (ply.lex.LexToken, type(saved_errors[18])):
        class PrivateMetadata(token_class):
            __slots__ = ("_value",)

        original = PrivateMetadata()
        original.value = "public"
        original._value = "metadata"
        copied = copy.deepcopy(original)
        require((original.value, copied.value, copied._value) ==
                ("public", "public", "metadata"), "compat subtype private metadata was lost")

        class ShadowValue(token_class):
            __slots__ = ("value",)

        original = ShadowValue()
        original.value = "subtype value"
        require(copy.deepcopy(original).value == "subtype value",
                "compat subtype public value was lost")

    core_errors = []

    class AssigningText(str):
        def __getitem__(self, key):
            if isinstance(key, slice):
                core_errors[0].value = "assigned during slicing"
            return super().__getitem__(key)

    core_error_lexer = LexerEngine()
    core_error_lexer.set_error_func("INITIAL", core_errors.append)
    require(list(core_error_lexer.tokenize(AssigningText("?"))) == [], "core error output")
    require(core_errors[0].value == "assigned during slicing" and
            core_errors[0].value == "assigned during slicing",
            "lazy core value overwrote a callback assignment")

    finalizer_errors = []
    finalizer_values = []

    class FinalizingText(str):
        def __del__(self):
            finalizer_values.append(finalizer_errors[0].value)
            finalizer_errors[0].value = "assigned by finalizer"

    finalizer_lexer = LexerEngine()
    finalizer_lexer.set_error_func("INITIAL", finalizer_errors.append)
    finalizer_lexer.input(FinalizingText("?"))
    require(finalizer_lexer.token() is None, "finalizer error output")
    finalizer_lexer.input("replacement")
    finalizer_errors[0].value = "committed assignment"
    require(finalizer_values == ["committed assignment"] and
            finalizer_errors[0].value == "assigned by finalizer",
            "lazy core assignment released its input before committing")

    mixed = Grammar({"a", "b", "HIGH"})
    mixed.set_precedence([("left", ["a"]), ("right", ["HIGH"])])
    for line, (name, rhs, precedence) in enumerate([
            ("A", [], None), ("B", [], "HIGH"), ("s", ["A", "a"], None),
            ("s", ["B", "a"], None), ("s", ["a", "b"], None)], 1):
        mixed.add_production(name, rhs, lambda *values, rule=line: (rule, values),
                             line=line, precedence=precedence, action_style="values")
    mixed.set_start("s")
    mixed_table = LRTable(mixed)
    require(mixed_table.action[0]["a"] == -2, "mixed conflict closure order")
    for frozen in (False, True):
        require(LRParser(mixed_table, frozen=frozen).parse([Token("a", "a")]) ==
                (4, ((2, ()), "a")), "mixed conflict parse result")

    nullable = Grammar()
    width = 1100
    nullable.add_production("s", [f"N{i}" for i in range(width)],
                            lambda *values: tuple(values), action_style="values")
    for index in range(width):
        nullable.add_production(f"N{index}", [], lambda value=index: value, action_style="values")
    nullable.set_start("s")
    require(LRParser(LRTable(nullable)).parse([]) == tuple(range(width)),
            "long nullable grammar build/parse")

    if expected_backend == "rust":
        import builtins
        ordinary = LexerEngine()
        ordinary.add_rule("INITIAL", "N", r"\d+")
        ordinary.set_ignore("INITIAL", " ")
        original_import = builtins.__import__

        def forbid_error_import(name, *args, **kwargs):
            require(name != "rply.core.lexer_engine", "normal token imported LexError")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = forbid_error_import
        try:
            require([tok.value for tok in ordinary.tokenize("1 2")] == ["1", "2"],
                    "native ordinary scan")
        finally:
            builtins.__import__ = original_import

    def owner_cycle():
        owner = LexerEngine()
        owner.add_rule("INITIAL", "N", "n")
        owner.saved = next(owner.tokenize("n"))
        require(gc.is_tracked(owner.saved), "lexer token is not GC tracked")
        return weakref.ref(owner)

    reference = owner_cycle()
    gc.collect()
    require(reference() is None, "saved token/lexer owner cycle leaked")

    def value_cycle():
        class Holder:
            pass

        owner = LexerEngine()
        owner.add_rule("INITIAL", "N", "n")
        tok = next(owner.tokenize("n"))
        holder = Holder()
        holder.token = tok
        tok.value = holder
        tok.lexer = None
        return weakref.ref(holder)

    reference = value_cycle()
    gc.collect()
    require(reference() is None, "self-referential token value cycle leaked")

    # Supported basic inputs must select native in auto; unsupported native
    # configuration may still hand off to Python under the documented policy.
    if backend == "auto":
        import re
        fallback = LexerEngine()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fallback.add_rule("INITIAL", "WORD", r"\w+", flags=re.ASCII)
        require(fallback.backend == "python" and caught, "auto fallback was not reported")
        require(next(fallback.tokenize("word")).value == "word", "auto fallback parse")

    verify_loaded_origins()
    return {"requested_backend": backend, "basic_backend": expected_backend,
            "origin": origin, "contracts": ["core", "snapshot", "frozen", "exception",
                                             "recovery", "modern", "ply", "sly", "gc",
                                             "lazy-compat-errors", "long-nullable-grammar",
                                             "native-error-import", "compat-subtype-state",
                                             "lazy-core-reentry", "mixed-conflict-order",
                                             "regex-warning-policy", "reduction-hook-protocol",
                                             "scanner-cache-refresh", "scanner-cache-reentry",
                                             "reduction-arguments", "reduction-finalizers",
                                             "reduction-slice-lifetimes"]}


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    mode = arguments.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--backend", choices=("python", "rust", "auto"))
    args = arguments.parse_args()
    if args.all:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        script = str(Path(__file__).resolve())
        for backend in ("python", "rust", "auto"):
            subprocess.run([sys.executable, "-I", script, "--backend", backend],
                           env=env, cwd=Path.cwd(), check=True, timeout=60)
    else:
        print(json.dumps(verify(args.backend), ensure_ascii=False))


if __name__ == "__main__":
    main()
