from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "workspace"
JOBS = WORKSPACE / "jobs"
OUT = WORKSPACE / "out"
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
UPLOADER_JS = ROOT / "uploader" / "youtube_studio.mjs"

DEFAULTS: dict = {
    "creator": "me",
    "agent": "claude",
    "agent_model": "",
    "whisper": {"backend": "auto", "model": "auto", "language": "en"},
    "daily_target": 25,
    "clips_per_video_max": 8,
    "min_score": 6,
    "vod_max_minutes": 240,
    "clip_length": {
        "stream": [15, 55],
        "gameplay": [15, 55],
        "reaction": [15, 50],
        "news": [15, 45],
        "trailer": [8, 25],
    },
    "render": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "caption_presets": ["yellow", "green", "red", "cyan", "pink"],
        "words_per_caption": 3,
        "music_dir": "assets/music",
        "music_volume_db": -18,
        "voice_hook_types": ["trailer", "news"],
        "tts_voice": "en-US-AndrewNeural",
        "crf": 21,
        "preset": "veryfast",
    },
    "upload": {
        "timezone": "America/New_York",
        "window": ["10:00", "23:00"],
        "schedule": True,
        "per_run_max": 15,
        "pause_seconds": [20, 60],
        "default_tags": ["gta 6", "gta vi", "gta6", "grand theft auto 6", "gaming"],
        "hashtags": ["#gta6", "#shorts", "#gaming"],
        "ego_profile": "",
    },
}

SOURCE_TYPES = ("stream", "gameplay", "reaction", "news", "trailer")


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path or os.environ.get("CLIPPER_CONFIG", ROOT / "config.yaml"))
    cfg = copy.deepcopy(DEFAULTS)
    if path.exists():
        _merge(cfg, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    return cfg


def load_sources(path: str | Path | None = None) -> dict:
    path = Path(path or ROOT / "sources.yaml")
    if not path.exists():
        return {"sources": [], "max_age_days": 3, "per_source_new_videos": 2}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("sources", [])
    data.setdefault("max_age_days", 3)
    data.setdefault("per_source_new_videos", 2)
    return data


def resolve(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else ROOT / p
