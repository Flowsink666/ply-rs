"""
Rich conflict and grammar diagnostic reporting.
"""

from typing import List, Optional
from ply_rs.core.lr_table import LRTable, ConflictRecord
from ply_rs.core.grammar import Grammar


class DiagnosticsFormatter:
    """Formats parser generator conflicts and warnings into human-friendly diagnostics."""

    @staticmethod
    def format_conflicts(table: LRTable) -> str:
        if not table.conflicts:
            return "No grammar conflicts detected."

        lines: List[str] = [
            f"=== Grammar Analysis: {len(table.conflicts)} conflict(s) found ===",
            "",
        ]

        for i, c in enumerate(table.conflicts, 1):
            lines.append(f"[{i}] {c.kind.upper()} conflict in State {c.state} on token {c.token!r}:")
            lines.append(f"    Resolution: {c.resolution.upper()}")
            if c.reason:
                lines.append(f"    Reason    : {c.reason}")

            lines.append("    Candidates:")
            for cand in c.candidates:
                if hasattr(cand, "production"):
                    prod = cand.production
                    lines.append(f"      - Item: {cand} (Rule {prod.number})")
                    if prod.file:
                        lines.append(f"        Defined at: {prod.file}:{prod.line}")
                elif hasattr(cand, "number"):
                    lines.append(f"      - Rule {cand.number}: {cand}")
                    if cand.file:
                        lines.append(f"        Defined at: {cand.file}:{cand.line}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def format_grammar_summary(grammar: Grammar, table: LRTable) -> str:
        lines: List[str] = [
            f"Grammar Summary:",
            f"  - Nonterminals: {len(grammar.Nonterminals)}",
            f"  - Terminals   : {len(grammar.Terminals)}",
            f"  - Productions : {len(grammar.Productions)}",
            f"  - LR(0) States: {len(table.states)}",
            f"  - Conflicts   : {len(table.conflicts)} (S/R: {len(table.sr_conflicts)}, R/R: {len(table.rr_conflicts)})",
        ]
        return "\n".join(lines)
