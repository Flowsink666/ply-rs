"""Pure-Python LR runtime and error recovery."""

from types import MethodType
from typing import Any, Callable, Iterator, List, Optional, Sequence, Union
from rply.core.symbols import EOF_SYMBOL, ERROR_SYMBOL
from rply.core.tokens import Token
from rply.core.production import ParsedSymbol, ParserError
from rply.core.table_data import ParseTableData
from rply.core.parser_context import ParseContext


_MISSING_ACTION = object()
_EMPTY_ACTION_ROW = {}
_SLICE_REDUCE = ParseContext.reduce


class PythonLRParser:
    """LR parsing engine."""

    def __init__(self, table: ParseTableData, error_func: Optional[Callable[..., Any]] = None):
        self.table = table
        self.error_func = error_func

    @property
    def productions(self):
        return self.table.productions

    @productions.setter
    def productions(self, value):
        self.table.productions = value

    @property
    def action(self):
        return self.table.action

    @action.setter
    def action(self, value):
        self.table.action = value

    @property
    def goto(self):
        return self.table.goto

    @goto.setter
    def goto(self, value):
        self.table.goto = value

    def parse(
        self,
        token_stream: Union[Iterator[Any], Sequence[Any]],
        lexer: Any = None,
        tracking: bool = True,
        *,
        context: Optional[ParseContext] = None,
    ) -> Any:
        """
        Execute LR parsing on the token stream.
        """
        statestack: List[int] = [0]
        context = context if context is not None else ParseContext(lexer, self, tracking, self.error_func)
        table_data = self.table
        # Bind once per invocation, so between-call table replacements remain
        # visible without repeated facade/table attribute loads in the LR loop.
        action_rows = table_data.action
        goto_rows = table_data.goto
        # Plain dictionaries and our frozen snapshots have ordinary indexing
        # semantics. Other mappings retain get(), including defaultdict rows
        # whose __missing__ must not invent a transition or modify the table.
        indexed_actions = type(action_rows) is dict or table_data.frozen
        indexed_gotos = table_data.frozen or (
            type(goto_rows) is dict
            and all(type(row) is dict or row is None for row in goto_rows.values())
        )
        productions = table_data.productions
        missing_action = _MISSING_ACTION
        empty_row = _EMPTY_ACTION_ROW
        reduce_action = context.reduce
        # Only the original shared method accepts the internal prepared slice.
        # Hooks on that class, as well as custom contexts, retain the public
        # four-argument protocol and the original RHS-only argument list.
        accepts_slice = (
            type(context) is ParseContext and type(reduce_action) is MethodType
            and reduce_action.__func__ is _SLICE_REDUCE
        )
        symstack: List[ParsedSymbol] = [ParsedSymbol(EOF_SYMBOL)]

        token_iter = iter(token_stream)

        def get_next_token() -> ParsedSymbol:
            try:
                tok = next(token_iter)
            except StopIteration:
                return ParsedSymbol(EOF_SYMBOL, EOF_SYMBOL, lineno=1, lexpos=0)
            if type(tok) is Token:
                return ParsedSymbol(tok.type, tok.value, tok.lineno, tok.lineno, tok.lexpos, tok.end)
            # Only iterator exhaustion denotes EOF; property failures propagate.
            try:
                sym_type = tok.type
            except AttributeError:
                sym_type = str(tok)
            sym_val = getattr(tok, "value", tok)
            lineno = getattr(tok, "lineno", 1)
            try:
                lexpos = tok.lexpos
            except AttributeError:
                lexpos = getattr(tok, "index", 0)
            try:
                endpos = tok.end
            except AttributeError:
                endpos = lexpos + len(str(sym_val))
            return ParsedSymbol(sym_type, sym_val, lineno, lineno, lexpos, endpos)

        error_count = 0  # Number of valid tokens to shift before exiting error recovery mode
        recovery_lookahead = None  # Real token preserved while shifting synthetic 'error'.
        lookahead = get_next_token()

        while True:
            state = statestack[-1]
            if indexed_actions:
                try:
                    row = action_rows[state]
                except KeyError:
                    row = empty_row
            else:
                row = action_rows.get(state, empty_row)
            if recovery_lookahead is not None:
                # Internal error tokens never use user literal fallback or accept.
                act = row.get(ERROR_SYMBOL, missing_action)
                if act == 0 or (
                    act is not missing_action and act is not None and act < 0
                    and (not 0 < -act < len(productions)
                         or productions[-act].len >= len(statestack))
                ):
                    act = missing_action
            else:
                # Literal fallback applies only to a missing type, never to
                # an explicit nonassoc prohibition (None).
                act = row.get(lookahead.type, missing_action)
                if act is missing_action and isinstance(lookahead.value, str):
                    act = row.get(lookahead.value, missing_action)

            if type(act) is int:
                # Case 1: Shift
                if act > 0:
                    statestack.append(act)
                    symstack.append(lookahead)
                    if recovery_lookahead is not None:
                        lookahead = recovery_lookahead
                        recovery_lookahead = None
                    else:
                        lookahead = get_next_token()
                        if error_count > 0:
                            error_count -= 1

                # Case 2: Reduce
                elif act < 0:
                    p = productions[-act]
                    plen = p.len
                    proxy_slice = accepts_slice and p.func is not None and p.action_style != "values"

                    # Pop RHS symbols
                    if plen > 0:
                        # Like PLY, take one slice including a replaceable LHS
                        # slot. Keep a new list/proxy for each reduction because
                        # user callbacks may retain either object.
                        popped_syms = symstack[-plen-1:] if proxy_slice else symstack[-plen:]
                        del symstack[-plen:]
                        del statestack[-plen:]
                    else:
                        popped_syms = []

                    # Create LHS symbol
                    lhs_sym = ParsedSymbol(p.name)
                    if plen > 0:
                        first_sym = popped_syms[1 if proxy_slice else 0]
                        lhs_sym.lineno = first_sym.lineno
                        lhs_sym.endlineno = popped_syms[-1].endlineno
                        lhs_sym.lexpos = first_sym.lexpos
                        lhs_sym.endlexpos = popped_syms[-1].endlexpos
                        lhs_sym.value = first_sym.value

                    if proxy_slice:
                        if plen > 0:
                            popped_syms[0] = lhs_sym
                        else:
                            popped_syms.append(lhs_sym)

                    # Execute rule semantic action
                    if p.func is not None:
                        if proxy_slice:
                            reduce_action(p, lhs_sym, popped_syms, symstack, popped_syms)
                        else:
                            reduce_action(p, lhs_sym, popped_syms, symstack)
                    # No action or a None result keeps the default value, including
                    # any p[0] assignment made through the proxy's LHS symbol.

                    # Goto next state
                    curr_state = statestack[-1]
                    if indexed_gotos:
                        try:
                            goto_row = goto_rows[curr_state]
                        except KeyError:
                            goto_row = None
                        if goto_row is None:
                            next_state = None
                        else:
                            try:
                                next_state = goto_row[p.name]
                            except KeyError:
                                next_state = None
                    else:
                        goto_row = goto_rows.get(curr_state)
                        next_state = goto_row.get(p.name) if goto_row is not None else None
                    if next_state is None:
                        raise ParserError(f"No GOTO entry for nonterminal {p.name!r} from state {curr_state}")

                    statestack.append(next_state)
                    symstack.append(lhs_sym)

                # Case 3: Accept
                else:
                    return symstack[-1].value

            # Case 4: Syntax Error / Error Recovery
            else:
                if recovery_lookahead is not None:
                    # The synthetic error token also participates in reductions.
                    # Pop only when this state cannot act on it, preserving any
                    # completed statements reduced before reaching this state.
                    if len(statestack) > 1:
                        statestack.pop()
                        symstack.pop()
                        continue
                    lookahead = recovery_lookahead
                    recovery_lookahead = None
                    raise SyntaxError(
                        f"Syntax error at {lookahead.value!r} (type {lookahead.type!r}) on line {lookahead.lineno}, pos {lookahead.lexpos}"
                    )

                if error_count == 0:
                    if context.error_func is not None:
                        res = context.error_func(lookahead)
                        if res is not None:
                            return res

                if symstack[-1].type == ERROR_SYMBOL:
                    # Recovery is still waiting for its synchronization token.
                    if lookahead.type == EOF_SYMBOL:
                        raise SyntaxError(
                            f"Syntax error at EOF on line {lookahead.lineno}, pos {lookahead.lexpos}"
                        )
                    lookahead = get_next_token()
                    continue

                # Let the ordinary LR loop reduce on 'error' before shifting it.
                # The three-shift counter suppresses callbacks, not new recoveries.
                recovery_lookahead = lookahead
                lookahead = ParsedSymbol(
                    ERROR_SYMBOL, lookahead.value,
                    lineno=lookahead.lineno, endlineno=lookahead.endlineno,
                    lexpos=lookahead.lexpos, endlexpos=lookahead.endlexpos,
                )
                error_count = 3
