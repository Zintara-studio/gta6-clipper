"""Machine-readable progress lines for the dashboard (only printed when CLIPPER_PROGRESS=1)."""
from __future__ import annotations

import os
import sys


def emit(kind: str, value) -> None:
    if os.environ.get("CLIPPER_PROGRESS") == "1":
        sys.stdout.write(f"::{kind}::{value}\n")
        sys.stdout.flush()
