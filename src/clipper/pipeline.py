"""One video in -> several ready-to-upload Shorts out."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from rich.console import Console

from . import db, fetch, pick, render, signals, transcribe, tts
from .config import JOBS

console = Console()
LONG_VIDEO = 20 * 60  # longer than this: transcribe from audio only, download just the picked sections


def slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:n] or "video"


def guess_type(meta: dict) -> str:
    t = meta["title"].lower()
    if "trailer" in t:
        return "trailer"
    if "twitch" in meta["id"]:
        return "stream"
    if any(k in t for k in ("news", "leak", "update", "release", "everything we know")):
        return "news"
    return "reaction" if not meta["local"] else "gameplay"


def default_layout(source_type: str) -> str:
    return "crop" if source_type in ("stream", "gameplay") else "blur"


def process(src: str, cfg: dict, source: dict | None = None, source_type: str | None = None,
            layout: str | None = None, agent: str | None = None, force: bool = False) -> list[int]:
    source = source or {}
    con = db.connect()
    meta = fetch.probe(src)
    vid = meta["id"]
    if meta.get("live"):
        console.print(f"[yellow]skip (still live):[/] {meta['title']}")
        return []
    status = db.video_status(con, vid)
    if status == "done" and not force:
        console.print(f"[dim]already processed:[/] {meta['title']}")
        return []

    source_type = source_type or source.get("type") or guess_type(meta)
    layout = layout or source.get("layout") or default_layout(source_type)
    facecam = source.get("facecam")
    if layout == "facecam" and not facecam:
        layout = "crop"

    job = JOBS / f"{slug(meta['title'])}_{vid}"
    job.mkdir(parents=True, exist_ok=True)
    (job / "meta.json").write_text(json.dumps({**meta, "source_type": source_type, "layout": layout,
                                               "source": source}, indent=2), encoding="utf-8")
    db.set_video(con, vid, source_name=source.get("name", ""), source_type=source_type,
                 url=meta["url"], title=meta["title"], status="processing")
    console.rule(f"[bold]{meta['title'][:70]}")
    console.print(f"type={source_type} layout={layout} duration={meta['duration'] / 60:.1f} min")

    try:
        long_remote = not meta["local"] and meta["duration"] > LONG_VIDEO
        full_video: Path | None = None
        max_s = float(cfg["vod_max_minutes"]) * 60

        t = time.time()
        if meta["local"]:
            full_video = Path(meta["url"])
            audio_src = full_video
        elif long_remote:
            audio_src = next(iter(job.glob("audio_src.*")), None) or fetch.download_audio(meta["url"], job)
        else:
            full_video = next(iter(job.glob("source.mp4")), None) or fetch.download_video(meta["url"], job)
            audio_src = full_video
        wav = fetch.to_wav(audio_src, job / "audio.wav", max_s)
        console.print(f"  fetched ({time.time() - t:.0f}s)")

        t = time.time()
        tr = transcribe.transcribe(wav, job / "transcript.json", cfg)
        sig = signals.compute(wav, job / "signals.json")
        console.print(f"  transcribed {len(tr['words'])} words, {len(sig['peaks'])} loud moments ({time.time() - t:.0f}s)")

        avoid = [(r["start"], r["end"]) for r in db.clips_for_video(con, vid)]
        try:
            picks = pick.pick(job, meta, source_type, tr, sig, cfg, avoid, agent)
        except pick.ManualPick:
            db.set_video(con, vid, status="needs_picks")
            console.print(f"[yellow]  prompts written to {job}. Write picks_raw.json there, then run "
                          f"`clip render \"{job}\"`.[/]")
            return []
        return render_picks(con, job, meta, picks, tr, sig, source_type, layout, facecam, full_video, cfg, source)
    except Exception as e:
        db.set_video(con, vid, status="failed", error=str(e)[:1000])
        console.print(f"  failed: {e}", markup=False)
        return []


def render_picks(con, job: Path, meta: dict, picks: list[dict], tr: dict, sig: dict, source_type: str,
                 layout: str, facecam, full_video: Path | None, cfg: dict, source: dict) -> list[int]:
    vid = meta["id"]
    console.print(f"  {len(picks)} clips picked, editing in Remotion...")
    clips_dir = job / "clips"
    jobs: list[render.ClipJob] = []
    for i, c in enumerate(picks, 1):
        s, e = float(c["start"]), float(c["end"])
        name = f"clip_{i:02d}"
        cdir = clips_dir / name
        try:
            if full_video is not None:
                src, t0 = full_video, s
            else:
                pad = 2.0
                sec_start = max(0.0, s - pad)
                src = fetch.download_section(meta["url"], job / "sections", name, sec_start, e + pad)
                t0 = s - sec_start
            words = [{"w": w["w"], "s": round(w["s"] - s, 2), "e": round(w["e"] - s, 2)}
                     for w in tr["words"] if s <= w["s"] < e]
            voice = None
            if source_type in cfg["render"]["voice_hook_types"] and c.get("voice_hook"):
                voice = tts.speak(c["voice_hook"], cfg["render"]["tts_voice"], cdir / "voice.mp3")
                if voice:  # caption the voice line (evenly timed), drop source words spoken underneath it
                    vd = fetch.ffprobe_duration(voice) + 0.15
                    vw = c["voice_hook"].split()
                    step = (vd - 0.15) / max(1, len(vw))
                    spoken = [{"w": x, "s": round(0.15 + k * step, 2), "e": round(0.15 + (k + 1) * step, 2)}
                              for k, x in enumerate(vw)]
                    words = spoken + [w for w in words if w["s"] > vd]
            punches = [round(p["t"] - s, 2) for p in sig.get("peaks", []) if s + 0.5 <= p["t"] < e - 0.5]
            jobs.append(render.ClipJob(name=name, dir=cdir, src=src, t0=t0, dur=e - s, words=words,
                                       hook=c.get("hook", ""), layout=layout, facecam=facecam,
                                       punches=punches, voice=voice))
        except Exception as ex:
            console.print(f"  ✗ {name}: {ex}", markup=False)

    t = time.time()
    render.render_batch(jobs, cfg)
    ids = []
    by_name = {f"clip_{i:02d}": c for i, c in enumerate(picks, 1)}
    for j in jobs:
        c = by_name[j.name]
        if j.error or not j.out:
            console.print(f"  ✗ {j.name}: {j.error}", markup=False)
            continue
        clip_id = db.add_clip(
            con, video_id=vid, job_dir=str(job), source_name=source.get("name", meta.get("uploader", "")),
            source_type=source_type, source_url=meta["url"], start=float(c["start"]), end=float(c["end"]),
            score=c["score"], title=c["title"], description=c.get("description", ""), tags=c.get("tags", []),
            hook=c.get("hook", ""), file=str(j.out), status="ready")
        ids.append(clip_id)
        console.print(f"  ✓ #{clip_id} {c['score']:.0f}/10  {j.dur:4.1f}s  {c['title']}", markup=False)
    console.print(f"  rendered {len(ids)}/{len(jobs)} in {time.time() - t:.0f}s")
    db.set_video(con, vid, status="done", error=None)
    return ids
