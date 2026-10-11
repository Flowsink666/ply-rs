"""Pure-Python LALR(1) table construction."""

from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple
from rply.core.symbols import EOF_SYMBOL, START_SYMBOL
from rply.core.grammar import Grammar, LRItem, Production, GrammarError
from rply.core.conflicts import ConflictRecord

MAXINT = 0x7FFFFFFF


def digraph(
    X: List[Any],
    R: Callable[[Any], List[Any]],
    FP: Callable[[Any], List[str]],
) -> Dict[Any, List[str]]:
    """
    Compute transitive closure of set-valued functions of the form:
        F(x) = F'(x) U U{F(y) | x R y}
    Used in LALR(1) to compute Read sets and Follow sets.
    """
    N: Dict[Any, int] = {x: 0 for x in X}
    stack: List[Any] = []
    F: Dict[Any, List[str]] = {}

    def traverse(x: Any) -> None:
        stack.append(x)
        d = len(stack)
        N[x] = d
        F[x] = list(FP(x))

        for y in R(x):
            if N.get(y, 0) == 0:
                traverse(y)
            N[x] = min(N[x], N[y])
            for a in F.get(y, []):
                if a not in F[x]:
                    F[x].append(a)

        if N[x] == d:
            while True:
                elem = stack.pop()
                N[elem] = MAXINT
                F[elem] = F[x]
                if elem == x:
                    break

    for x in X:
        if N[x] == 0:
            traverse(x)

    return F


class PythonLRTable:
    """
    Generates and stores the LALR(1) action and goto tables.
    """

    def __init__(self, grammar: Grammar):
        self.grammar = grammar
        self.action: Dict[int, Dict[str, Optional[int]]] = {}
        self.goto: Dict[int, Dict[str, int]] = {}
        self.productions = grammar.Productions

        # Conflict tracking
        self.conflicts: List[ConflictRecord] = []
        self.sr_conflicts: List[Tuple[int, str, str]] = []
        self.rr_conflicts: List[Tuple[int, Production, Production]] = []

        # Internal caches for LR0 states
        self._goto_cache: Dict[Tuple[frozenset, str], List[LRItem]] = {}
        self._state_map: Dict[frozenset, int] = {}
        self.states: List[List[LRItem]] = []

        # Build table
        self._build()

    @staticmethod
    def _state_key(I: Sequence[LRItem]) -> frozenset:
        """Unique immutable key representing an LR(0) item set."""
        return frozenset((item.production.number, item.lr_index) for item in I)

    def _build(self) -> None:
        self.grammar.build_lritems()
        self.grammar.compute_first()
        self.grammar.compute_follow()

        # Step 1: Construct collection of LR(0) items
        self.states = self._lr0_items()

        # Step 2: Add LALR(1) lookaheads to items
        self._add_lalr_lookaheads(self.states)

        # Step 3: Build action and goto tables with conflict resolution
        self._build_parse_table(self.states)

    def _lr0_closure(self, I: List[LRItem]) -> List[LRItem]:
        """Compute LR(0) closure of an item set I."""
        J = I[:]
        added: Set[int] = set()
        didadd = True
        while didadd:
            didadd = False
            for j in J:
                for prod in j.lr_after:
                    if prod.number in added:
                        continue
                    added.add(prod.number)
                    # prod.lr_items[0] is the item with dot at index 0 (B -> . gamma)
                    J.append(prod.lr_items[0])
                    didadd = True
        return J

    def _lr0_goto(self, I: List[LRItem], x: str) -> List[LRItem]:
        """Compute LR(0) goto(I, x)."""
        ik = self._state_key(I)
        cache_key = (ik, x)
        cached = self._goto_cache.get(cache_key)
        if cached is not None:
            return cached

        gs: List[LRItem] = []
        for p in I:
            n = p.lr_next
            if n is not None and n.lr_before == x:
                gs.append(n)

        if gs:
            res = self._lr0_closure(gs)
        else:
            res = []

        self._goto_cache[cache_key] = res
        return res

    def _lr0_items(self) -> List[List[LRItem]]:
        """Construct collection C of LR(0) item sets."""
        start_item = self.grammar.Productions[0].lr_items[0]
        I0 = self._lr0_closure([start_item])
        C = [I0]
        self._state_map = {self._state_key(I0): 0}

        i = 0
        while i < len(C):
            I = C[i]
            i += 1

            # Unique symbols after dot
            symbols: Set[str] = set()
            for item in I:
                if item.lr_index < item.len:
                    symbols.add(item.prod[item.lr_index])

            for x in symbols:
                g = self._lr0_goto(I, x)
                if not g:
                    continue
                gk = self._state_key(g)
                if gk not in self._state_map:
                    self._state_map[gk] = len(C)
                    C.append(g)

        return C

    def _compute_nullable_nonterminals(self) -> Set[str]:
        """Find nonterminals that can derive an empty string."""
        nullable: Set[str] = set()
        num_nullable = 0
        while True:
            for p in self.grammar.Productions[1:]:
                if p.len == 0:
                    nullable.add(p.name)
                    continue
                if all(sym in nullable for sym in p.prod):
                    nullable.add(p.name)
            if len(nullable) == num_nullable:
                break
            num_nullable = len(nullable)
        return nullable

    def _find_nonterminal_transitions(
        self, C: List[List[LRItem]]
    ) -> List[Tuple[int, str]]:
        """Find all transitions on a nonterminal: (state, N)."""
        trans: List[Tuple[int, str]] = []
        seen: Set[Tuple[int, str]] = set()
        for stateno, state in enumerate(C):
            for item in state:
                if item.lr_index < item.len:
                    sym = item.prod[item.lr_index]
                    if sym in self.grammar.Nonterminals:
                        t = (stateno, sym)
                        if t not in seen:
                            seen.add(t)
                            trans.append(t)
        return trans

    def _dr_relation(
        self, C: List[List[LRItem]], trans: Tuple[int, str]
    ) -> List[str]:
        """Direct read relation DR(p, A)."""
        state, N = trans
        terms: List[str] = []
        g = self._lr0_goto(C[state], N)
        for item in g:
            if item.lr_index < item.len:
                a = item.prod[item.lr_index]
                if a in self.grammar.Terminals and a not in terms:
                    terms.append(a)

        # Start symbol transition reads EOF
        if state == 0 and N == self.grammar.Productions[0].prod[0]:
            if EOF_SYMBOL not in terms:
                terms.append(EOF_SYMBOL)
        return terms

    def _reads_relation(
        self,
        C: List[List[LRItem]],
        trans: Tuple[int, str],
        nullable: Set[str],
    ) -> List[Tuple[int, str]]:
        """Compute READS relation: (p, A) READS (j, C)."""
        rel: List[Tuple[int, str]] = []
        state, N = trans
        g = self._lr0_goto(C[state], N)
        j = self._state_map.get(self._state_key(g), -1)
        for item in g:
            if item.lr_index < item.len:
                a = item.prod[item.lr_index]
                if a in nullable:
                    rel.append((j, a))
        return rel

    def _compute_lookback_includes(
        self,
        C: List[List[LRItem]],
        trans: List[Tuple[int, str]],
        nullable: Set[str],
    ) -> Tuple[Dict[Tuple[int, str], List[Tuple[int, LRItem]]], Dict[Tuple[int, str], List[Tuple[int, str]]]]:
        """Compute LOOKBACK and INCLUDES relations."""
        lookdict: Dict[Tuple[int, str], List[Tuple[int, LRItem]]] = {}
        includedict: Dict[Tuple[int, str], List[Tuple[int, str]]] = {}
        dtrans = set(trans)

        for state, N in trans:
            lookb: List[Tuple[int, LRItem]] = []
            for item in C[state]:
                if item.name != N:
                    continue

                # Advance through the production
                curr_item = item
                j = state
                while curr_item.lr_index < curr_item.len:
                    t = curr_item.prod[curr_item.lr_index]
                    if (j, t) in dtrans:
                        # Check if rest of RHS is nullable
                        rest = curr_item.prod[curr_item.lr_index + 1:]
                        if all(s in nullable for s in rest):
                            includedict.setdefault((j, t), []).append((state, N))

                    g = self._lr0_goto(C[j], t)
                    j = self._state_map.get(self._state_key(g), -1)
                    curr_item = curr_item.lr_next  # type: ignore

                # Locate the reduction item in state j
                for r in C[j]:
                    if r.production == item.production and r.lr_index == r.len:
                        lookb.append((j, r))

            lookdict[(state, N)] = lookb

        return lookdict, includedict

    def _add_lalr_lookaheads(self, C: List[List[LRItem]]) -> None:
        """Add LALR(1) lookahead symbols to reduction items."""
        nullable = self._compute_nullable_nonterminals()
        trans = self._find_nonterminal_transitions(C)

        # Read sets
        read_sets = digraph(
            trans,
            lambda x: self._reads_relation(C, x, nullable),
            lambda x: self._dr_relation(C, x),
        )

        # Lookback and Includes
        lookbacks, includes = self._compute_lookback_includes(C, trans, nullable)

        # Follow sets
        follow_sets = digraph(
            trans,
            lambda x: includes.get(x, []),
            lambda x: read_sets.get(x, []),
        )

        # Attach lookaheads to productions
        for t, lb in lookbacks.items():
            f = follow_sets.get(t, [])
            for state, item in lb:
                if state not in item.production.lookaheads:
                    item.production.lookaheads[state] = []
                for a in f:
                    if a not in item.production.lookaheads[state]:
                        item.production.lookaheads[state].append(a)

    def _build_parse_table(self, C: List[List[LRItem]]) -> None:
        """Build LR action and goto tables with conflict resolution."""
        for st, I in enumerate(C):
            st_action: Dict[str, Optional[int]] = {}
            st_action_item: Dict[str, Any] = {}
            st_goto: Dict[str, int] = {}

            # Process reductions & accept
            for item in I:
                if item.lr_index == item.len:
                    if item.name == START_SYMBOL:
                        # Accept on EOF
                        st_action[EOF_SYMBOL] = 0
                        st_action_item[EOF_SYMBOL] = item
                    else:
                        laheads = item.production.lookaheads.get(st, [])
                        for a in laheads:
                            existing = st_action.get(a)
                            if existing is not None:
                                self._resolve_conflict(
                                    st, a, existing, -item.production.number, item, st_action, st_action_item
                                )
                            else:
                                st_action[a] = -item.production.number
                                st_action_item[a] = item
                                item.production.reduced += 1
                else:
                    # Shifts & Gotos
                    sym = item.prod[item.lr_index]
                    g = self._lr0_goto(I, sym)
                    j = self._state_map.get(self._state_key(g), -1)
                    if j >= 0:
                        if sym in self.grammar.Terminals:
                            existing = st_action.get(sym)
                            if existing is not None:
                                self._resolve_conflict(
                                    st, sym, existing, j, item, st_action, st_action_item
                                )
                            else:
                                st_action[sym] = j
                                st_action_item[sym] = item
                        elif sym in self.grammar.Nonterminals:
                            st_goto[sym] = j

            self.action[st] = st_action
            self.goto[st] = st_goto

    def _resolve_conflict(
        self,
        state: int,
        tok: str,
        existing_action: Optional[int],
        new_action: int,
        new_item: LRItem,
        st_action: Dict[str, Optional[int]],
        st_action_item: Dict[str, Any],
    ) -> None:
        """Resolve shift/reduce or reduce/reduce conflicts using operator precedence."""
        if existing_action is None:
            return

        # Case 1: Shift/Reduce conflict
        # existing > 0 is shift, new < 0 is reduce (or vice versa)
        if (existing_action > 0 and new_action < 0) or (existing_action < 0 and new_action > 0):
            if existing_action > 0:
                shift_state = existing_action
                reduce_pnum = -new_action
                shift_item = st_action_item[tok]
                reduce_item = new_item
            else:
                shift_state = new_action
                reduce_pnum = -existing_action
                shift_item = new_item
                reduce_item = st_action_item[tok]

            reduce_prod = self.productions[reduce_pnum]
            sprec, slevel = self.grammar.Precedence.get(tok, ("right", 0))
            rprec, rlevel = reduce_prod.prec

            reason = ""
            if (slevel < rlevel) or (slevel == rlevel and rprec == "left"):
                # Favor Reduce
                st_action[tok] = -reduce_pnum
                st_action_item[tok] = reduce_item
                resolution = "reduce"
                reason = f"Precedence of production {reduce_prod} (level {rlevel}, {rprec}) >= token {tok!r} (level {slevel})"
            elif slevel == rlevel and rprec == "nonassoc":
                # Non-associative: drop action (syntax error)
                st_action[tok] = None
                resolution = "error (nonassoc)"
                reason = f"Token {tok!r} and rule {reduce_prod} are nonassociative"
            else:
                # Favor Shift
                st_action[tok] = shift_state
                st_action_item[tok] = shift_item
                resolution = "shift"
                reason = f"Precedence of token {tok!r} (level {slevel}) > production {reduce_prod} (level {rlevel})"

            record = ConflictRecord(
                kind="shift/reduce",
                state=state,
                token=tok,
                resolution=resolution,
                candidates=[shift_item, reduce_item],
                reason=reason,
            )
            self.conflicts.append(record)
            self.sr_conflicts.append((state, tok, resolution))

        # Case 2: Reduce/Reduce conflict
        elif existing_action < 0 and new_action < 0:
            pnum1 = -existing_action
            pnum2 = -new_action
            prod1 = self.productions[pnum1]
            prod2 = self.productions[pnum2]

            # Favor production defined first (lower line number or number)
            if prod1.line <= prod2.line:
                chosen, rejected = prod1, prod2
                st_action[tok] = -pnum1
            else:
                chosen, rejected = prod2, prod1
                st_action[tok] = -pnum2

            reason = f"Rule ({chosen}) was defined earlier than ({rejected})"
            record = ConflictRecord(
                kind="reduce/reduce",
                state=state,
                token=tok,
                resolution=f"reduce using rule {chosen.number}",
                candidates=[prod1, prod2],
                reason=reason,
            )
            self.conflicts.append(record)
            self.rr_conflicts.append((state, chosen, rejected))
