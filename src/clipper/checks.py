"""Environment checks shared by `clip doctor` and the dashboard."""
from __future__ import annotations

import shutil

from .config import ROOT, load_config, resolve

MUSIC_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"}


def run_checks() -> dict:
    cfg = load_config()
    music_dir = resolve(cfg["render"]["music_dir"])
    music = [p for p in music_dir.glob("*") if p.suffix.lower() in MUSIC_EXT] if music_dir.exists() else []
    items = [
        {"name": "ffmpeg", "ok": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")), "required": True},
        {"name": f"agent: {cfg['agent']}", "ok": bool(shutil.which(cfg["agent"])), "required": True},
        {"name": "node", "ok": bool(shutil.which("node")), "required": True},
        {"name": "Remotion editor", "ok": (ROOT / "editor/node_modules/remotion").exists(),
         "required": True, "hint": "run: cd editor && npm install"},
        {"name": "ego-browser (uploads)", "ok": bool(shutil.which("ego-browser")), "required": True},
        {"name": "caption font", "ok": (ROOT / "editor/public/Anton-Regular.ttf").exists(), "required": True},
        {"name": f"music tracks: {len(music)}", "ok": bool(music), "required": False,
         "hint": "drop no-copyright tracks in assets/music/"},
    ]
    return {"items": items, "ok": all(i["ok"] for i in items if i["required"]), "agent": cfg["agent"],
            "music": len(music), "config_exists": (ROOT / "config.yaml").exists()}
