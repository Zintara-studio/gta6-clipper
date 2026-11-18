"""Local dashboard: `uv run clip dashboard` -> http://127.0.0.1:8765

Python stdlib only. Binds to localhost. Every job runs as a `clip ...` subprocess with a log file, in one of two lanes
(processing / uploads), so an upload can run while videos are being clipped.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import db
from ..checks import run_checks
from ..config import ROOT, SOURCE_TYPES, WORKSPACE, load_config, load_sources

HERE = Path(__file__).parent
TASK_LOGS = WORKSPACE / "tasks"
LAYOUTS = ("crop", "blur", "facecam")
URL_RE = re.compile(r"^https?://\S+$", re.I)


# ---------------------------------------------------------------- task runner

class Runner:
    def __init__(self) -> None:
        TASK_LOGS.mkdir(parents=True, exist_ok=True)
        self.procs: dict[int, subprocess.Popen] = {}
        self.cancelled: set[int] = set()
        con = db.connect()
        # anything "running" when the dashboard last stopped was interrupted
        con.execute("update tasks set status='failed', finished_at=? where status='running'", (time.time(),))
        con.commit()
        for lane in ("process", "upload", "live"):
            threading.Thread(target=self._loop, args=(lane,), daemon=True).start()

    def add(self, kind: str, args: list[str], label: str, lane: str) -> int:
        con = db.connect()
        cur = con.execute("insert into tasks(kind, lane, args, label, status, created_at) values(?,?,?,?,?,?)",
                          (kind, lane, json.dumps(args), label, "queued", time.time()))
        con.commit()
        return cur.lastrowid

    def cancel(self, tid: int) -> None:
        con = db.connect()
        row = con.execute("select status from tasks where id=?", (tid,)).fetchone()
        if not row:
            return
        if row["status"] == "queued":
            con.execute("update tasks set status='cancelled', finished_at=? where id=?", (time.time(), tid))
            con.commit()
        elif row["status"] == "running" and tid in self.procs:
            self.cancelled.add(tid)
            _kill_tree(self.procs[tid])

    def stop_all(self) -> None:
        for tid, p in list(self.procs.items()):
            self.cancelled.add(tid)
            _kill_tree(p)

    def _loop(self, lane: str) -> None:
        while True:
            try:
                con = db.connect()
                row = con.execute("select * from tasks where lane=? and status='queued' order by id limit 1",
                                  (lane,)).fetchone()
                if row:
                    self._run(con, row)
                else:
                    time.sleep(1)
            except Exception as e:  # never let a lane die
                print(f"[dashboard] {lane} lane error: {e}")
                time.sleep(2)

    def _run(self, con, row) -> None:
        tid = row["id"]
        log = TASK_LOGS / f"{tid}.log"
        cmd = [sys.executable, "-m", "clipper.cli", *json.loads(row["args"])]
        env = {**os.environ, "CLIPPER_PROGRESS": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
               "COLUMNS": "140", "NO_COLOR": "1", "TERM": "dumb"}
        con.execute("update tasks set status='running', started_at=?, log=? where id=?", (time.time(), str(log), tid))
        con.commit()
        kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        with open(log, "w", encoding="utf-8") as f:
            p = subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, env=env, **kw)
            self.procs[tid] = p
            rc = p.wait()
            self.procs.pop(tid, None)
        info = parse_log(log, row["kind"])
        if tid in self.cancelled:
            status = "cancelled"
        elif rc != 0 or info.get("failed"):
            status = "failed"
        else:
            status = "done"
        con.execute("update tasks set status=?, returncode=?, finished_at=? where id=?",
                    (status, rc, time.time(), tid))
        con.commit()


def _kill_tree(p: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, signal.SIGTERM)
    except Exception:
        p.terminate()



def _future(iso: str | None, now: datetime) -> bool:
    """publish_at is stored with its own UTC offset, so compare as datetimes, not strings."""
    try:
        return datetime.fromisoformat(iso) > now
    except (TypeError, ValueError):
        return False

def parse_log(path: Path | str | None, kind: str) -> dict:
    """Pull the ::stage:: / ::title:: / ::clip:: markers and a readable tail out of a task log."""
    out = {"stage": None, "title": None, "titles": [], "clips": 0, "picked": None, "render": None, "tail": [],
           "failed": False, "lastGood": None, "uploaded": 0, "upload_failed": 0, "upload_pos": None}
    if not path or not Path(path).exists():
        return out
    text = Path(path).read_text(encoding="utf-8", errors="replace")[-400_000:]
    lines = []
    stages = []
    for line in text.splitlines():
        m = re.match(r"^::(\w+)::(.*)$", line)
        if m:
            k, v = m.group(1), m.group(2)
            if k == "stage":
                if v not in ("failed", "skipped"):
                    out["lastGood"] = v
                stages.append(v)
                out["stage"] = v
            elif k == "title":
                out["titles"].append(v)
                out["title"] = v
            elif k == "clip":
                out["clips"] += 1
            elif k == "picked":
                out["picked"] = int(v)
            elif k == "render":
                out["render"] = v
            continue
        if kind == "live" and "⚡" in line:
            out["bursts"] = out.get("bursts", 0) + 1
        if kind == "upload":
            m = re.search(r"\[uploader\] \((\d+)/(\d+)\)", line)
            if m:
                out["upload_pos"] = f"{m.group(1)}/{m.group(2)}"
            if re.search(r"\[uploader\]\s+ok\b", line):
                out["uploaded"] += 1
            if re.search(r"\[uploader\]\s+FAILED", line):
                out["upload_failed"] += 1
        lines.append(line)
    out["tail"] = [l for l in lines if l.strip()][-60:]
    # a single-video task failed if its last stage was "failed" and it produced nothing
    out["failed"] = kind == "process" and out["stage"] == "failed" and out["clips"] == 0
    return out


# ---------------------------------------------------------------- API helpers

def _row(r) -> dict:
    return {k: r[k] for k in r.keys()}


def api_state() -> dict:
    con = db.connect()
    cfg = load_config()
    tasks = []
    for r in con.execute("select * from tasks order by id desc limit 40"):
        t = _row(r)
        t["args"] = json.loads(t["args"] or "[]")
        t["info"] = parse_log(t["log"], t["kind"])
        if t["status"] not in ("running",):
            t["info"]["tail"] = t["info"]["tail"][-12:]
        tasks.append(t)
    counts = {r["status"]: r["n"] for r in con.execute("select status, count(*) n from clips group by status")}
    start_of_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    uploaded_today = con.execute("select count(*) n from clips where status='uploaded' and uploaded_at>=?",
                                 (start_of_day,)).fetchone()["n"]
    now = datetime.now().astimezone()
    upcoming = sum(1 for r in con.execute("select publish_at from clips where status='uploaded' and publish_at!=''")
                   if _future(r["publish_at"], now))
    u = cfg["upload"]
    return {
        "tasks": tasks,
        "counts": counts,
        "uploaded_today": uploaded_today,
        "scheduled_upcoming": upcoming,
        "config": {"agent": cfg["agent"], "daily_target": cfg["daily_target"], "per_run_max": u["per_run_max"],
                   "window": u["window"], "timezone": u["timezone"], "schedule": u["schedule"],
                   "sources": len(load_sources()["sources"])},
    }


def api_clips(status: str) -> list[dict]:
    con = db.connect()
    q = "select * from clips"
    args: tuple = ()
    if status in ("ready", "uploaded", "skipped", "failed"):
        q += " where status=?"
        args = (status,)
    q += " order by " + ("publish_at desc, id desc" if status == "uploaded" else "id desc") + " limit 300"
    out = []
    for r in con.execute(q, args):
        c = _row(r)
        c["tags"] = json.loads(c["tags"] or "[]")
        c["duration"] = round((c["end"] or 0) - (c["start"] or 0), 1)
        c["exists"] = bool(c["file"]) and Path(c["file"]).exists()
        out.append(c)
    return out


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    runner: Runner  # set in serve()
    server_version = "gta6-clipper"

    def log_message(self, *a):  # quiet
        pass

    # -- helpers
    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    # -- GET
    def do_GET(self) -> None:
        u = urlparse(self.path)
        p = u.path
        if p in ("/", "/index.html"):
            body = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif p == "/api/state":
            self._json(api_state())
        elif p == "/api/clips":
            self._json(api_clips(parse_qs(u.query).get("status", ["ready"])[0]))
        elif p == "/api/doctor":
            self._json(run_checks())
        elif m := re.match(r"^/api/tasks/(\d+)/log$", p):
            row = db.connect().execute("select log from tasks where id=?", (int(m.group(1)),)).fetchone()
            text = Path(row["log"]).read_text(encoding="utf-8", errors="replace") if row and row["log"] and Path(row["log"]).exists() else ""
            text = "\n".join(l for l in text.splitlines() if not l.startswith("::"))
            body = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif m := re.match(r"^/media/(\d+)\.mp4$", p):
            self._media(int(m.group(1)))
        else:
            self._json({"error": "not found"}, 404)

    def _media(self, clip_id: int) -> None:
        row = db.connect().execute("select file from clips where id=?", (clip_id,)).fetchone()
        if not row or not row["file"] or not Path(row["file"]).exists():
            self._json({"error": "file missing"}, 404)
            return
        f = Path(row["file"])
        size = f.stat().st_size
        start, end = 0, size - 1
        rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if rng:
            if rng.group(1):
                start = int(rng.group(1))
                if rng.group(2):
                    end = min(int(rng.group(2)), size - 1)
            elif rng.group(2):  # suffix range: last N bytes
                start = max(0, size - int(rng.group(2)))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        try:
            with open(f, "rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -- POST (custom header required: blocks other websites from driving this local server)
    def do_POST(self) -> None:
        if self.headers.get("X-Clipper") != "1":
            self._json({"error": "missing X-Clipper header"}, 403)
            return
        p = urlparse(self.path).path
        try:
            data = self._body()
        except json.JSONDecodeError:
            self._json({"error": "bad json"}, 400)
            return

        if p == "/api/process":
            raw = data.get("urls") or ""
            items = [x.strip().strip('"') for x in raw.splitlines() if x.strip()]  # one per line (paths may have spaces)
            bad = [x for x in items if not (URL_RE.match(x) or Path(x).expanduser().exists())]
            if not items:
                self._json({"error": "Paste at least one link."}, 400)
                return
            if bad:
                self._json({"error": f"Not a link or file: {bad[0]}"}, 400)
                return
            ids = []
            for x in items:
                args = ["run", x]
                if data.get("type") in SOURCE_TYPES:
                    args += ["--type", data["type"]]
                if data.get("layout") in LAYOUTS:
                    args += ["--layout", data["layout"]]
                if data.get("force"):
                    args.append("--force")
                ids.append(self.runner.add("process", args, x, "process"))
            self._json({"ok": True, "tasks": ids})
        elif p == "/api/live":
            from ..live import parse_channel
            chans = [x.strip().strip('"') for x in (data.get("channels") or "").splitlines() if x.strip()]
            try:
                for c in chans:
                    parse_channel(c)
            except ValueError as e:
                self._json({"error": str(e)}, 400)
                return
            if not chans:
                self._json({"error": "Enter at least one channel."}, 400)
                return
            args = ["live", *chans] + (["--wait"] if data.get("wait") else []) + (["--post"] if data.get("post") else [])
            label = "Live: " + ", ".join(parse_channel(c)[1] or c for c in chans)
            self._json({"ok": True, "task": self.runner.add("live", args, label, "live")})
        elif p == "/api/harvest":
            args = ["harvest"]
            if int(data.get("limit") or 0) > 0:
                args += ["--limit", str(int(data["limit"]))]
            self._json({"ok": True, "task": self.runner.add("harvest", args, "Harvest sources.yaml", "process")})
        elif p == "/api/upload":
            args = ["upload"]
            label = "Upload"
            if data.get("check"):
                args.append("--check")
                label = "Upload check (1 clip, you click Schedule)"
            else:
                n = max(1, min(50, int(data.get("count") or 15)))
                args += ["--count", str(n)]
                label = f"Upload {n} clips"
            if data.get("now"):
                args.append("--now")
                label += " · publish now"
            self._json({"ok": True, "task": self.runner.add("upload", args, label, "upload")})
        elif m := re.match(r"^/api/tasks/(\d+)/cancel$", p):
            self.runner.cancel(int(m.group(1)))
            self._json({"ok": True})
        elif p == "/api/tasks/clear":
            con = db.connect()
            con.execute("delete from tasks where status in ('done','failed','cancelled')")
            con.commit()
            self._json({"ok": True})
        elif m := re.match(r"^/api/clips/(\d+)$", p):
            cid = int(m.group(1))
            con = db.connect()
            fields = {}
            if isinstance(data.get("title"), str) and data["title"].strip():
                fields["title"] = data["title"].strip()[:100]
            if isinstance(data.get("description"), str):
                fields["description"] = data["description"].strip()[:4000]
            if data.get("status") in ("ready", "skipped"):
                fields["status"] = data["status"]
            if fields:
                db.update_clip(con, cid, **fields)
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)


def serve(port: int = 8765, open_browser: bool = True) -> None:
    Handler.runner = Runner()
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        raise SystemExit(f"Port {port} is busy. Try: uv run clip dashboard --port {port + 1}")
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}"
    print(f"GTA 6 Clipper dashboard running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        Handler.runner.stop_all()
        httpd.server_close()
