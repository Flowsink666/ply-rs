"""Native parser adapter; native table reuse remains a backend detail."""

from rply.backends.rust import RustTableArtifact


class RustParserRuntime:
    def __init__(self, data, module, artifact=None):
        self.data = data
        self.module = module
        self.artifact = artifact
        # Detect stale native interfaces during setup, where fallback is safe.
        module.LRParserEngine.from_engine
        self._metadata = None
        self._action_snapshot = None
        self._goto_snapshot = None
        self._num_states = None
        self._refresh()

    def _refresh(self):
        data = self.data
        metadata = (
            tuple(p.name for p in self.productions), tuple(p.len for p in self.productions),
            tuple(bool(p.func) for p in self.productions),
            tuple(bool(p.func) and p.action_style == "values" for p in self.productions),
        )
        tables_changed = (data.num_states != self._num_states
                          or data.action != self._action_snapshot
                          or data.goto != self._goto_snapshot)
        if tables_changed:
            if isinstance(self.artifact, RustTableArtifact):
                engine = self.artifact.create_parser(data, metadata)
            else:
                engine = self.module.LRParserEngine(data.num_states, data.action, data.goto, *metadata)
        elif metadata != self._metadata:
            engine = self.module.LRParserEngine.from_engine(self.engine, *metadata)
        else:
            return self.engine
        # Publish caches only after successful construction. An active parse keeps
        # its own engine reference if a recursive call refreshes this runtime.
        self.engine = engine
        self._metadata = metadata
        self._has_value_actions = any(metadata[-1])
        if tables_changed:
            self._action_snapshot = {state: row.copy() for state, row in data.action.items()}
            self._goto_snapshot = {state: row.copy() for state, row in data.goto.items()}
            self._num_states = data.num_states
        return engine

    @property
    def productions(self):
        return self.data.productions

    def parse(self, token_stream, *, context):
        engine = self._refresh()
        productions = self.productions
        reduce_action = context.reduce

        def reduce_cb(number, lhs, popped, stack):
            return reduce_action(productions[number], lhs, popped, stack)

        return engine.parse(
            token_stream, reduce_cb, context.error_func,
            [p.func for p in productions] if self._has_value_actions else None,
            with_stack=True,
        )
