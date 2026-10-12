"""Pure-Python regular expression lexer and state management."""

from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Union
from rply.core.tokens import Token, LexError
from rply.core.lexer_config import LexerConfig, MAX_LEXER_COUNTER, lexer_counter


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

    def input(self, text: str) -> None:
        """Feed input text into the lexer and reset position."""
        if not isinstance(text, str):
            raise TypeError("Input must be a string")
        self.data = text
        self.length = len(text)
        self.pos = 0
        self.lineno = 1
        self.current_state = "INITIAL"
        self.state_stack.clear()
        self.input_revision = (self.input_revision + 1) & MAX_LEXER_COUNTER

    def restore_state(self, state):
        state = state.validated(self.states)
        self.input(state.data)
        self.pos = state.pos
        self.lineno = state.lineno
        self.current_state = state.current_state
        self.state_stack[:] = state.state_stack

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
            if self._rules_revision != self.config.revision:
                self._active_rules.clear()
                self._rules_revision = self.config.revision
            curr_state = self._current_state
            ignore = self.ignore.get(curr_state, "")

            # Fast skip ignored single characters
            while self._pos < length and data[self._pos] in ignore:
                if data[self._pos] == "\n":
                    self._lineno += 1
                self._pos += 1

            if self._pos >= length:
                break

            # Rule registration invalidates all inclusive-state caches.
            active_rules = self._active_rules.get(curr_state)
            if active_rules is None:
                rules = tuple(self.rules.get(curr_state, ()))
                if self.states.get(curr_state) == "inclusive" and curr_state != "INITIAL":
                    rules = tuple(self.rules.get("INITIAL", ())) + rules
                self._active_rules[curr_state] = active_rules = tuple(
                    sorted(rules, key=lambda rule: rule.priority, reverse=True)
                )

            # Match by priority, retaining declaration order on ties.
            matched = False
            for rule in active_rules:
                m = rule.compiled.match(data, self._pos)
                if not m:
                    continue

                matched = True
                val = m.group(0)
                start_pos = self._pos
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
                if rule.func:
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
                if err_func:
                    err_tok = Token(
                        tok_type="error",
                        value=data[self._pos:],
                        lineno=self._lineno,
                        lexpos=self._pos,
                        end=length,
                        lexer=self.callback_owner,
                    )
                    saved_pos = self._pos
                    saved_input = self.input_revision
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
