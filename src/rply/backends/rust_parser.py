"""Native parser adapter; native table reuse remains a backend detail."""

from types import MethodType

from rply.backends.rust import RustTableArtifact
from rply.core.parser_context import ParseContext
from rply.core import parser_context
from rply.core.table_data import native_rows


_SLICE_REDUCE = ParseContext.reduce
_NATIVE_PROXY = parser_context.ProductionProxy
_PROXY_INIT = _NATIVE_PROXY.__init__
_PROXY_NEW = _NATIVE_PROXY.__new__
_PROXY_SETATTR = _NATIVE_PROXY.__setattr__


class RustParserRuntime:
    def __init__(self, data, module, artifact=None):
        self.data = data
        self.module = module
        self.artifact = artifact
        # Detect stale native interfaces before constructing a parser.
        module.LRParserEngine.from_engine
        self._supports_prepared_slice = getattr(module.LRParserEngine, "supports_prepared_slice", False)
        self._supports_control = getattr(module.LRParserEngine, "supports_recovery_control", False)
        self._supports_native_proxy = getattr(module.LRParserEngine, "supports_native_proxy", False)
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
            self._action_snapshot = {state: None if row is None else row.copy() for state, row in data.action.items()}
            self._goto_snapshot = {state: None if row is None else row.copy() for state, row in data.goto.items()}
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
        control_args = {}
        if self._supports_control:
            control_args = {"control": context.control, "recovery_style": context.recovery_style}
        elif context.control is not None or context.recovery_style != "core":
            raise RuntimeError("Native extension lacks recovery controls; rebuild rply._core_rs")
        if (self._supports_native_proxy and prepared_slice
                and parser_context.ProductionProxy is _NATIVE_PROXY
                and _NATIVE_PROXY.__init__ is _PROXY_INIT
                and _NATIVE_PROXY.__new__ is _PROXY_NEW
                and _NATIVE_PROXY.__setattr__ is _PROXY_SETATTR):
            control_args["proxy_context"] = (
                productions, _NATIVE_PROXY, parser_context._invoke_proxy,
                context.lexer, context.parser,
            )
        if prepared_slice:
            return engine.parse(
                token_stream, reduce_cb, context.error_func, value_funcs,
                with_stack=True, prepared_slice=True,
                **control_args,
            )
        return engine.parse(
            token_stream, reduce_cb, context.error_func, value_funcs,
            with_stack=True,
            **control_args,
        )
