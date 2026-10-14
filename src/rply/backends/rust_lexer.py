"""Native lexer runtime adapter; the native engine owns all scan state."""


class RustLexerRuntime:
    def __init__(self, module, callback_owner):
        self.engine = module.LexerEngine()
        # Reject stale extensions at setup rather than failing during a handoff.
        self.engine.state_stack
        self.engine.input_revision
        self.engine.restore_state
        self.engine.callback_owner = callback_owner
        # Bind the native token method once; no Python forwarding frame per token.
        self.token = self.engine.token

    @property
    def pos(self):
        return self.engine.pos

    @pos.setter
    def pos(self, value):
        self.engine.pos = value

    @property
    def lineno(self):
        return self.engine.lineno

    @lineno.setter
    def lineno(self, value):
        self.engine.lineno = value

    @property
    def current_state(self):
        return self.engine.current_state

    @current_state.setter
    def current_state(self, value):
        self.engine.begin(value)

    @property
    def data(self):
        return self.engine.data

    @property
    def length(self):
        return self.engine.length

    @property
    def state_stack(self):
        return self.engine.state_stack

    @state_stack.setter
    def state_stack(self, value):
        self.engine.state_stack = value

    def configure(self, method, *args, **kwargs):
        getattr(self.engine, method)(*args, **kwargs)

    @property
    def input_revision(self):
        return self.engine.input_revision

    def restore_state(self, state):
        self.engine.restore_state(state.data, state.pos, state.lineno,
                                  state.current_state, list(state.state_stack))

    def input(self, text):
        self.engine.input(text)

    def begin(self, state):
        self.engine.begin(state)

    def push_state(self, state):
        self.engine.push_state(state)

    def pop_state(self):
        self.engine.pop_state()

    def skip(self, n=1):
        self.engine.pos += n

