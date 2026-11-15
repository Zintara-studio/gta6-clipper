"""Upload ready clips through YouTube Studio in ego-lite (no API, no Google Cloud project).

Python picks the clips + US-prime-time publish slots and writes a manifest; uploader/youtube_studio.mjs
runs inside `ego-browser nodejs` and fills the Studio upload dialog for each clip.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from rich.console import Console

from . import db
from .config import OUT, UPLOADER_JS, WORKSPACE

console = Console()


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def day_slots(day: datetime, cfg: dict, tz: ZoneInfo) -> list[datetime]:
    """~daily_target times spread across the window, jittered, on 15-minute marks (Studio's picker)."""
    u = cfg["upload"]
    (h0, m0), (h1, m1) = _hm(u["window"][0]), _hm(u["window"][1])
    start = datetime(day.year, day.month, day.day, h0, m0, tzinfo=tz)
    end = datetime(day.year, day.month, day.day, h1, m1, tzinfo=tz)
    n = max(1, int(cfg["daily_target"]))
    step = (end - start) / n
    out = set()
    for k in range(n):
        t = start + step * (k + random.uniform(0.15, 0.85))
        t = t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0)
        out.add(t)
    return sorted(out)


def next_slots(con, cfg: dict, count: int) -> list[datetime]:
    tz = ZoneInfo(cfg["upload"]["timezone"])
    now = datetime.now(tz)
    earliest = now + timedelta(minutes=45)
    taken = set()
    for r in con.execute("select publish_at from clips where publish_at is not null and status='uploaded'"):
        try:
            taken.add(datetime.fromisoformat(r["publish_at"]).astimezone(tz).replace(second=0, microsecond=0))
        except ValueError:
            pass
    per_day = defaultdict(int)
    for t in taken:
        per_day[t.date()] += 1
    out: list[datetime] = []
    day = now
    for _ in range(30):
        free = max(0, int(cfg["daily_target"]) - per_day[day.date()])
        for t in day_slots(day, cfg, tz):
            if free == 0 or len(out) >= count:
                break
            if t >= earliest and t not in taken:
                out.append(t)
                free -= 1
        if len(out) >= count:
            break
        day = day + timedelta(days=1)
    return out


def interleave(rows) -> list:
    """Best clips first, but never two clips from the same video back to back."""
    groups = defaultdict(list)
    for r in sorted(rows, key=lambda r: -r["score"]):
        groups[r["video_id"]].append(r)
    order = sorted(groups.values(), key=lambda g: -g[0]["score"])
    out = []
    while any(order):
        for g in order:
            if g:
                out.append(g.pop(0))
    return out


def description_for(r, cfg: dict) -> str:
    u = cfg["upload"]
    parts = [r["description"].strip()] if r["description"] else []
    if r["source_name"]:
        parts.append(f"Credit: {r['source_name']} {r['source_url'] if r['source_url'].startswith('http') else ''}".strip())
    parts.append(" ".join(u["hashtags"]))
    return "\n\n".join(parts)


def tags_for(r, cfg: dict) -> str:
    tags = (json.loads(r["tags"]) if r["tags"] else []) + list(cfg["upload"]["default_tags"])
    seen, out, total = set(), [], 0
    for t in tags:
        t = t.strip().replace(",", "").replace("<", "").replace(">", "")
        if t and t.lower() not in seen and total + len(t) + 1 <= 450:
            seen.add(t.lower())
            out.append(t)
            total += len(t) + 1
    return ", ".join(out)


@contextmanager
def upload_lock():
    """Only one uploader may drive Studio at a time (dashboard + terminal runs used to collide)."""
    f = open(WORKSPACE / "upload.lock", "a+")
    try:
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise SystemExit("Another upload is already running (dashboard or terminal). Wait for it to finish.")
    try:
        yield
    finally:
        f.close()  # the OS drops the lock with the handle, also if the process dies


def run_upload(cfg: dict, count: int | None = None, schedule: bool | None = None, check: bool = False,
               dry_run: bool = False) -> None:
    con = db.connect()
    u = cfg["upload"]
    count = 1 if check else (count or int(u["per_run_max"]))
    schedule = u["schedule"] if schedule is None else schedule
    rows = interleave(con.execute("select * from clips where status='ready'").fetchall())[:count]
    if not rows:
        console.print("[yellow]No ready clips. Run `clip harvest` or `clip run <url>` first.[/]")
        return
    slots = next_slots(con, cfg, len(rows)) if schedule else []
    if schedule and len(slots) < len(rows):
        rows = rows[: len(slots)]

    items = []
    for i, r in enumerate(rows):
        item = {"id": r["id"], "file": r["file"], "title": r["title"],
                "description": description_for(r, cfg), "tags": tags_for(r, cfg)}
        if schedule:
            local = slots[i].astimezone()  # Studio's picker uses the computer's own time zone
            item.update(publishAtIso=slots[i].isoformat(),
                        publishDate=local.strftime("%b %d, %Y").replace(" 0", " "),
                        publishTime=local.strftime("%I:%M %p").lstrip("0"),
                        usTime=slots[i].strftime("%a %b %d %I:%M %p %Z"))
        items.append(item)

    run_dir = OUT / "uploads" / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "spaceName": "gta6-clipper uploads",
        "profileId": u.get("ego_profile") or None,
        "pauseSeconds": u["pause_seconds"],
        "checkMode": check,
        "debugDir": str(run_dir),
        "resultsPath": str(run_dir / "results.json"),
        "items": items,
    }
    mpath = run_dir / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    for it in items:
        when = f" -> {it['usTime']}" if schedule else " -> public now"
        console.print(f"  #{it['id']} {it['title']}{when}")
    if dry_run:
        console.print(f"[dim]dry run: manifest at {mpath}[/]")
        return

    with upload_lock():
        _drive_studio(con, items, run_dir, mpath)


def _drive_studio(con, items: list[dict], run_dir, mpath) -> None:
    exe = shutil.which("ego-browser")
    if not exe:
        raise SystemExit("ego-browser not found. Install ego lite (see README) and log into your channel in it.")
    script = f"const MANIFEST_PATH = {json.dumps(str(mpath))};\n" + UPLOADER_JS.read_text(encoding="utf-8")
    t = time.time()
    subprocess.run([exe, "nodejs"], input=script, text=True, encoding="utf-8")

    results_path = run_dir / "results.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else []
    by_id = {it["id"]: it for it in items}
    ok = 0
    for res in results:
        it = by_id.get(res["id"])
        if not it:
            continue
        if res.get("ok"):
            ok += 1
            db.update_clip(con, res["id"], status="uploaded", video_url=res.get("url", ""),
                           publish_at=it.get("publishAtIso"), uploaded_at=time.time(), error=None)
        else:
            db.update_clip(con, res["id"], error=res.get("error", "")[:500])
    console.print(f"[bold]{ok}/{len(items)} uploaded[/] in {(time.time() - t) / 60:.1f} min. Log: {run_dir}")
