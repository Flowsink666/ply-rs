"""Small user objects for testing implicit calls into Python.

These objects record public observations, rather than inspecting runtime
implementation details. Keep them reusable by state and lifecycle contracts.
"""


def lexer_state(lexer):
    snapshot = lexer.capture_state()
    return (
        snapshot.data, snapshot.pos, snapshot.lineno, snapshot.current_state,
        snapshot.state_stack, lexer.length, lexer._input_revision,
    )


class CallbackFailure(RuntimeError):
    pass


class RecordingAction:
    """A callback whose next behavior can be changed without re-registering it."""

    def __init__(self, mode, replacement="", from_error=False):
        self.mode = mode
        self.replacement = replacement
        self.from_error = from_error
        self.calls = []

    def __call__(self, token):
        self.calls.append((
            token.type, token.value, token.lineno, token.lexpos, token.end,
            lexer_state(token.lexer),
        ))
        if self.mode == "raise":
            raise CallbackFailure("callback failed")
        if self.mode in ("replace_ignore", "replace_return", "recursive"):
            token.lexer.input(self.replacement)
            if self.mode == "recursive":
                return token.lexer.token()
            return token if self.mode == "replace_return" else None
        if self.mode == "return":
            if self.from_error:
                token.lexer.skip()
            return token
        assert self.mode == "ignore", self.mode
        return None


class FinalizingText(str):
    """Run a supplied user operation when the engine releases its old input."""

    def __new__(cls, text, on_release):
        instance = super().__new__(cls, text)
        instance.on_release = on_release
        return instance

    def __del__(self):
        self.on_release()
