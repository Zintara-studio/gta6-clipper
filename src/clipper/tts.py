"""Short US-English voice-over hook with edge-tts (free, no key)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def speak(text: str, voice: str, out: Path) -> Path | None:
    text = text.strip()
    if not text:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    # the edge-tts CLI is more reliable than calling the library in-process
    for _ in range(3):
        p = subprocess.run([sys.executable, "-m", "edge_tts", "--voice", voice, "--rate=+8%", "--text", text,
                            "--write-media", str(out)], capture_output=True, text=True)
        if p.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return out
    print(f"  ! voice hook skipped: {p.stderr.strip()[-200:]}")
    return None
