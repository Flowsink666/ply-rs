"""Load the required native engine; auto is an alias for rust."""

import os
from importlib import import_module

BACKEND = os.environ.get("PLY_RS_BACKEND", "auto").lower()
if BACKEND == "python":
    raise ValueError("PLY_RS_BACKEND=python is no longer supported; ply-rs uses the native Rust engine. Use rust or auto.")
if BACKEND not in {"auto", "rust"}:
    raise ValueError("PLY_RS_BACKEND must be 'auto' or 'rust'")

try:
    native_core = import_module("ply_rs._core_rs")
except ImportError as error:
    raise ImportError(
        "ply-rs requires the ply_rs._core_rs native extension; install a compatible wheel "
        "or build it with maturin develop --release"
    ) from error

if not all(hasattr(native_core, name) for name in
           ("ParserControl", "TokenCursor", "_ProductionView", "_ReductionData")):
    raise ImportError("ply_rs._core_rs is outdated; rebuild it with maturin develop --release")
