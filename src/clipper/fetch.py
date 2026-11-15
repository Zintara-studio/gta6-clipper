"""Getting media: probe/list/download with yt-dlp, plus ffmpeg helpers."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

import yt_dlp

YDL_BASE = {"quiet": True, "no_warnings": True, "noprogress": True}


def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("ffmpeg not found. Run setup.sh (Mac) or setup.ps1 (Windows).")
    return exe


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def has_audio(path: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout
    return bool(out.strip())


def is_local(src: str) -> bool:
    return Path(src).expanduser().exists()


def probe(src: str) -> dict:
    """Return {id, title, duration, url, local, upload_date, uploader}."""
    if is_local(src):
        p = Path(src).expanduser().resolve()
        h = hashlib.sha1(f"{p}:{p.stat().st_size}".encode()).hexdigest()[:12]
        return {"id": f"local-{h}", "title": p.stem, "duration": ffprobe_duration(p),
                "url": str(p), "local": True, "uploader": "", "upload_date": ""}
    with yt_dlp.YoutubeDL(YDL_BASE) as ydl:
        info = ydl.extract_info(src, download=False)
    return {
        "id": f"{info.get('extractor_key', 'web').lower()}-{info['id']}",
        "title": info.get("title") or info["id"],
        "duration": float(info.get("duration") or 0),
        "url": info.get("webpage_url") or src,
        "local": False,
        "live": bool(info.get("is_live")),
        "live_status": info.get("live_status") or "",
        "uploader": info.get("uploader") or "",
        "upload_date": info.get("upload_date") or "",
    }


def list_recent(source_url: str, n: int) -> list[dict]:
    """Newest n entries of a channel/VOD list, without downloading."""
    opts = {**YDL_BASE, "extract_flat": "in_playlist", "playlistend": n}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(source_url, download=False)
    out = []
    for e in (info.get("entries") or [])[:n]:
        url = e.get("url") or e.get("webpage_url")
        if not url:
            continue
        out.append({"url": url, "title": e.get("title") or "", "id": e.get("id"),
                    "timestamp": e.get("timestamp") or e.get("release_timestamp"),
                    "upload_date": e.get("upload_date")})
    return out


def _download(url: str, dest: Path, stem: str, fmt: str, ranges=None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    opts = {
        **YDL_BASE,
        "format": fmt,
        "outtmpl": str(dest / f"{stem}.%(ext)s"),
        "merge_output_format": "mp4",
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "overwrites": True,
    }
    if ranges:
        opts["download_ranges"] = yt_dlp.utils.download_range_func(None, ranges)
        opts["force_keyframes_at_cuts"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = sorted(dest.glob(f"{stem}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    files = [f for f in files if f.suffix not in (".part", ".ytdl", ".json")]
    if not files:
        raise RuntimeError(f"download produced no file for {url}")
    return files[0]


VIDEO_FMT = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b"


def download_video(url: str, dest: Path) -> Path:
    return _download(url, dest, "source", VIDEO_FMT)


def download_audio(url: str, dest: Path) -> Path:
    return _download(url, dest, "audio_src", "ba/b")


def download_section(url: str, dest: Path, stem: str, start: float, end: float) -> Path:
    return _download(url, dest, stem, VIDEO_FMT, ranges=[(max(0.0, start), end)])


def download_audio_section(url: str, dest: Path, stem: str, start: float, end: float) -> Path:
    return _download(url, dest, stem, "ba/b", ranges=[(max(0.0, start), end)])


def to_wav(src: Path, wav: Path, max_seconds: float | None = None) -> Path:
    if wav.exists() and wav.stat().st_size > 0:
        return wav
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-i", str(src)]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)]
    subprocess.run(cmd, check=True)
    return wav


def age_days(entry: dict) -> float | None:
    ts = entry.get("timestamp")
    if ts:
        return (time.time() - float(ts)) / 86400
    d = entry.get("upload_date")
    if d and len(d) == 8:
        t = time.mktime(time.strptime(d, "%Y%m%d"))
        return (time.time() - t) / 86400
    return None
