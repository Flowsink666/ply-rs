"""Load the required native engine; auto is an alias for rust."""

import os
from importlib import import_module

BACKEND = os.environ.get("RPLY_BACKEND", "auto").lower()
if BACKEND == "python":
    raise ValueError("RPLY_BACKEND=python is no longer supported; rply uses the native Rust engine. Use rust or auto.")
if BACKEND not in {"auto", "rust"}:
    raise ValueError("RPLY_BACKEND must be 'auto' or 'rust'")

try:
    native_core = import_module("rply._core_rs")
except ImportError as error:
    raise ImportError(
        "rply requires the rply._core_rs native extension; install a compatible wheel "
        "or build it with maturin develop --release"
    ) from error

if not all(hasattr(native_core, name) for name in
           ("ParserControl", "TokenCursor", "_ProductionView", "_ReductionData")):
    raise ImportError("rply._core_rs is outdated; rebuild it with maturin develop --release")
