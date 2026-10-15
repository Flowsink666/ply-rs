"""Independent graph reachability reference."""


def closure_by_fixed_point(edges, initial):
    """Specification: keep adding reachable labels until no set changes."""
    result = {node: set(labels) for node, labels in initial.items()}
    changed = True
    while changed:
        changed = False
        for node, successors in edges.items():
            updated = result[node].union(*(result[child] for child in successors))
            if updated != result[node]:
                result[node] = updated
                changed = True
    return result
