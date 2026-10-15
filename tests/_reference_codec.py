"""Load hardware/imu_capture.py as a module without needing it on sys.path.

`dataclasses` resolves annotations via `sys.modules[cls.__module__]`, so a module
built by hand with importlib must be registered in `sys.modules` BEFORE
`exec_module` runs — otherwise @dataclass raises AttributeError on the first
annotated field.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REFERENCE_CODEC = REPO / "hardware" / "imu_capture.py"


def load_reference():
    name = "imu_capture_reference"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REFERENCE_CODEC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
