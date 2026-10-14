"""Backend-independent parse table conflict records."""

from typing import Any, List


class ConflictRecord:
    """Detailed record of an LR parsing conflict for rich diagnostics."""

    def __init__(
        self,
        kind: str,  # 'shift/reduce' or 'reduce/reduce'
        state: int,
        token: str,
        resolution: str,  # 'shift' or 'reduce'
        candidates: List[Any],
        reason: str = "",
    ):
        self.kind = kind
        self.state = state
        self.token = token
        self.resolution = resolution
        self.candidates = candidates
        self.reason = reason

    def __repr__(self) -> str:
        return f"<Conflict {self.kind} state={self.state} token={self.token} -> {self.resolution}>"
