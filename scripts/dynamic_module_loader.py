#!/usr/bin/env python3
"""Load an explicitly selected Python module without process execution."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_module_from_path(module_name: str, script_path: str | Path):
    """Load one module from the exact path selected by the caller."""
    path = Path(script_path).resolve(strict=True)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module_import_failed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
