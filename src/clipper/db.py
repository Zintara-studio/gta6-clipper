"""Tiny SQLite store: which videos were processed and every clip's lifecycle."""
from __future__ import annotations

import json
import sqlite3
import time

from .config import WORKSPACE

SCHEMA = """
create table if not exists videos(
  video_id text primary key,
  source_name text, source_type text, url text, title text,
  status text, error text, created_at real
);
create table if not exists clips(
  id integer primary key autoincrement,
  video_id text, job_dir text, source_name text, source_type text, source_url text,
  start real, end real, score real,
  title text, description text, tags text, hook text,
  file text, status text default 'ready', error text,
  created_at real, publish_at text, video_url text, uploaded_at real
);
create table if not exists tasks(
  id integer primary key autoincrement,
  kind text, lane text, args text, label text,
  status text default 'queued', log text, returncode integer,
  created_at real, started_at real, finished_at real
);
"""
# clip status: ready -> uploaded | failed | skipped
# task status: queued -> running -> done | failed | cancelled   (dashboard background jobs)


def connect() -> sqlite3.Connection:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(WORKSPACE / "clipper.db", timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def video_status(con, video_id: str) -> str | None:
    row = con.execute("select status from videos where video_id=?", (video_id,)).fetchone()
    return row["status"] if row else None


def set_video(con, video_id: str, **fields) -> None:
    existing = con.execute("select 1 from videos where video_id=?", (video_id,)).fetchone()
    if existing:
        sets = ", ".join(f"{k}=?" for k in fields)
        con.execute(f"update videos set {sets} where video_id=?", (*fields.values(), video_id))
    else:
        fields = {"video_id": video_id, "created_at": time.time(), **fields}
        cols = ", ".join(fields)
        qs = ", ".join("?" for _ in fields)
        con.execute(f"insert into videos({cols}) values({qs})", tuple(fields.values()))
    con.commit()


def add_clip(con, **c) -> int:
    c = {**c, "created_at": time.time()}
    if isinstance(c.get("tags"), list):
        c["tags"] = json.dumps(c["tags"])
    cols = ", ".join(c)
    qs = ", ".join("?" for _ in c)
    cur = con.execute(f"insert into clips({cols}) values({qs})", tuple(c.values()))
    con.commit()
    return cur.lastrowid


def update_clip(con, clip_id: int, **fields) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"update clips set {sets} where id=?", (*fields.values(), clip_id))
    con.commit()


def clips_for_video(con, video_id: str) -> list[sqlite3.Row]:
    return con.execute("select * from clips where video_id=?", (video_id,)).fetchall()
