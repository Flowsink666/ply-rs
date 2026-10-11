"""Adapters between Python grammar objects and the native extension."""

from typing import Dict, List, Tuple
from rply.backends import native_core
from rply.core.conflicts import ConflictRecord


def build_table(table) -> None:
    """Populate a public table from the native grammar representation."""
    rs_g = native_core.Grammar(list(table.grammar.Terminals))
    if table.grammar.Precedence:
        level_map: Dict[int, Tuple[str, List[str]]] = {}
        for tok, (assoc, lvl) in table.grammar.Precedence.items():
            if lvl not in level_map:
                level_map[lvl] = (assoc, [])
            level_map[lvl][1].append(tok)
        prec_specs = [level_map[lvl] for lvl in sorted(level_map.keys())]
        rs_g.set_precedence(prec_specs)

    for p in table.grammar.Productions[1:]:  # skip augmented rule S' -> start
        prec_val = p.prec if p.prec != ("right", 0) else None
        rs_g.add_production(p.name, list(p.prod), p.line, None, prec_val)

    rs_g.set_start(table.grammar.start)
    rs_table = rs_g.build_table()

    table.action = rs_table.action
    table.goto = rs_table.goto
    table.sr_conflicts = rs_table.sr_conflicts
    table.rr_conflicts = []
    for st, c1, c2 in rs_table.rr_conflicts:
        table.rr_conflicts.append((st, table.productions[c1], table.productions[c2]))

    table.conflicts = []
    for c in rs_table.conflicts:
        table.conflicts.append(
            ConflictRecord(
                kind=c["kind"],
                state=int(c["state"]),
                token=c["token"],
                resolution=c["resolution"],
                candidates=[],
                reason=c["reason"],
            )
        )

    table.states = [[] for _ in range(rs_table.num_states)]
    # Keep the native runtime table for subsequent parser instances. Snapshots
    # detect edits to the public dictionaries before opting into shared storage.
    table._rs_table = rs_table
    table._native_action_snapshot = {state: row.copy() for state, row in table.action.items()}
    table._native_goto_snapshot = {state: row.copy() for state, row in table.goto.items()}
