"""Edit = ffmpeg (cut + audio mix) -> Remotion (visuals: layout, captions, hook, zoom punches) -> ffmpeg (mux).

Remotion renders a whole batch in one Node process so the project is bundled once per video.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import ROOT, WORKSPACE, resolve
from .fetch import ffmpeg_bin, ffprobe_duration, has_audio
from .progress import emit

EDITOR = ROOT / "editor"
MUSIC_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"}
ACCENTS = {"yellow": "#FFE600", "green": "#3CFF3C", "red": "#FF3B3B", "cyan": "#00E5FF", "pink": "#FF4FD8"}


@dataclass
class ClipJob:
    name: str
    dir: Path              # workspace/jobs/<job>/clips/<name>/
    src: Path
    t0: float
    dur: float
    words: list[dict]
    hook: str
    layout: str
    facecam: list[float] | None
    punches: list[float] = field(default_factory=list)
    voice: Path | None = None
    out: Path | None = None    # final mp4
    error: str | None = None


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed: {p.stderr[-1500:]}")


def _dims(path: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


def pick_music(cfg: dict) -> Path | None:
    d = resolve(cfg["render"]["music_dir"])
    tracks = [p for p in d.glob("*") if p.suffix.lower() in MUSIC_EXT] if d.exists() else []
    return random.choice(tracks) if tracks else None


def cut_segment(j: ClipJob) -> Path:
    """Short, seek-friendly video-only copy of just this clip (what Remotion reads)."""
    seg = j.dir / "segment.mp4"
    _run([ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{j.t0:.3f}", "-t", f"{j.dur:.3f}", "-i", str(j.src),
          "-vf", "scale='if(gt(iw,ih),min(1920,iw),-2)':'if(gt(iw,ih),-2,min(1920,ih))',fps=30",
          "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-g", "15", "-pix_fmt", "yuv420p",
          str(seg)])
    return seg


def mix_audio(j: ClipJob, cfg: dict) -> Path:
    """Source audio (+ voice hook, ducked under it) + music ducked under speech, normalised to -14 LUFS."""
    out = j.dir / "audio.m4a"
    fmt = "aformat=sample_rates=48000:channel_layouts=stereo"
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{j.t0:.3f}", "-t", f"{j.dur:.3f}", "-i", str(j.src)]
    idx, music_i, voice_i = 1, None, None
    music = pick_music(cfg)
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        music_i, idx = idx, idx + 1
    if j.voice:
        cmd += ["-i", str(j.voice)]
        voice_i, idx = idx, idx + 1

    parts = [f"[0:a]{fmt}[orig0]" if has_audio(j.src) else f"anullsrc=r=48000:cl=stereo,atrim=0:{j.dur:.3f}[orig0]"]
    cur = "orig0"
    if voice_i is not None:
        vd = min(ffprobe_duration(j.voice) + 0.3, j.dur)
        parts += [f"[orig0]volume='if(lt(t,{vd:.2f}),0.25,1)':eval=frame[origd]",
                  f"[{voice_i}:a]{fmt},adelay=150|150,volume=1.7[vo]",
                  "[origd][vo]amix=inputs=2:duration=first:normalize=0[voice]"]
        cur = "voice"
    if music_i is not None:
        parts += [f"[{music_i}:a]{fmt},volume={float(cfg['render']['music_volume_db'])}dB[mus]",
                  f"[{cur}]asplit=2[vmain][vkey]",
                  "[mus][vkey]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[duck]",
                  "[vmain][duck]amix=inputs=2:duration=first:normalize=0[mix]"]
        cur = "mix"
    parts.append(f"[{cur}]loudnorm=I=-14:TP=-1.5:LRA=11,{fmt}[aout]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[aout]", "-t", f"{j.dur:.3f}",
            "-c:a", "aac", "-b:a", "160k", str(out)]
    _run(cmd)
    return out


def _node() -> str:
    exe = shutil.which("node")
    if not exe:
        raise SystemExit("Node.js not found (needed for Remotion). Run setup.sh / setup.ps1.")
    if not (EDITOR / "node_modules").exists():
        _run([shutil.which("npm") or "npm", "install", "--no-audit", "--no-fund"], cwd=EDITOR)
    return exe


def render_batch(jobs: list[ClipJob], cfg: dict) -> list[ClipJob]:
    r = cfg["render"]
    items = []
    for j in jobs:
        try:
            j.dir.mkdir(parents=True, exist_ok=True)
            seg = cut_segment(j)
            mix_audio(j, cfg)
            sw, sh = _dims(seg)
            items.append({
                "out": str(j.dir / "visual.mp4"),
                "props": {
                    "videoPath": seg.relative_to(WORKSPACE).as_posix(),
                    "srcWidth": sw, "srcHeight": sh, "duration": round(j.dur, 3), "fps": 30,
                    "layout": j.layout, "facecam": j.facecam, "words": j.words, "hook": j.hook,
                    "accent": ACCENTS.get(random.choice(r["caption_presets"]), "#FFE600"),
                    "captionStyle": random.choice(["stroke", "box"]),
                    "wordsPerGroup": int(r["words_per_caption"]), "punches": j.punches,
                },
            })
        except Exception as e:
            j.error = str(e)
    if not items:
        return jobs

    batch = jobs[0].dir.parent / "remotion_batch.json"
    batch.write_text(json.dumps({"root": str(WORKSPACE), "crf": int(r["crf"]), "preset": r["preset"],
                                 "items": items}), encoding="utf-8")
    proc = subprocess.Popen([_node(), str(EDITOR / "render.mjs"), str(batch)], cwd=EDITOR,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    results, other = {}, []
    emit("render", f"0/{len(items)}")
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
                results[d["out"]] = d
                emit("render", f"{len(results)}/{len(items)}")
                continue
            except json.JSONDecodeError:
                pass
        if line and not line.startswith("Downloading"):
            other.append(line)
    proc.wait()
    stderr_tail = "\n".join(other[-20:])

    for j in jobs:
        if j.error:
            continue
        res = results.get(str(j.dir / "visual.mp4"))
        if not res or not res.get("ok"):
            j.error = (res or {}).get("error") or f"remotion failed: {stderr_tail[-1200:]}"
            continue
        final = j.dir.parent / f"{j.name}.mp4"
        try:
            _run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(j.dir / "visual.mp4"), "-i", str(j.dir / "audio.m4a"),
                  "-map", "0:v", "-map", "1:a", "-c", "copy", "-shortest", "-movflags", "+faststart", str(final)])
            j.out = final
            if os.environ.get("CLIPPER_KEEP_TEMP") != "1":
                for f in ("segment.mp4", "visual.mp4"):
                    (j.dir / f).unlink(missing_ok=True)
        except Exception as e:
            j.error = str(e)
    return jobs
