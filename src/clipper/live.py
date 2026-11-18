"""Live clipping: watch a stream's chat in real time and clip the moment the moment chat goes wild.

Per channel:
  Recorder  streamlink -> ffmpeg writes the stream into 2 s .ts segments; only the last few minutes are kept.
  Chat      Twitch IRC (anonymous, read-only) or YouTube live chat (yt-dlp), timestamped in wall-clock time.
  Detector  every second: messages in the last 10 s vs the median 10 s of the past 5 minutes (hype words count
            double, "!commands" are ignored). A burst = something happened.
When chat bursts at T, the recording from T - lead (15 s) to T + tail is cut out of the buffer, transcribed, sent to
the agent for a yes/no + title, rendered with Remotion and queued like every other clip.
"""
from __future__ import annotations

import json
import random
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

from rich.console import Console

from . import chat, db, fetch, pick, pipeline, signals, transcribe
from .agents import AgentError, run_agent
from .config import JOBS, load_sources
from .progress import emit

console = Console()
SEG_SECONDS = 2


# ---------------------------------------------------------------- channel info

def parse_channel(url: str) -> tuple[str, str, str]:
    """-> (platform, channel, url). Accepts twitch.tv/<name>, a bare Twitch name, or a YouTube live URL."""
    u = url.strip()
    m = re.match(r"(?:https?://)?(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]+)/?$", u)
    if m:
        return "twitch", m.group(1).lower(), f"https://www.twitch.tv/{m.group(1).lower()}"
    if re.fullmatch(r"[A-Za-z0-9_]{3,25}", u):
        return "twitch", u.lower(), f"https://www.twitch.tv/{u.lower()}"
    if "youtu" in u:
        m = re.search(r"youtube\.com/(@[\w.-]+)", u)
        return "youtube", (m.group(1) if m else ""), u
    raise ValueError(f"not a Twitch channel or YouTube live URL: {url}")


def twitch_info(channel: str) -> dict | None:
    """Live stream title/game for a Twitch channel, or None when offline."""
    q = {"query": 'query($l:String!){user(login:$l){displayName stream{id title viewersCount game{name}}}}',
         "variables": {"l": channel}}
    req = urllib.request.Request(chat._GQL, data=json.dumps(q).encode(),
                                 headers={"Client-ID": chat._CLIENT_ID, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        user = (json.load(r).get("data") or {}).get("user")
    if not user or not user.get("stream"):
        return None
    s = user["stream"]
    return {"id": s["id"], "title": s.get("title") or "", "game": (s.get("game") or {}).get("name", ""),
            "viewers": s.get("viewersCount", 0), "name": user.get("displayName") or channel}


def stream_info(platform: str, channel: str, url: str) -> dict | None:
    """{id, title, name, url} when the stream is live, else None."""
    if platform == "twitch":
        i = twitch_info(channel)
        return i and {**i, "url": url}
    try:
        m = fetch.probe(url)
    except Exception:
        return None
    if not m.get("live"):
        return None
    return {"id": m["id"].split("-", 1)[1], "title": m["title"], "name": m.get("uploader") or channel,
            "url": m["url"], "game": "", "viewers": 0}


# ---------------------------------------------------------------- recording

@dataclass
class Seg:
    path: Path
    start: float   # wall-clock time of the segment's first frame
    end: float     # wall-clock time of its last frame


class Recorder:
    """streamlink | ffmpeg -> rolling buffer of 2 s .ts segments, each tagged with wall-clock time."""

    def __init__(self, platform: str, url: str, folder: Path, keep_seconds: float):
        self.platform, self.url, self.dir, self.keep = platform, url, folder, keep_seconds
        self.segs: list[Seg] = []
        self.lock = threading.Lock()
        self.ended = threading.Event()
        self.procs: list[subprocess.Popen] = []

    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for f in self.dir.glob("*"):
            f.unlink()
        with self.lock:
            self.segs = []
        self.ended.clear()
        sl = [sys.executable, "-m", "streamlink", "--stdout", "--loglevel", "error",
              "--retry-streams", "5", "--retry-max", "3", "--hls-live-edge", "2"]
        if self.platform == "twitch":
            sl += ["--twitch-low-latency", "--twitch-supported-codecs", "h264"]
        sl += [self.url, "1080p60,1080p,936p60,720p60,720p,best"]
        p1 = subprocess.Popen(sl, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        ff = [fetch.ffmpeg_bin(), "-hide_banner", "-v", "error", "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0?",
              "-c", "copy", "-f", "segment", "-segment_time", str(SEG_SECONDS),
              "-segment_list", str(self.dir / "list.csv"), "-segment_list_type", "csv",
              str(self.dir / "seg_%06d.ts")]
        p2 = subprocess.Popen(ff, stdin=p1.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p1.stdout.close()  # ffmpeg owns the pipe now
        self.procs = [p1, p2]
        threading.Thread(target=self._follow, daemon=True).start()

    def _follow(self) -> None:
        """Tail list.csv: a line appears when a segment is complete, i.e. right when its last frame arrived."""
        lst, pos, buf = self.dir / "list.csv", 0, ""
        while True:
            alive = self.procs[1].poll() is None
            if lst.exists():
                with open(lst, encoding="utf-8") as f:
                    f.seek(pos)
                    buf += f.read()
                    pos = f.tell()
                now = time.time()
                *lines, buf = buf.split("\n")
                for line in lines:
                    try:
                        name, s, e = line.rsplit(",", 2)
                        dur = float(e) - float(s)
                    except ValueError:
                        continue
                    with self.lock:
                        self.segs.append(Seg(self.dir / name, now - dur, now))
            if not alive:
                self.ended.set()
                return
            time.sleep(0.5)

    @property
    def recorded_until(self) -> float:
        with self.lock:
            return self.segs[-1].end if self.segs else 0.0

    def prune(self, protect_from: float | None = None) -> None:
        cut = time.time() - self.keep
        if protect_from is not None:
            cut = min(cut, protect_from)
        with self.lock:
            while self.segs and self.segs[0].end < cut:
                self.segs.pop(0).path.unlink(missing_ok=True)

    def cut(self, a: float, b: float, out: Path) -> tuple[Path, float]:
        """Wall-clock range [a, b] -> mp4 (whole segments, so slightly longer) + its wall-clock start."""
        with self.lock:
            segs = [s for s in self.segs if s.end > a and s.start < b]
        if not segs:
            raise RuntimeError("that part of the stream is not in the buffer any more")
        out.parent.mkdir(parents=True, exist_ok=True)
        raw = out.with_suffix(".ts")
        with open(raw, "wb") as w:  # MPEG-TS segments can be joined byte for byte
            for s in segs:
                w.write(s.path.read_bytes())
        subprocess.run([fetch.ffmpeg_bin(), "-y", "-v", "error", "-i", str(raw), "-c", "copy",
                        "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(out)], check=True)
        raw.unlink(missing_ok=True)
        return out, segs[0].start

    def stop(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        self.ended.set()


# ---------------------------------------------------------------- chat

class TwitchChat:
    """Anonymous read-only IRC connection (the same thing a logged-out viewer gets)."""

    def __init__(self, channel: str, on_msg):
        self.channel, self.on_msg = channel, on_msg
        self.stopped = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while not self.stopped.is_set():
            try:
                s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30)
                s.sendall(f"PASS SCHMOOPIIE\r\nNICK justinfan{random.randint(10000, 99999)}\r\n"
                          f"JOIN #{self.channel}\r\n".encode())
                buf = b""
                while not self.stopped.is_set():
                    try:
                        data = s.recv(65536)
                    except socket.timeout:
                        s.sendall(b"PING :tmi.twitch.tv\r\n")
                        continue
                    if not data:
                        break
                    buf += data
                    *lines, buf = buf.split(b"\r\n")
                    now = time.time()
                    for raw in lines:
                        line = raw.decode("utf-8", "replace")
                        if line.startswith("PING"):
                            s.sendall(b"PONG :tmi.twitch.tv\r\n")
                        elif " PRIVMSG #" in line:
                            self.on_msg(now, line.split(" PRIVMSG #", 1)[1].split(" :", 1)[-1])
                s.close()
            except OSError:
                pass
            if not self.stopped.is_set():
                time.sleep(3)  # reconnect

    def stop(self) -> None:
        self.stopped.set()


class YouTubeChat:
    """Polls YouTube's live-chat feed (what the popout chat window does): ~5 s behind, no login."""

    UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/131.0 Safari/537.36")

    def __init__(self, video_id: str, on_msg):
        self.vid, self.on_msg = video_id, on_msg
        self.stopped = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _req(self, url: str, body: dict | None = None):
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                     headers={"User-Agent": self.UA, "Accept-Language": "en-US",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")

    def _init(self) -> tuple[str, str, str]:
        html = self._req(f"https://www.youtube.com/live_chat?is_popout=1&v={self.vid}")
        key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
        ver = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
        m = re.search(r'(?:var ytInitialData|window\["ytInitialData"\])\s*=\s*(\{.*?\});\s*</script>', html, re.S)
        if not (key and ver and m):
            raise RuntimeError("this stream has no live chat (or it is turned off)")
        r = json.loads(m.group(1))["contents"]["liveChatRenderer"]
        try:  # "Live chat" (every message) instead of the default filtered "Top chat"
            items = r["header"]["liveChatHeaderRenderer"]["viewSelector"]["sortFilterSubMenuRenderer"]["subMenuItems"]
            cont = items[-1]["continuation"]["reloadContinuationData"]["continuation"]
        except (KeyError, IndexError, TypeError):
            cont = self._cont(r["continuations"])[0]
        return key.group(1), ver.group(1), cont

    @staticmethod
    def _cont(conts: list) -> tuple[str, float]:
        for c in conts:
            for v in c.values():
                if isinstance(v, dict) and v.get("continuation"):
                    return v["continuation"], int(v.get("timeoutMs") or 5000) / 1000
        raise RuntimeError("chat ended")

    def _run(self) -> None:
        fails = 0
        while not self.stopped.is_set():
            try:
                key, ver, cont = self._init()
                url = f"https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={key}&prettyPrint=false"
                ctx = {"client": {"clientName": "WEB", "clientVersion": ver, "hl": "en", "gl": "US"}}
                while not self.stopped.is_set():
                    lc = json.loads(self._req(url, {"context": ctx, "continuation": cont}))
                    lc = lc["continuationContents"]["liveChatContinuation"]
                    now = time.time()
                    for act in lc.get("actions", []):
                        r = act.get("addChatItemAction", {}).get("item", {}).get("liveChatTextMessageRenderer")
                        if not r:
                            continue
                        text = " ".join(x.get("text") or ((x.get("emoji") or {}).get("shortcuts") or [""])[0]
                                        for x in r.get("message", {}).get("runs", []))
                        ts = int(r.get("timestampUsec") or 0) / 1e6 or now
                        self.on_msg(min(ts, now), text)
                    cont, wait = self._cont(lc.get("continuations", []))
                    fails = 0
                    self.stopped.wait(min(max(wait, 1.0), 6.0))
            except Exception as e:
                fails += 1
                if fails in (1, 10):
                    console.print(f"  [dim]youtube chat: {e}; retrying[/]")
                self.stopped.wait(min(60, 5 * fails))

    def stop(self) -> None:
        self.stopped.set()


# ---------------------------------------------------------------- spike detection

class Detector:
    """Live version of chat.spikes(): only looks backwards, one check per second."""

    def __init__(self, z: float, ratio: float, min_msgs: int, min_gap: float):
        self.z, self.ratio, self.min_msgs, self.min_gap = z, ratio, min_msgs, min_gap
        self.sec: dict[int, list] = {}            # second -> [score, count, Counter(hype)]
        self.texts: deque = deque(maxlen=20000)   # (t, text) for the agent's context
        self.lock = threading.Lock()
        self.first: int | None = None
        self.last_spike = -1e9
        self.total = 0

    def add(self, t: float, text: str) -> None:
        text = text.strip()
        if not text or text.startswith("!"):  # bot commands / giveaway entries are not reactions
            return
        hits = {w for w in chat._tokens(text) if w in chat.HYPE}
        s = int(t)
        with self.lock:
            e = self.sec.setdefault(s, [0.0, 0, Counter()])
            e[0] += 2.0 if hits else 1.0
            e[1] += 1
            e[2].update(hits)
            self.texts.append((t, text))
            self.total += 1
            if self.first is None:
                self.first = s

    def _win(self, end: int) -> tuple[float, int]:
        sc, n = 0.0, 0
        for s in range(end - 9, end + 1):
            e = self.sec.get(s)
            if e:
                sc += e[0]
                n += e[1]
        return sc, n

    def check(self, t: int) -> dict | None:
        """Is the 10 s ending at second t a burst? Returns the spike (t = when chat started going wild)."""
        with self.lock:
            if self.first is None or t - self.first < 60 or t - self.last_spike < self.min_gap:
                return None
            score, count = self._win(t)
            past = [self._win(t - 10 * k)[0] for k in range(1, 31) if t - 10 * k - 9 >= self.first]
            if len(past) < 5:
                return None
            nz = [p for p in past if p > 0]
            base = max(statistics.median(past), 0.5 * max(2.0, statistics.median(nz) if nz else 2.0))
            z = (score - base) / (base + 1.0) ** 0.5
            ratio = score / base
            if z < self.z or ratio < self.ratio or count < self.min_msgs:
                return None
            per_s = base / 10
            onset = next((s for s in range(t - 9, t + 1)
                          if (self.sec.get(s) or [0.0])[0] >= max(1.5, 2 * per_s)), t - 9)
            top = Counter()
            for s in range(t - 9, t + 1):
                top.update((self.sec.get(s) or [0, 0, Counter()])[2])
            self.last_spike = onset
            for s in [s for s in self.sec if s < t - 600]:
                del self.sec[s]
            return {"t": float(onset), "msgs": count, "x": round(ratio, 1), "z": round(z, 1),
                    "top": " ".join(f"{w.upper()}×{n}" for w, n in top.most_common(3))}

    def sample(self, a: float, b: float, n: int = 25) -> list[str]:
        with self.lock:
            msgs = [x for t, x in self.texts if a <= t < b]
        if len(msgs) > n:
            msgs = [msgs[round(i * (len(msgs) - 1) / (n - 1))] for i in range(n)]
        return msgs


# ---------------------------------------------------------------- one channel

@dataclass
class Spike:
    n: int
    info: dict
    a: float = 0.0      # wall-clock range to cut (lead/tail plus a little room for the agent to adjust)
    b: float = 0.0
    sample: list = field(default_factory=list)


class Session:
    def __init__(self, url: str, cfg: dict, args: dict):
        self.platform, self.channel, self.url = parse_channel(url)
        self.cfg, self.args = cfg, args
        lv = cfg["live"]
        self.lead, self.tail = float(args.get("lead") or lv["lead_seconds"]), float(lv["tail_seconds"])
        self.delay = 4 if self.platform == "twitch" else 9  # YouTube chat arrives in ~5 s batches
        self.det = Detector(float(lv["sensitivity"]), 2.0, int(lv["min_msgs"]), float(lv["min_gap_seconds"]))
        self.rec: Recorder | None = None
        self.chat = None
        self.info: dict = {}
        self.pending: list[Spike] = []
        self.spikes = 0
        self.last_eval = 0
        self.job: Path | None = None
        self.started = 0.0
        self.done = False
        self.restarts = 0
        self.told = False

    def source(self) -> dict:
        """This channel's entry in sources.yaml (layout/facecam/permission), if it has one."""
        for s in load_sources()["sources"]:
            if self.channel and self.channel.lower() in str(s.get("url", "")).lower():
                return s
        return {}

    def go_live(self, info: dict) -> None:
        self.info = info
        self.started = time.time()
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        name = self.channel or pipeline.slug(info["title"], 20)
        self.job = JOBS / f"live_{name}_{stamp}"
        self.job.mkdir(parents=True, exist_ok=True)
        src = self.source()
        self.meta = {
            "id": f"live-{self.platform}-{name}-{stamp}", "title": info["title"], "url": info["url"],
            "local": False, "live": True, "duration": 0, "uploader": info["name"],
            "source_type": self.args.get("type") or src.get("type") or "stream",
            "layout": self.args.get("layout") or src.get("layout") or "crop", "source": src,
        }
        if self.meta["layout"] == "facecam" and not src.get("facecam"):
            self.meta["layout"] = "crop"
        (self.job / "meta.json").write_text(json.dumps(self.meta, indent=2), encoding="utf-8")
        con = db.connect()
        db.set_video(con, self.meta["id"], source_name=src.get("name", info["name"]),
                     source_type=self.meta["source_type"], url=info["url"], title=info["title"],
                     status="processing")
        keep = float(self.cfg["live"]["buffer_minutes"]) * 60
        self.rec = Recorder(self.platform, self.url, self.job / "buffer", keep)
        self.rec.start()
        if self.platform == "twitch":
            self.chat = TwitchChat(self.channel, self.det.add)
        else:
            self.chat = YouTubeChat(info["id"], self.det.add)
        self.chat.start()
        emit("title", info["name"])
        game = f" · {info['game']}" if info.get("game") else ""
        console.print(f"[bold red]● LIVE[/] {info['name']}{game}: {info['title'][:80]}", markup=True)
        console.print(f"  recording to a {keep / 60:.0f}-min buffer, watching chat; a burst = clip from "
                      f"{self.lead:.0f} s before it to {self.tail:.0f} s after", style="dim")

    def tick(self, work: Queue) -> None:
        if self.done or not self.rec:
            return
        now = time.time()
        upto = int(now - self.delay)
        if not self.last_eval:
            self.last_eval = upto - 1
        for t in range(self.last_eval + 1, upto + 1):
            sp = self.det.check(t)
            if sp:
                self.spikes += 1
                s = Spike(self.spikes, sp, sp["t"] - self.lead - 3, sp["t"] + self.tail + 12)
                self.pending.append(s)
                ago = now - sp["t"]
                console.print(f"  [yellow]⚡ {self.info['name']} burst #{s.n}[/] {datetime.fromtimestamp(sp['t']):%H:%M:%S} "
                              f"({ago:.0f}s ago): {sp['msgs']} msgs/10s, {sp['x']}x normal  {sp['top']}")
        self.last_eval = max(self.last_eval, upto)

        rec_until = self.rec.recorded_until
        for s in list(self.pending):
            if rec_until >= s.b or (self.rec.ended.is_set() and rec_until > s.info["t"]):
                self.pending.remove(s)
                try:
                    out, w0 = self.rec.cut(s.a, s.b, self.job / "spikes" / f"s{s.n:02d}" / "raw.mp4")
                    s.sample = self.det.sample(s.info["t"] - 3, s.info["t"] + 15)
                    # a snapshot: in --wait mode this session resets itself when the stream ends
                    ctx = SimpleNamespace(job=self.job, meta=self.meta, info=dict(self.info), lead=self.lead,
                                          tail=self.tail, channel=self.channel)
                    work.put((ctx, s, out, w0))
                except Exception as e:
                    console.print(f"  ✗ {self.info['name']} burst #{s.n}: {e}", markup=False)
            elif now - s.b > 120:  # stream stalled; give up on this one
                self.pending.remove(s)
        self.rec.prune(protect_from=min((s.a for s in self.pending), default=None))
        if self.rec.ended.is_set() and not self.pending:
            if stream_info(self.platform, self.channel, self.url) and self.restarts < 20:
                self.restarts += 1  # connection dropped but the stream is still on: keep going
                console.print(f"  [dim]{self.info.get('name')}: reconnecting the recording[/]")
                self.rec.start()
                return
            self.finish("stream ended")
            if self.args.get("wait"):  # all-day mode: go back to waiting for the next stream
                self.__init__(self.url, self.cfg, self.args)

    def status(self) -> str:
        mins = (time.time() - self.started) / 60
        return f"{self.info.get('name', self.channel)}: {mins:.0f} min, {self.det.total:,} chat msgs, {self.spikes} bursts"

    def finish(self, why: str) -> None:
        if self.done:
            return
        self.done = True
        if self.chat:
            self.chat.stop()
        if self.rec:
            self.rec.stop()
            for f in self.rec.dir.glob("*"):
                f.unlink(missing_ok=True)
        if self.job:
            db.set_video(db.connect(), self.meta["id"], status="done", error=None)
        console.print(f"[dim]■ {self.info.get('name', self.channel)}: {why} ({self.status()})[/]")


# ---------------------------------------------------------------- turning a burst into a Short

def live_prompt(sess: SimpleNamespace, s: Spike, tr: dict, peaks: list[dict], spike_at: float, dur: float,
                lmin: int, lmax: int) -> str:
    lines = [f"[{x['s']:.1f}-{x['e']:.1f}] {x['text']}" for x in tr["segments"]]
    loud = ", ".join(f"{p['t']}s" for p in peaks) or "none"
    start0 = max(0.0, spike_at - sess.lead)
    said = "\n".join(f"  {m[:120]}" for m in s.sample) or "  (none)"
    return f"""You are the clip editor for a YouTube Shorts channel about GTA 6 aimed at viewers in the USA,
Canada, UK and Australia. A LIVE stream's chat just exploded, which usually means something clip-worthy happened.
Decide if it makes a good Short and write it up.

STREAM: "{sess.info.get('title', '')}" by {sess.info.get('name', '')}{f" (playing {sess.info['game']})" if sess.info.get('game') else ""}
This recording is {dur:.0f} s long. Chat went wild at {spike_at:.1f}s ({s.info['msgs']} msgs in 10 s, {s.info['x']}x the
normal pace; {s.info['top'] or 'no hype words'}). Chat reacts a few seconds AFTER the moment. You only get the
transcript, not the picture: chat going wild is your evidence that something visual (a crash, a fail, a kill) may have
happened even when the words are thin.

WHAT CHAT SAID RIGHT THEN:
{said}

TRANSCRIPT (seconds from start of this recording):
{chr(10).join(lines) or "(no speech detected)"}

LOUDNESS SPIKES: {loud}

THE CUT: by default the clip runs {start0:.1f}s to {min(dur, spike_at + sess.tail):.1f}s (15 s before chat went wild).
You may move the start by up to 3 s earlier or 5 s later to begin on a full sentence, and the end anywhere up to
{dur:.1f}s to finish right after the payoff. Length {lmin}-{lmax} s.

{pick.rules(1, lmin, lmax)}- Return an EMPTY list if this is not a real moment: an ad or "be right back" screen, a raid / sub train /
  giveaway / emote-only spam, chat arguing among itself, or nothing that a new viewer would understand.
"""


def handle(sess: SimpleNamespace, s: Spike, raw: Path, w0: float, cfg: dict, agent: str, post: bool) -> list[int]:
    sdir = raw.parent
    tag = f"{sess.info.get('name', sess.channel)} burst #{s.n}"
    meta = sess.meta
    spike_at = s.info["t"] - w0
    dur = fetch.ffprobe_duration(raw)
    wav = fetch.to_wav(raw, sdir / "audio.wav")
    tr = transcribe.transcribe(wav, sdir / "transcript.json", cfg)
    peaks = signals.find_peaks(signals.loudness_per_second(wav), 4, 8)
    lmin, lmax = cfg["clip_length"].get(meta["source_type"], [15, 55])
    start0, end0 = max(0.0, spike_at - sess.lead), min(dur, spike_at + sess.tail)
    default = {"start": start0, "end": end0, "score": 7, "title": f"{sess.info.get('name', '')} chat lost it at this moment",
               "hook": "CHAT WENT CRAZY", "voice_hook": "", "description": sess.info.get("title", ""),
               "tags": [], "reason": "chat burst"}

    c = default
    if agent != "none":
        prompt = live_prompt(sess, s, tr, peaks, spike_at, dur, lmin, lmax)
        (sdir / "prompt.md").write_text(prompt, encoding="utf-8")
        try:
            res = run_agent(agent, prompt, pick.CLIP_SCHEMA, cfg.get("agent_model", ""), sdir)
            clips = res.get("clips", [])
            if not clips:
                console.print(f"  · {tag}: agent passed (not a real moment)")
                return []
            c = {**default, **clips[0]}
        except AgentError as e:
            console.print(f"  ! {tag}: agent failed ({e}); using the default cut + title", markup=False)
    # keep the cut where the user wants it: starting ~lead s before the burst
    st = min(max(float(c["start"]), start0 - 3), start0 + 5)
    en = min(max(float(c["end"]), spike_at + 5), dur)
    st, en = pick._snap(tr["words"], st, en)
    en = min(en, dur, st + lmax)
    if en - st < min(lmin, 10):
        st, en = start0, end0
    c = {**c, "start": round(max(0.0, st), 2), "end": round(en, 2)}
    if float(c["score"]) < float(cfg["live"]["min_score"]):
        console.print(f"  · {tag}: scored {c['score']}/10, below live.min_score; not clipped")
        return []
    (sdir / "pick.json").write_text(json.dumps(c, indent=2), encoding="utf-8")
    con = db.connect()
    ids = pipeline.render_picks(con, sess.job, meta, [c], tr, {"peaks": peaks}, meta["source_type"],
                                meta["layout"], meta["source"].get("facecam"), raw, cfg, meta["source"])
    if ids and post:
        from .upload import run_upload
        try:
            run_upload(cfg, count=len(ids), schedule=False)
        except SystemExit as e:  # another upload is running / daily limit: it stays in the queue
            console.print(f"  ! not posted now ({e}); it stays in the upload queue", markup=False)
    return ids


# ---------------------------------------------------------------- main loop

def run(urls: list[str], cfg: dict, agent: str | None = None, post: bool = False, wait: bool = False,
        max_clips: int = 0, hours: float = 0, args: dict | None = None, dry_run: bool = False) -> int:
    args = {**(args or {}), "wait": wait}
    agent = agent or cfg["agent"]
    sessions = [Session(u, cfg, args) for u in urls]
    work: Queue = Queue()
    made: list[int] = []
    stop = threading.Event()

    def worker():
        while True:
            item = work.get()
            if item is None:
                return
            sess, s, raw, w0 = item
            try:
                t = time.time()
                if dry_run:
                    console.print(f"  · {sess.info['name']} burst #{s.n} cut: {raw}  (spike at {s.info['t'] - w0:.1f}s in it)", markup=False)
                    continue
                ids = handle(sess, s, raw, w0, cfg, agent, post)
                made.extend(ids)
                if ids:
                    console.print(f"  [green]✓ {sess.info['name']} burst #{s.n} → clip #{ids[0]}[/] ({time.time() - t:.0f}s after the cut)")
                if max_clips and len(made) >= max_clips:
                    stop.set()
            except Exception as e:
                console.print(f"  ✗ {sess.info.get('name')} burst #{s.n}: {e}", markup=False)
            finally:
                work.task_done()

    import signal

    def _term(*_):
        raise KeyboardInterrupt
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _term)  # dashboard "cancel" / kill: stop cleanly like Ctrl-C

    wt = threading.Thread(target=worker, daemon=True)
    wt.start()
    deadline = time.time() + hours * 3600 if hours else 0
    next_check = 0.0
    last_status = time.time()
    try:
        while not stop.is_set():
            now = time.time()
            if now >= next_check:  # offline channels: go live when they start
                next_check = now + 60
                for sess in sessions:
                    if sess.rec is None and not sess.done:
                        info = stream_info(sess.platform, sess.channel, sess.url)
                        if info:
                            sess.go_live(info)
                        elif not wait:
                            console.print(f"[yellow]{sess.channel or sess.url} is not live.[/] "
                                          "Use --wait to start when it goes live.")
                            sess.done = True
                        elif not sess.told:
                            console.print(f"[dim]waiting for {sess.channel or sess.url} to go live...[/]")
                            sess.told = True
            for sess in sessions:
                sess.tick(work)
            if now - last_status > 300:
                last_status = now
                live = [s.status() for s in sessions if s.rec and not s.done]
                if live:
                    console.print("[dim]  " + " | ".join(live) + f" | {len(made)} clips[/]")
            if all(s.done for s in sessions) and not (wait and any(s.rec is None for s in sessions)):
                break
            if deadline and now > deadline:
                console.print("[dim]time limit reached[/]")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[dim]stopping: finishing the clips already cut (Ctrl-C again to quit now)[/]")
    for sess in sessions:
        sess.finish("stopped") if not sess.done else None
    try:
        work.put(None)
        wt.join()
    except KeyboardInterrupt:
        pass
    console.print(f"[bold green]{len(made)} live clips ready.[/]" + ("" if post else " Next: `clip upload`"))
    return len(made)
