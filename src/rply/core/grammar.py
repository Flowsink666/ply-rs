"""
Grammar representations and analysis (FIRST/FOLLOW sets, nullable nonterminals).
"""

import inspect
from typing import Callable, Dict, List, Optional, Set, Tuple, Any
from rply.core.symbols import EOF_SYMBOL, ERROR_SYMBOL, START_SYMBOL
from rply.core.production import make_namemap


class GrammarError(Exception):
    """Exception raised for grammar definition issues."""
    pass


def _resolve_action_style(func, arity: int, style: str) -> str:
    if style not in {"auto", "proxy", "values"}:
        raise GrammarError("action_style must be 'auto', 'proxy', or 'values'")
    if style != "auto":
        return style
    if isinstance(func, str):
        return "auto"  # Resolve named actions when bind() supplies the callable.
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return "proxy"
    try:
        signature.bind(None)
    except TypeError:
        try:
            signature.bind(*([None] * arity))
        except TypeError as error:
            raise GrammarError(f"Action cannot accept a proxy or {arity} RHS values") from error
        return "values"
    return "proxy"


class Production:
    """
    Represents a single grammar production rule:
        name -> prod[0] prod[1] ... prod[n-1]
    """

    __slots__ = (
        "number",
        "name",
        "prod",
        "prec",
        "func",
        "action_style",
        "namemap",
        "file",
        "line",
        "len",
        "usyms",
        "reduced",
        "lr_items",
        "lookaheads",
    )

    def __init__(
        self,
        number: int,
        name: str,
        prod: Tuple[str, ...],
        precedence: Tuple[str, int] = ("right", 0),
        func: Optional[Callable[..., Any]] = None,
        file: str = "",
        line: int = 0,
        action_style: str = "auto",
    ):
        self.number = number
        self.name = name
        self.prod = tuple(prod)
        self.prec = precedence
        self.func = func
        self.file = file
        self.line = line
        self.len = len(self.prod)
        self.action_style = _resolve_action_style(func, self.len, action_style)
        self.namemap = make_namemap(self.prod)
        self.usyms = set(self.prod)
        self.reduced = 0
        self.lr_items: List[Any] = []
        self.lookaheads: Dict[int, List[str]] = {}

    def __str__(self) -> str:
        if self.prod:
            rhs = " ".join(self.prod)
        else:
            rhs = "<empty>"
        return f"{self.name} -> {rhs}"

    def __repr__(self) -> str:
        return f"Production({self.number}, {self.name!r}, {self.prod!r})"

    def bind(self, namespace: Dict[str, Any]) -> None:
        """Bind function name or callable to production."""
        if isinstance(self.func, str):
            if self.func in namespace:
                self.func = namespace[self.func]
                self.action_style = _resolve_action_style(self.func, self.len, self.action_style)
            else:
                raise GrammarError(f"Cannot bind rule action {self.func!r}")


class LRItem:
    """
    Represents an LR(0) or LR(1) item:
        A -> alpha . beta
    where lr_index indicates the position of the dot (from 0 to len(prod)).
    """

    __slots__ = (
        "production",
        "name",
        "prod",
        "lr_index",
        "lr_after",
        "lr_before",
        "lr_next",
        "len",
        "usyms",
    )

    def __init__(self, production: Production, lr_index: int):
        self.production = production
        self.name = production.name
        self.prod = production.prod
        self.lr_index = lr_index
        self.len = production.len
        self.usyms = production.usyms
        self.lr_after: List[Production] = []
        self.lr_before: Optional[str] = None
        self.lr_next: Optional["LRItem"] = None

    def __str__(self) -> str:
        # e.g. expr -> expr . PLUS expr
        before = " ".join(self.prod[:self.lr_index])
        after = " ".join(self.prod[self.lr_index:])
        dot = "."
        parts = [p for p in (before, dot, after) if p]
        return f"{self.name} -> {' '.join(parts)}"

    def __repr__(self) -> str:
        return f"LRItem({self.production.number}, dot={self.lr_index})"


class Grammar:
    """
    Stores grammar rules, terminals, non-terminals, operator precedence,
    and calculates FIRST and FOLLOW sets.
    """

    def __init__(self, terminals: Optional[Set[str]] = None):
        self.Productions: List[Production] = []
        self.Prodnames: Dict[str, List[Production]] = {}
        self.Terminals: Set[str] = set(terminals) if terminals else set()
        self.Nonterminals: Set[str] = set()
        self.First: Dict[str, Set[str]] = {}
        self.Follow: Dict[str, Set[str]] = {}
        self.Precedence: Dict[str, Tuple[str, int]] = {}
        self.start: Optional[str] = None

        # Add EOF and error terminals by default
        self.Terminals.add(EOF_SYMBOL)
        self.Terminals.add(ERROR_SYMBOL)

    def set_precedence(self, prec_specs: List[Tuple[str, List[str]]]) -> None:
        """
        Define operator precedences:
        prec_specs: [('left', ['PLUS', 'MINUS']), ('left', ['TIMES', 'DIVIDE']), ...]
        Each subsequent level has a higher precedence level (starting at 1).
        """
        level = 1
        for assoc, tokens in prec_specs:
            if assoc not in ("left", "right", "nonassoc"):
                raise GrammarError(f"Invalid associativity {assoc!r}")
            for tok in tokens:
                if len(tok) >= 2 and ((tok.startswith("'") and tok.endswith("'")) or (tok.startswith('"') and tok.endswith('"'))):
                    tok = tok[1:-1]
                    self.Terminals.add(tok)
                self.Precedence[tok] = (assoc, level)
            level += 1

    def add_production(
        self,
        name: str,
        syms: List[str],
        func: Optional[Callable[..., Any]] = None,
        file: str = "",
        line: int = 0,
        precedence: Optional[str] = None,
        action_style: str = "auto",
    ) -> None:
        """
        Add a production rule to the grammar:
            name -> syms
        """
        if name in self.Terminals:
            raise GrammarError(f"Rule name {name!r} conflicts with terminal symbol")

        self.Nonterminals.add(name)

        # Normalize literal tokens in RHS syms
        cleaned_syms: List[str] = []
        for s in syms:
            if len(s) >= 2 and ((s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"'))):
                lit = s[1:-1]
                self.Terminals.add(lit)
                cleaned_syms.append(lit)
            else:
                cleaned_syms.append(s)
        syms = cleaned_syms

        # Production precedence calculation
        if precedence:
            if len(precedence) >= 2 and ((precedence.startswith("'") and precedence.endswith("'")) or (precedence.startswith('"') and precedence.endswith('"'))):
                precedence = precedence[1:-1]
            if precedence not in self.Precedence:
                raise GrammarError(f"Precedence specified for unknown symbol {precedence!r}")
            prod_prec = self.Precedence[precedence]
        else:
            # Find the rightmost terminal in syms with defined precedence
            prod_prec = ("right", 0)
            for s in reversed(syms):
                if s in self.Precedence:
                    prod_prec = self.Precedence[s]
                    break

        pnumber = len(self.Productions)
        prod = Production(
            number=pnumber,
            name=name,
            prod=tuple(syms),
            precedence=prod_prec,
            func=func,
            file=file,
            line=line,
            action_style=action_style,
        )

        self.Productions.append(prod)
        if name not in self.Prodnames:
            self.Prodnames[name] = []
        self.Prodnames[name].append(prod)

    def set_start(self, start: Optional[str] = None) -> None:
        """
        Set or auto-detect start symbol and insert augmented start rule S' -> start.
        """
        if start is None:
            if not self.Productions:
                raise GrammarError("Cannot determine start symbol: No productions defined")
            start = self.Productions[0].name

        if start not in self.Nonterminals:
            raise GrammarError(f"Start symbol {start!r} is not a valid nonterminal")

        self.start = start

        # Augmented start rule 0: S' -> start
        augmented = Production(
            number=0,
            name=START_SYMBOL,
            prod=(start,),
            precedence=("right", 0),
            func=None,
        )

        # Insert at index 0 and renumber
        self.Productions.insert(0, augmented)
        for i, p in enumerate(self.Productions):
            p.number = i

        self.Nonterminals.add(START_SYMBOL)
        self.Prodnames[START_SYMBOL] = [augmented]

    def build_lritems(self) -> None:
        """Build LR items for each production."""
        for p in self.Productions:
            lr_items: List[LRItem] = []
            for i in range(len(p.prod) + 1):
                item = LRItem(p, i)
                # Next symbol after the dot
                if i < len(p.prod):
                    next_sym = p.prod[i]
                    item.lr_after = self.Prodnames.get(next_sym, [])
                else:
                    item.lr_after = []

                if i > 0:
                    item.lr_before = p.prod[i - 1]
                else:
                    item.lr_before = None

                lr_items.append(item)

            # Link items sequentially
            for i in range(len(lr_items) - 1):
                lr_items[i].lr_next = lr_items[i + 1]
            lr_items[-1].lr_next = None

            p.lr_items = lr_items

    def compute_first(self) -> Dict[str, Set[str]]:
        """Compute the FIRST sets for all grammar symbols."""
        if self.First:
            return self.First

        first: Dict[str, Set[str]] = {}
        for t in self.Terminals:
            first[t] = {t}
        for nt in self.Nonterminals:
            first[nt] = set()

        changed = True
        while changed:
            changed = False
            for p in self.Productions[1:]:  # skip augmented rule
                rhs_first = self._compute_sequence_first(p.prod, first)
                old_len = len(first[p.name])
                first[p.name].update(rhs_first)
                if len(first[p.name]) > old_len:
                    changed = True

        self.First = first
        return first

    def _compute_sequence_first(
        self, seq: Tuple[str, ...], first_map: Dict[str, Set[str]]
    ) -> Set[str]:
        """Compute FIRST of a sequence of symbols."""
        if not seq:
            return {""}  # empty derives epsilon (represented as empty string)

        res: Set[str] = set()
        for sym in seq:
            sym_first = first_map.get(sym, {sym})
            res.update(sym_first - {""})
            if "" not in sym_first:
                break
        else:
            res.add("")
        return res

    def compute_follow(self) -> Dict[str, Set[str]]:
        """Compute the FOLLOW sets for all nonterminals."""
        if self.Follow:
            return self.Follow

        first = self.compute_first()
        follow: Dict[str, Set[str]] = {nt: set() for nt in self.Nonterminals}

        if self.start:
            follow[self.start].add(EOF_SYMBOL)
        follow[START_SYMBOL].add(EOF_SYMBOL)

        changed = True
        while changed:
            changed = False
            for p in self.Productions:
                for i, sym in enumerate(p.prod):
                    if sym in self.Nonterminals:
                        rest = p.prod[i + 1:]
                        rest_first = self._compute_sequence_first(rest, first)

                        old_len = len(follow[sym])
                        follow[sym].update(rest_first - {""})
                        if "" in rest_first or not rest:
                            follow[sym].update(follow[p.name])

                        if len(follow[sym]) > old_len:
                            changed = True

        self.Follow = follow
        return follow

    def validate(self) -> List[str]:
        """
        Validate grammar for undefined symbols, unreachable symbols, etc.
        Returns a list of warning messages.
        """
        warnings: List[str] = []
        for p in self.Productions:
            for sym in p.prod:
                if sym not in self.Terminals and sym not in self.Nonterminals:
                    # Could be undefined token or undefined rule
                    warnings.append(
                        f"Rule {p.name!r} contains unknown symbol {sym!r} at {p.file}:{p.line}"
                    )
        return warnings
