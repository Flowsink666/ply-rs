"""Native parser adapter; native table reuse remains a backend detail."""

from types import MethodType

from rply.backends.rust import RustTableArtifact
from rply.core.parser_context import ParseContext
from rply.core.table_data import native_rows


_SLICE_REDUCE = ParseContext.reduce


class RustParserRuntime:
    def __init__(self, data, module, artifact=None):
        self.data = data
        self.module = module
        self.artifact = artifact
        # Detect stale native interfaces during setup, where fallback is safe.
        module.LRParserEngine.from_engine
        self._supports_prepared_slice = getattr(module.LRParserEngine, "supports_prepared_slice", False)
        self._metadata = None
        self._action_snapshot = None
        self._goto_snapshot = None
        self._num_states = None
        self._refresh()

    def _refresh(self):
        data = self.data
        if data.frozen and self._metadata is not None:
            return self.engine
        metadata = data.runtime_metadata()
        tables_changed = (data.num_states != self._num_states
                          or data.action != self._action_snapshot
                          or data.goto != self._goto_snapshot)
        if tables_changed:
            if isinstance(self.artifact, RustTableArtifact):
                engine = self.artifact.create_parser(data, metadata)
            else:
                engine = self.module.LRParserEngine(
                    data.num_states, native_rows(data.action), native_rows(data.goto), *metadata)
        elif metadata != self._metadata:
            engine = self.module.LRParserEngine.from_engine(self.engine, *metadata)
        else:
            return self.engine
        # Publish caches only after successful construction. An active parse keeps
        # its own engine reference if a recursive call refreshes this runtime.
        self.engine = engine
        self._metadata = metadata
        self._has_value_actions = any(metadata.value_actions)
        self._value_funcs = [p.func for p in self.productions] if self._has_value_actions else None
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
        prepared_slice = (
            self._supports_prepared_slice and type(context) is ParseContext
            and type(reduce_action) is MethodType
            and reduce_action.__func__ is _SLICE_REDUCE
        )

        if prepared_slice:
            def reduce_cb(number, lhs, popped, stack, slice_items):
                return reduce_action(productions[number], lhs, popped, stack, slice_items)
        else:
            def reduce_cb(number, lhs, popped, stack):
                return reduce_action(productions[number], lhs, popped, stack)

        value_funcs = self._value_funcs if self.data.frozen else (
            [p.func for p in productions] if self._has_value_actions else None)
        if prepared_slice:
            return engine.parse(
                token_stream, reduce_cb, context.error_func, value_funcs,
                with_stack=True, prepared_slice=True,
            )
        return engine.parse(
            token_stream, reduce_cb, context.error_func, value_funcs,
            with_stack=True,
        )
