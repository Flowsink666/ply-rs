"""Pure-Python LR runtime and error recovery."""

from typing import Any, Callable, Iterator, List, Optional, Sequence, Union
from rply.core.symbols import EOF_SYMBOL, ERROR_SYMBOL
from rply.core.production import ParsedSymbol, ProductionProxy, ParserError
from rply.backends.python.lr_table import PythonLRTable


class PythonLRParser:
    """LR parsing engine."""

    def __init__(self, table: PythonLRTable, error_func: Optional[Callable[..., Any]] = None):
        self.table = table
        self.productions = table.productions
        self.action = table.action
        self.goto = table.goto
        self.error_func = error_func

    def parse(
        self,
        token_stream: Union[Iterator[Any], Sequence[Any]],
        lexer: Any = None,
        tracking: bool = True,
    ) -> Any:
        """
        Execute LR parsing on the token stream.
        """
        statestack: List[int] = [0]
        symstack: List[ParsedSymbol] = [ParsedSymbol(EOF_SYMBOL)]

        token_iter = iter(token_stream)

        def get_next_token() -> ParsedSymbol:
            try:
                tok = next(token_iter)
                # Convert token object (PLY LexToken, SLY Token, or custom) to ParsedSymbol
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
            except StopIteration:
                return ParsedSymbol(EOF_SYMBOL, EOF_SYMBOL, lineno=1, lexpos=0)

        error_count = 0  # Number of valid tokens to shift before exiting error recovery mode
        lookahead = get_next_token()

        while True:
            state = statestack[-1]
            act = self.action.get(state, {}).get(lookahead.type)

            # If not matched by type, try literal match by value (e.g. '+' in literals)
            if act is None and isinstance(lookahead.value, str):
                act = self.action.get(state, {}).get(lookahead.value)

            # Case 1: Shift
            if act is not None and act > 0:
                statestack.append(act)
                symstack.append(lookahead)
                lookahead = get_next_token()
                if error_count > 0:
                    error_count -= 1

            # Case 2: Reduce
            elif act is not None and act < 0:
                p = self.productions[-act]
                plen = p.len

                # Pop RHS symbols
                if plen > 0:
                    popped_syms = symstack[-plen:]
                    del symstack[-plen:]
                    del statestack[-plen:]
                else:
                    popped_syms = []

                # Create LHS symbol
                lhs_sym = ParsedSymbol(p.name)
                if popped_syms:
                    lhs_sym.lineno = popped_syms[0].lineno
                    lhs_sym.endlineno = popped_syms[-1].endlineno
                    lhs_sym.lexpos = popped_syms[0].lexpos
                    lhs_sym.endlexpos = popped_syms[-1].endlexpos
                    lhs_sym.value = popped_syms[0].value

                # Execute rule semantic action
                if p.func:
                    if p.action_style == "values":
                        res = p.func(*(s.value for s in popped_syms))
                    else:
                        proxy = ProductionProxy(
                            [lhs_sym] + popped_syms,
                            stack=symstack,
                            lexer=lexer,
                            parser=self,
                            namemap=p.namemap,
                        )
                        res = p.func(proxy)

                    if res is not None:
                        lhs_sym.value = res
                # No action or a None result keeps the default value, including
                # any p[0] assignment made through the proxy's LHS symbol.

                # Goto next state
                curr_state = statestack[-1]
                next_state = self.goto.get(curr_state, {}).get(p.name)
                if next_state is None:
                    raise ParserError(f"No GOTO entry for nonterminal {p.name!r} from state {curr_state}")

                statestack.append(next_state)
                symstack.append(lhs_sym)

            # Case 3: Accept
            elif act == 0:
                return symstack[-1].value

            # Case 4: Syntax Error / Error Recovery
            else:
                if error_count == 0:
                    if self.error_func:
                        res = self.error_func(lookahead)
                        if res is not None:
                            return res
                else:
                    # Still in error recovery mode: discard lookahead to make progress
                    if lookahead.type == EOF_SYMBOL:
                        raise SyntaxError(
                            f"Syntax error at EOF on line {lookahead.lineno}, pos {lookahead.lexpos}"
                        )
                    lookahead = get_next_token()
                    continue

                # Check for yacc error recovery: state that can shift on 'error'
                error_state = None
                recovery_idx = len(statestack) - 1
                while recovery_idx >= 0:
                    st = statestack[recovery_idx]
                    if ERROR_SYMBOL in self.action.get(st, {}):
                        error_state = recovery_idx
                        break
                    recovery_idx -= 1

                if error_state is not None:
                    # Pop states down to error state
                    pop_count = (len(statestack) - 1) - error_state
                    if pop_count > 0:
                        del statestack[-pop_count:]
                        del symstack[-pop_count:]

                    # Shift error token
                    err_shift = self.action[statestack[-1]][ERROR_SYMBOL]
                    if err_shift and err_shift > 0:
                        statestack.append(err_shift)
                        symstack.append(ParsedSymbol(ERROR_SYMBOL, lookahead.value, lookahead.lineno, lookahead.lexpos))
                        error_count = 3  # Require 3 successful shifts before leaving error mode

                        # Discard tokens until we can shift on lookahead
                        recovered = False
                        while lookahead.type != EOF_SYMBOL:
                            next_act = self.action.get(statestack[-1], {}).get(lookahead.type)
                            if next_act is None and isinstance(lookahead.value, str):
                                next_act = self.action.get(statestack[-1], {}).get(lookahead.value)
                            if next_act is not None:
                                recovered = True
                                break
                            lookahead = get_next_token()

                        if not recovered:
                            # Reached EOF without a valid shift action
                            next_act = self.action.get(statestack[-1], {}).get(lookahead.type)
                            if next_act is None:
                                raise SyntaxError(
                                    f"Syntax error at {lookahead.value!r} (type {lookahead.type!r}) on line {lookahead.lineno}, pos {lookahead.lexpos}"
                                )
                        continue

                raise SyntaxError(
                    f"Syntax error at {lookahead.value!r} (type {lookahead.type!r}) on line {lookahead.lineno}, pos {lookahead.lexpos}"
                )
