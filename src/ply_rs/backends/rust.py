"""Adapters between Python grammar objects and the native extension."""

from typing import Dict, List, Tuple
from ply_rs.core.conflicts import ConflictRecord
from ply_rs.core.table_data import ParseTableData, TableBuildResult, native_rows


class RustTableArtifact:
    """Own the native table and snapshots used to validate shared runtime rows."""

    def __init__(self, module, native_table, data):
        self.module = module
        self.native_table = native_table
        self.action_snapshot = {state: row.copy() for state, row in data.action.items()}
        self.goto_snapshot = {state: row.copy() for state, row in data.goto.items()}

    def create_parser(self, data, metadata):
        if (data.num_states == self.native_table.num_states
                and data.action == self.action_snapshot and data.goto == self.goto_snapshot):
            return self.module.LRParserEngine.from_table(self.native_table, *metadata)
        return self.module.LRParserEngine(data.num_states, native_rows(data.action), native_rows(data.goto), *metadata)


def build_table(grammar, module) -> TableBuildResult:
    """Return native build results without mutating a public table object."""
    rs_g = module.Grammar(list(grammar.Terminals))
    if grammar.Precedence:
        level_map: Dict[int, Tuple[str, List[str]]] = {}
        for tok, (assoc, lvl) in grammar.Precedence.items():
            if lvl not in level_map:
                level_map[lvl] = (assoc, [])
            level_map[lvl][1].append(tok)
        # Empty tiers preserve numeric levels after repeated declarations or
        # intentionally empty entries; compacting them changes shift priority.
        prec_specs = [level_map.get(lvl, ("right", []))
                      for lvl in range(1, max(level_map) + 1)]
        rs_g.set_precedence(prec_specs)

    for p in grammar.Productions[1:]:  # skip augmented rule S' -> start
        # Grammar registration has already resolved precedence. Level zero is
        # a valid result, not a request for the native backend to infer it.
        rs_g.add_production(p.name, list(p.prod), p.line, None, p.prec)

    rs_g.set_start(grammar.start)
    rs_table = rs_g.build_table()
    grammar.build_lritems()
    grammar.First = rs_g.first
    grammar.Follow = rs_g.follow
    lookaheads = [{} for _ in grammar.Productions]
    for (state, number), values in rs_table.lookaheads.items():
        lookaheads[number][state] = values
    for production, current in zip(grammar.Productions, lookaheads):
        for state in list(production.lookaheads):
            if state not in current:
                del production.lookaheads[state]
        for state, values in current.items():
            production.lookaheads.setdefault(state, [])[:] = values
        production.reduced = 0
    action = rs_table.action
    for row in action.values():
        for value in row.values():
            if value is not None and value < 0:
                grammar.Productions[-value].reduced += 1

    rr_conflicts = []
    for st, c1, c2 in rs_table.rr_conflicts:
        rr_conflicts.append((st, grammar.Productions[c1], grammar.Productions[c2]))

    conflicts = []
    for c in rs_table.conflicts:
        conflicts.append(
            ConflictRecord(
                kind=c["kind"],
                state=int(c["state"]),
                token=c["token"],
                resolution=c["resolution"],
                candidates=[grammar.Productions[number] if c["kind"] == "reduce/reduce"
                            else grammar.Productions[number].lr_items[dot]
                            for number, dot in c["candidates"]],
                reason=c["reason"],
            )
        )

    data = ParseTableData(
        action, rs_table.goto, grammar.Productions,
        [[grammar.Productions[number].lr_items[dot] for number, dot in state]
         for state in rs_table.states], conflicts, rs_table.sr_conflicts, rr_conflicts,
    )
    return TableBuildResult(data, RustTableArtifact(module, rs_table, data))
