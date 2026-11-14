"""`clip` command line."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import db, fetch, pick, pipeline
from .config import ROOT, SOURCE_TYPES, load_config, load_sources

app = typer.Typer(add_completion=False, no_args_is_help=True, help="GTA 6 Shorts clipping engine.")
console = Console()


@app.command()
def init():
    """Create config.yaml / sources.yaml and check that every tool is installed."""
    for name in ("config", "sources"):
        dst, src = ROOT / f"{name}.yaml", ROOT / f"{name}.example.yaml"
        if not dst.exists():
            shutil.copy(src, dst)
            console.print(f"created {dst.name}: edit it")
    doctor()


@app.command()
def doctor():
    """Check ffmpeg, your agent CLI, node/Remotion, ego-browser, font and music."""
    from .checks import run_checks

    res = run_checks()
    for i in res["items"]:
        mark = "[green]✓[/]" if i["ok"] else ("[red]✗[/]" if i["required"] else "[yellow]![/]")
        console.print(f"  {mark} {i['name']}" + (f"  ({i['hint']})" if not i["ok"] and i.get("hint") else ""))
    console.print(f"  python {sys.version.split()[0]}")
    if not res["ok"]:
        raise typer.Exit(1)


@app.command()
def run(
    sources: list[str] = typer.Argument(..., help="Video URLs or local files"),
    type: Optional[str] = typer.Option(None, "--type", "-t", help=f"one of {', '.join(SOURCE_TYPES)}"),
    layout: Optional[str] = typer.Option(None, "--layout", "-l", help="crop | blur | facecam"),
    agent: Optional[str] = typer.Option(None, help="override picker: claude | codex | agy | manual"),
    force: bool = typer.Option(False, help="re-process a video that was already done"),
):
    """Clip one or more videos into ready-to-upload Shorts."""
    cfg = load_config()
    total = 0
    for s in sources:
        total += len(pipeline.process(s, cfg, source_type=type, layout=layout, agent=agent, force=force))
    console.print(f"[bold green]{total} clips ready.[/] Next: `clip upload`")


@app.command()
def harvest(
    limit: int = typer.Option(0, help="max videos to process this run (0 = no limit)"),
    dry_run: bool = typer.Option(False, help="only list what would be processed"),
):
    """Check every source in sources.yaml for new videos and clip them."""
    cfg = load_config()
    srcs = load_sources()
    con = db.connect()
    todo = []
    for s in srcs["sources"]:
        try:
            entries = fetch.list_recent(s["url"], int(srcs["per_source_new_videos"]))
        except Exception as e:
            console.print(f"[red]✗ {s.get('name')}: {e}[/]")
            continue
        for e in entries:
            age = fetch.age_days(e)
            if age is not None and age > float(srcs["max_age_days"]):
                continue
            if db.video_status(con, _guess_id(e)) == "done":
                continue
            todo.append((s, e))
    console.print(f"{len(todo)} new videos")
    for s, e in todo:
        console.print(f"  • [{s.get('name')}] {e['title'][:80]}")
    if dry_run:
        return
    made = 0
    for s, e in todo[: limit or None]:
        made += len(pipeline.process(e["url"], cfg, source=s))
    console.print(f"[bold green]{made} clips ready.[/]")


def _guess_id(entry: dict) -> str:
    # matches fetch.probe ids for the common extractors so already-done videos are skipped without a probe
    url = entry["url"]
    if "twitch" in url:
        return f"twitchvod-{entry['id']}" if entry.get("id") else url
    if "youtu" in url:
        return f"youtube-{entry['id']}" if entry.get("id") else url
    return url


@app.command("render")
def render_cmd(job: Path = typer.Argument(..., help="job folder that has picks_raw.json (manual picking)")):
    """Render clips for a job whose picks were written by hand / by an interactive agent."""
    cfg = load_config()
    con = db.connect()
    meta = json.loads((job / "meta.json").read_text(encoding="utf-8"))
    tr = json.loads((job / "transcript.json").read_text(encoding="utf-8"))
    sig = json.loads((job / "signals.json").read_text(encoding="utf-8"))
    avoid = [(r["start"], r["end"]) for r in db.clips_for_video(con, meta["id"])]
    picks = pick.load_manual(job, tr, float(sig["duration"] or meta["duration"]), meta["source_type"], cfg, avoid)
    full = Path(meta["url"]) if meta["local"] else next(iter(job.glob("source.mp4")), None)
    pipeline.render_picks(con, job, meta, picks, tr, sig, meta["source_type"], meta["layout"],
                          meta["source"].get("facecam"), full, cfg, meta["source"])


@app.command()
def upload(
    count: Optional[int] = typer.Option(None, help="how many clips this run (default upload.per_run_max)"),
    now: bool = typer.Option(False, "--now", help="publish immediately instead of scheduling for US prime time"),
    check: bool = typer.Option(False, help="fill in ONE upload and stop before publishing, so you can check it"),
    dry_run: bool = typer.Option(False, help="show the plan + schedule without opening the browser"),
):
    """Upload ready clips through YouTube Studio in ego lite, scheduled across US prime time."""
    from .upload import run_upload

    run_upload(load_config(), count=count, schedule=False if now else None, check=check, dry_run=dry_run)


@app.command()
def auto(limit: int = typer.Option(0, help="max new videos to process")):
    """harvest + upload: the one command to run daily (or on a timer)."""
    harvest(limit=limit, dry_run=False)
    upload(count=None, now=False, check=False, dry_run=False)


@app.command()
def dashboard(
    port: int = typer.Option(8765, help="local port"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="open the dashboard in your browser"),
):
    """Local web dashboard: paste links, watch progress, preview clips, upload."""
    from .dashboard.server import serve

    serve(port=port, open_browser=open_browser)


@app.command()
def status():
    """Queue and upload counts."""
    con = db.connect()
    t = Table("status", "clips")
    for r in con.execute("select status, count(*) n from clips group by status"):
        t.add_row(r["status"], str(r["n"]))
    console.print(t)
    rows = con.execute("select id, score, title, file from clips where status='ready' order by score desc limit 10").fetchall()
    if rows:
        t2 = Table("id", "score", "next up")
        for r in rows:
            t2.add_row(str(r["id"]), f"{r['score']:.0f}", r["title"])
        console.print(t2)
    vids = con.execute("select status, count(*) n from videos group by status").fetchall()
    console.print("videos: " + ", ".join(f"{r['status']}={r['n']}" for r in vids))


@app.command()
def skip(clip_ids: list[int]):
    """Never upload these clips."""
    con = db.connect()
    for i in clip_ids:
        db.update_clip(con, i, status="skipped")
    console.print(f"skipped {len(clip_ids)}")


if __name__ == "__main__":
    app()
