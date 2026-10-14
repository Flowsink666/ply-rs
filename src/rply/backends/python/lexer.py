"""Pure-Python regular expression lexer and state management."""

import re
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Union
from rply.core.tokens import Token, LexError, _LazyErrorToken
from rply.core.lexer_config import LexerConfig, MAX_LEXER_COUNTER, lexer_counter


# Keep syntax whose diagnostics can change when wrapped out of combined REs.
# This is deliberately conservative, including set-operation-looking text
# outside classes, and never changes the caller's process-wide warning filters.
_STANDALONE_RE_SYNTAX = re.compile(r"\(\?[aiLmsux]+\)|\[\[|&&|~~|\|\||--")


def _compile_rule_group(rules):
    """Merge safe alternatives without changing individual regex semantics."""
    if len(rules) == 1:
        return ((rules[0].compiled.match, rules[0], False),)
    flags = rules[0].compiled.flags
    # A verbose pattern may end in a comment; terminate it before the wrapper.
    suffix = "\n" if flags & re.VERBOSE else ""
    pattern = "|".join(
        f"(?P<_rply_{index}>{rule.compiled.pattern}{suffix})"
        for index, rule in enumerate(rules)
    )
    try:
        compiled = re.compile(pattern, flags)
    except (re.error, RecursionError, OverflowError):
        # Global inline flags, or compiler limits, can prevent wrapping an
        # otherwise valid expression. Keep rule order and use its original RE.
        middle = len(rules) // 2
        return _compile_rule_group(rules[:middle]) + _compile_rule_group(rules[middle:])
    return ((compiled.match, (None,) + tuple(rules), True),)


def _compile_matchers(rules):
    """Build ordered groups; captures/backreferences retain standalone REs.

    Restrict merging to capture-free rules with identical effective flags.
    Wrapping capture groups would renumber backreferences and conditionals,
    and named groups from unrelated rules can collide. Global inline flags and
    ambiguous character-set syntax stay standalone: wrapping can repeat or
    relocate warnings from the original rule's validation.
    """
    matchers = []
    pending = []
    flags = None
    for rule in rules:
        compiled = rule.compiled
        safe = (
            type(compiled) is re.Pattern and not compiled.groups
            and not compiled.flags & re.DEBUG
            and not _STANDALONE_RE_SYNTAX.search(compiled.pattern)
        )
        if pending and (not safe or compiled.flags != flags):
            matchers.extend(_compile_rule_group(pending))
            pending = []
        if safe:
            flags = compiled.flags
            pending.append(rule)
        else:
            matchers.append((compiled.match, rule, False))
    if pending:
        matchers.extend(_compile_rule_group(pending))
    return tuple(matchers)


class PythonLexerEngine:
    """
    Low-level regex lexing engine with state management and action execution.
    """

    def __init__(self, config=None, callback_owner=None):
        self.config = config if config is not None else LexerConfig()
        self.states = self.config.states
        self.rules = self.config.rules
        self.ignore = self.config.ignore
        self.error_funcs = self.config.error_funcs
        self.literals = self.config.literals
        self.callback_owner = callback_owner if callback_owner is not None else self

        # Runtime state
        self.data: str = ""
        self.length: int = 0
        self.pos: int = 0
        self.lineno: int = 1
        self.current_state: str = "INITIAL"
        self.state_stack: List[str] = []
        self._active_rules: Dict[str, tuple] = {}
        self._rules_revision = -1
        self._scan_revision = -1
        self._scan_ignore = ""
        self._scan_matchers = ()
        self.input_revision = 0

    @property
    def pos(self) -> int:
        return self._pos

    @pos.setter
    def pos(self, value: int) -> None:
        self._pos = lexer_counter(value, "position")

    @property
    def lineno(self) -> int:
        return self._lineno

    @lineno.setter
    def lineno(self, value: int) -> None:
        self._lineno = lexer_counter(value, "line number")

    @property
    def current_state(self) -> str:
        return self._current_state

    @current_state.setter
    def current_state(self, value: str) -> None:
        self._scan_revision = -1
        self._current_state = value

    def add_state(self, name: str, kind: str = "inclusive") -> None:
        self.config.add_state(name, kind)

    def add_rule(
        self,
        state: str,
        name: str,
        pattern: str,
        func: Optional[Callable[..., Any]] = None,
        priority: int = 0,
        flags: int = 0,
    ) -> None:
        self.config.add_rule(state, name, pattern, func, priority, flags)

    def set_ignore(self, state: str, chars_or_regex: str) -> None:
        self.config.set_ignore(state, chars_or_regex)

    def set_error_func(self, state: str, func: Callable[..., Any]) -> None:
        self.config.set_error_func(state, func)

    def set_literals(self, lits: Union[str, Sequence[str]]) -> None:
        self.config.set_literals(lits)

    def _replace_input(self, text: str):
        """Keep the previous source alive until the whole transition commits."""
        if not isinstance(text, str):
            raise TypeError("Input must be a string")
        length = str.__len__(text)
        previous = self.data
        self.data = text
        self.length = length
        self.pos = 0
        self.lineno = 1
        self.current_state = "INITIAL"
        self.state_stack.clear()
        self.input_revision = (self.input_revision + 1) & MAX_LEXER_COUNTER
        return previous

    def input(self, text: str) -> None:
        """Feed input text into the lexer and reset position."""
        previous = self._replace_input(text)
        del previous

    def restore_state(self, state):
        state = state.validated(self.states)
        previous = self._replace_input(state.data)
        self.pos = state.pos
        self.lineno = state.lineno
        self.current_state = state.current_state
        self.state_stack[:] = state.state_stack
        del previous

    def begin(self, state: str) -> None:
        """Switch current lexer state."""
        if state not in self.rules:
            raise ValueError(f"Undefined lexer state {state!r}")
        self.current_state = state

    def push_state(self, state: str) -> None:
        """Push current state and transition to new state."""
        self.state_stack.append(self.current_state)
        self.begin(state)

    def pop_state(self) -> None:
        """Pop state from state stack."""
        if not self.state_stack:
            raise ValueError("State stack is empty")
        self.begin(self.state_stack.pop())

    def skip(self, n: int = 1) -> None:
        """Skip n characters."""
        self.pos += n

    def token(self) -> Optional[Token]:
        """Scan and return the next token."""
        while True:
            data = self.data
            length = self.length
            if self._pos >= length:
                break
            curr_state = self._current_state
            scan_revision = self.config.scan_revision
            refresh_scan = (
                self._scan_revision != scan_revision or type(data) is not str
            )
            if refresh_scan:
                if self._rules_revision != self.config.revision:
                    self._active_rules.clear()
                    self._rules_revision = self.config.revision
                ignore = self.ignore.get(curr_state, "")
            else:
                ignore = self._scan_ignore
                active_rules = self._scan_matchers

            # Fast skip ignored single characters
            pos = self._pos
            lineno = self._lineno
            while pos < length:
                c = data[pos]
                if c in ignore:
                    if c == "\n":
                        lineno += 1
                    pos += 1
                else:
                    break
            self._pos = pos
            self._lineno = lineno

            if pos >= length:
                break

            if refresh_scan:
                active_rules = self._active_rules.get(curr_state)
                if active_rules is None:
                    rules = tuple(self.rules.get(curr_state, ()))
                    if self.states.get(curr_state) == "inclusive" and curr_state != "INITIAL":
                        rules = tuple(self.rules.get("INITIAL", ())) + rules
                    ordered = sorted(rules, key=lambda rule: rule.priority, reverse=True)
                    self._active_rules[curr_state] = active_rules = _compile_matchers(ordered)
                # Subclasses can run user code or finalizers: preserve their
                # lookup order and lifetime instead of retaining extra refs.
                if type(curr_state) is str and type(ignore) is str and type(data) is str:
                    self._scan_ignore = ignore
                    self._scan_matchers = active_rules
                    self._scan_revision = scan_revision
                else:
                    self._scan_revision = -1

            # Match by priority, retaining declaration order on ties.
            matched = False
            for match, rule_index, grouped in active_rules:
                m = match(data, pos)
                if not m:
                    continue

                rule = rule_index[m.lastindex] if grouped else rule_index
                matched = True
                val = m.group(0)
                start_pos = pos
                end_pos = m.end()
                if end_pos == start_pos:
                    raise LexError(
                        f"Rule {rule.name!r} matched an empty string at pos {self._pos}",
                        text=data[self._pos:],
                        error_index=self._pos,
                    )
                self._pos = end_pos

                tok = Token(
                    tok_type=rule.name,
                    value=val,
                    lineno=self._lineno,
                    lexpos=start_pos,
                    end=end_pos,
                    lexer=self.callback_owner,
                )

                # Execute action function if provided
                if rule.func is not None:
                    res = rule.func(tok)

                    if res is None:
                        # Ignored token
                        break
                    return res
                else:
                    return tok

            if not matched:
                # Check literals
                char = data[self._pos]
                if char in self.literals:
                    tok = Token(
                        tok_type=char,
                        value=char,
                        lineno=self._lineno,
                        lexpos=self._pos,
                        end=self._pos + 1,
                        lexer=self.callback_owner,
                    )
                    self._pos += 1
                    return tok

                # Check state error function
                err_func = self.error_funcs.get(curr_state)
                if err_func is not None:
                    # Token construction can execute Python (including a
                    # replaced factory), so capture progress before calling it.
                    saved_pos = self._pos
                    saved_input = self.input_revision
                    err_tok = _LazyErrorToken(
                        source=data,
                        lineno=self._lineno,
                        lexpos=saved_pos,
                        lexer=self.callback_owner,
                        end=length,
                    )
                    res = err_func(err_tok)
                    if res is not None:
                        return res
                    # If error func didn't advance position, skip 1 to avoid infinite loop
                    if self.input_revision == saved_input and self._pos == saved_pos:
                        self._pos += 1
                    continue

                # Unhandled lexical error
                raise LexError(
                    f"Illegal character {char!r} at line {self._lineno}, pos {self._pos}",
                    text=data[self._pos:],
                    error_index=self._pos,
                )

        return None

    def tokenize(self, text: Optional[str] = None) -> Iterator[Token]:
        """Generator yielding all tokens until EOF."""
        if text is not None:
            self.input(text)
        while True:
            tok = self.token()
            if tok is None:
                break
            yield tok
