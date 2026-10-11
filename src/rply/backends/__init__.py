"""Backend selection shared by the public engine facades.

Set RPLY_BACKEND to auto (default), python, or rust before importing rply.
Explicit rust mode fails instead of silently switching implementations.
"""

import os
import warnings
from importlib import import_module

BACKEND = os.environ.get("RPLY_BACKEND", "auto").lower()
if BACKEND not in {"auto", "python", "rust"}:
    raise ValueError("RPLY_BACKEND must be 'auto', 'python', or 'rust'")

native_core = None
if BACKEND != "python":
    try:
        native_core = import_module("rply._core_rs")
    except ImportError:
        if BACKEND == "rust":
            raise ImportError(
                "RPLY_BACKEND=rust requires the rply._core_rs extension; "
                "build it with maturin develop --release"
            ) from None

HAS_RUST_CORE = native_core is not None


def fallback(component: str, error: Exception) -> None:
    """Report a native setup failure, or reject it in explicit rust mode."""
    if BACKEND == "rust":
        raise RuntimeError(f"Rust {component} initialization failed") from error
    warnings.warn(
        f"Rust {component} unavailable; using Python: {error}",
        RuntimeWarning,
        stacklevel=2,
    )
