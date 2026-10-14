"""Run identical workload definitions with ply-rs, upstream PLY, or upstream SLY."""

import importlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def token_action(rule):
    def action(tok):
        if rule.convert is not None:
            tok.value = rule.convert(tok.value)
        return tok
    action.__doc__ = rule.pattern
    return action


def parse_error(tok):
    raise SyntaxError(f"Unexpected benchmark token: {tok!r}")


class Runtime:
    def __init__(self, tokenize, parse, state_count):
        self.tokenize = tokenize
        self.parse = parse
        self.state_count = state_count


def build_ply_rs(workload, engine):
    from ply_rs import Grammar, LexerEngine, LRParser, LRTable

    lexer = LexerEngine()
    for rule in workload.tokens:
        lexer.add_rule("INITIAL", rule.name, rule.pattern,
                       token_action(rule) if rule.convert is not None else None)
    lexer.set_ignore("INITIAL", " \t\r\n")
    grammar = Grammar({rule.name for rule in workload.tokens})
    for rule in workload.rules:
        def make_action(rule):
            arity = len(rule.rhs.split())
            def action(p):
                return rule.action(*(p[i + 1] for i in range(arity)))
            return action
        grammar.add_production(rule.name, rule.rhs.split(), func=make_action(rule), action_style="proxy")
    grammar.set_start(workload.start)
    table = LRTable(grammar)
    parser = LRParser(table, error_func=parse_error)
    if (lexer.backend, table.backend, parser.backend) != (engine,) * 3:
        raise RuntimeError(f"Requested {engine}, got {lexer.backend}/{table.backend}/{parser.backend}")
    return Runtime(lexer.tokenize, parser.parse, len(table.action))


def load_reference(name):
    """Prefer pinned local clones; fall back to explicitly installed packages."""
    package = ROOT / "upstream" / name / "src" / name / "__init__.py"
    if not package.is_file():
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as error:
            if error.name != name:
                raise
            raise RuntimeError(f"Missing {name}; see docs/upstream.md or install {name}") from error
    alias = f"_benchmark_reference_{name}"
    if alias not in sys.modules:
        spec = importlib.util.spec_from_file_location(alias, package)
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(alias, None)
            raise
    return sys.modules[alias]


def build_ply(workload):
    package = load_reference("ply")
    lex = importlib.import_module(f"{package.__name__}.lex")
    yacc = importlib.import_module(f"{package.__name__}.yacc")
    spec = SimpleNamespace(__file__=__file__, __module__=__name__,
                           tokens=tuple(rule.name for rule in workload.tokens),
                           t_ignore=" \t\r\n", t_error=parse_error, p_error=parse_error)
    for index, rule in enumerate(workload.tokens):
        if rule.convert is None:
            setattr(spec, f"t_{rule.name}", rule.pattern)
            continue
        action = token_action(rule)
        # PLY sorts function rules by source line. Preserve shared rule order.
        action.__code__ = action.__code__.replace(co_firstlineno=1000 + index)
        setattr(spec, f"t_{rule.name}", action)
    for index, rule in enumerate(workload.rules):
        def make_action(rule):
            arity = len(rule.rhs.split())
            def action(p):
                p[0] = rule.action(*(p[i + 1] for i in range(arity)))
            action.__doc__ = f"{rule.name} : {rule.rhs}"
            return action
        setattr(spec, f"p_rule_{index:04d}", make_action(rule))
    lexer = lex.lex(module=spec)
    # PLY versions differ in table-cache options. Omit disk caching when supported.
    import inspect
    kwargs = {"write_tables": False} if "write_tables" in inspect.signature(yacc.yacc).parameters else {}
    if "tabmodule" in inspect.signature(yacc.yacc).parameters:
        kwargs["tabmodule"] = "_ply_rs_benchmark_no_cached_table"
    parser = yacc.yacc(module=spec, start=workload.start, debug=False, **kwargs)

    def tokenize(text):
        lexer.lineno = 1
        lexer.input(text)
        return iter(lexer.token, None)

    def parse(tokens):
        # Supply a fresh cursor each time, including when replaying a token list.
        iterator = iter(tokens)
        cursor = SimpleNamespace(lineno=1, lexpos=0)
        def token():
            tok = next(iterator, None)
            if tok is not None:
                cursor.lineno, cursor.lexpos = tok.lineno, tok.lexpos
            return tok
        cursor.token = token
        return parser.parse(lexer=cursor, tracking=True)
    return Runtime(tokenize, parse, len(parser.action))


def build_sly(workload):
    api = load_reference("sly")
    meta = type(api.Lexer)
    namespace = meta.__prepare__("BenchmarkLexer", (api.Lexer,))
    namespace.update(__module__=__name__, tokens={rule.name for rule in workload.tokens},
                     ignore=" \t\r\n")
    for rule in workload.tokens:
        if rule.convert is None:
            namespace[rule.name] = rule.pattern
            continue
        def make_action(rule):
            action = token_action(rule)
            def method(self, tok):
                return action(tok)
            return method
        namespace[rule.name] = namespace["_"](rule.pattern)(make_action(rule))
    Lexer = meta("BenchmarkLexer", (api.Lexer,), namespace)
    meta = type(api.Parser)
    namespace = meta.__prepare__("BenchmarkParser", (api.Parser,))
    namespace.update(__module__=__name__, tokens=Lexer.tokens, start=workload.start,
                     debugfile=None, track_positions=True)
    for rule in workload.rules:
        def make_action(rule):
            arity = len(rule.rhs.split())
            def method(self, p):
                return rule.action(*(p[i] for i in range(arity)))
            method.__name__ = rule.name
            return method
        namespace[rule.name] = namespace["_"](rule.rhs)(make_action(rule))
    def error(self, tok):
        parse_error(tok)
    namespace["error"] = error
    Parser = meta("BenchmarkParser", (api.Parser,), namespace)
    lexer, parser = Lexer(), Parser()
    return Runtime(lexer.tokenize, lambda tokens: parser.parse(iter(tokens)), len(parser._lrtable.lr_action))


def build(workload, engine):
    if engine == "rust":
        return build_ply_rs(workload, engine)
    return {"ply": build_ply, "sly": build_sly}[engine](workload)
