"""Chat replay as a moment finder: when something crazy happens on stream, chat explodes.

Sources, in order:
  1. a chat export you pass with `--chat` (TwitchDownloader JSON, or [[seconds, text], ...])
  2. Twitch VODs: the chat replay straight from Twitch (public, no login, no extra app)
  3. YouTube past live streams: the live-chat replay via yt-dlp

Messages are counted per 10 s bucket (hype words like LMAO / KEKW / CLIP IT count double) and
compared with the chat's normal pace around that time. Chat reacts AFTER the moment, so a spike at
T means the clip-worthy moment is roughly T-25 s to T.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import yt_dlp

BUCKET = 10

# Twitch's own web client id + the persisted chat-replay query (the same calls TwitchDownloader makes)
_GQL = "https://gql.twitch.tv/gql"
_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
_COMMENTS_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"

HYPE = {
    "lmao", "lmfao", "lol", "lul", "lulw", "kekw", "kek", "omegalul", "icant", "pepelaugh", "xd",
    "w", "ww", "www", "clip", "clipit", "clipped", "nah", "wtf", "omg", "holy", "pog", "pogchamp",
    "poggers", "monkas", "sheesh", "insane", "noway", "bruh", "ayo", "dead", "deadass", "aware",
    "💀", "😂", "🤣", "😭", "🔥", "😱", "‼️",
}
_PHRASES = {"no way": "noway", "clip it": "clipit", "clip that": "clipit", "oh my god": "omg"}
_TOKEN = re.compile(r"[a-z0-9']+|[\U0001F300-\U0001FAFF☀-➿]")


class ChatUnavailable(Exception):
    pass


# ---------------------------------------------------------------- fetching

def _gql(variables: dict) -> dict:
    body = [{"operationName": "VideoCommentsByOffsetOrCursor", "variables": variables,
             "extensions": {"persistedQuery": {"version": 1, "sha256Hash": _COMMENTS_HASH}}}]
    req = urllib.request.Request(_GQL, data=json.dumps(body).encode(),
                                 headers={"Client-ID": _CLIENT_ID, "Content-Type": "application/json"})
    err = "unreachable"
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.load(r)[0]
            errors = res.get("errors") or []
            if not errors:
                return res
            err = errors[0].get("message", "twitch error")
            if (errors[0].get("extensions") or {}).get("code") == "IntegrityCheckFailed":
                raise ChatUnavailable(err)  # Twitch locked this request type; retrying won't help
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError) as e:
            err = str(e)
        time.sleep(min(20.0, 1.5 * 2 ** attempt))  # "service error" / rate limit: back off and retry
    raise ChatUnavailable(err)


def _twitch_range(vod: str, a: int, b: int) -> list[tuple[float, str, str]]:
    """All messages with a <= offset < b. Twitch serves ~50 messages per request starting at an offset."""
    out, t = {}, a
    while t < b:
        video = _gql({"videoID": vod, "contentOffsetSeconds": t})["data"]["video"]
        if not video:
            raise ChatUnavailable("VOD not found (deleted or subscriber-only)")
        edges = (video.get("comments") or {}).get("edges") or []
        if not edges:
            break
        for e in edges:
            n = e["node"]
            off = n["contentOffsetSeconds"]
            if a <= off < b:
                text = "".join(f.get("text") or "" for f in (n.get("message") or {}).get("fragments") or [])
                out[n["id"]] = (float(off), text)
        last = edges[-1]["node"]["contentOffsetSeconds"]
        t = last if last > t else t + 1  # a single second with >50 messages gets capped; fine for counting
    return [(o, txt, i) for i, (o, txt) in out.items()]


def fetch_twitch(vod: str, duration: float, workers: int = 12) -> list[list]:
    end = int(duration) + 30
    n = max(1, min(96, end // 120))
    edges = [end * k // n for k in range(n + 1)]
    def one(k: int):
        try:
            return _twitch_range(vod, edges[k], edges[k + 1])
        except ChatUnavailable as e:
            return e

    with ThreadPoolExecutor(workers) as ex:
        parts = list(ex.map(one, range(n)))
    failed = [p for p in parts if isinstance(p, ChatUnavailable)]
    if len(failed) > n // 10:  # a few missing stretches are fine for spotting spikes, most missing is not
        raise failed[0]
    parts = [p for p in parts if not isinstance(p, ChatUnavailable)]
    seen, msgs = set(), []
    for part in parts:
        for off, text, i in part:
            if i not in seen:
                seen.add(i)
                msgs.append([off, text])
    return sorted(msgs, key=lambda m: m[0])


def fetch_youtube(url: str, job: Path) -> list[list]:
    stem = job / "ytchat"
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "skip_download": True,
            "writesubtitles": True, "subtitleslangs": ["live_chat"], "outtmpl": str(stem)}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    f = next(iter(job.glob("ytchat*.live_chat.json")), None)
    if not f:
        raise ChatUnavailable("no chat replay on this stream")
    msgs = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            o = json.loads(line)["replayChatItemAction"]
        except (ValueError, KeyError):
            continue
        t = int(o.get("videoOffsetTimeMsec", 0)) / 1000
        for act in o.get("actions", []):
            r = act.get("addChatItemAction", {}).get("item", {}).get("liveChatTextMessageRenderer")
            if not r:
                continue
            parts = []
            for run in r.get("message", {}).get("runs", []):
                if "text" in run:
                    parts.append(run["text"])
                elif "emoji" in run:
                    em = run["emoji"]
                    parts.append(em.get("emojiId") if not em.get("isCustomEmoji") else (em.get("shortcuts") or [""])[0])
            msgs.append([t, " ".join(p for p in parts if p)])
    f.unlink(missing_ok=True)
    return msgs


def load_export(path: Path) -> list[list]:
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if isinstance(data, dict) and "comments" in data:  # TwitchDownloader
        return sorted(([float(c["content_offset_seconds"]), (c.get("message") or {}).get("body", "")]
                       for c in data["comments"]), key=lambda m: m[0])
    if isinstance(data, dict) and "messages" in data:  # our own chat.json
        return data["messages"]
    if isinstance(data, list):
        return [[float(m[0]), str(m[1])] for m in data]
    raise ChatUnavailable(f"unknown chat file format: {path}")


def fetch(meta: dict, job: Path, chat_file: str | Path | None = None) -> list[list]:
    """Chat messages as [[seconds_from_start, text], ...]. Cached in the job folder. [] when there is none."""
    cache = job / "chat.json"
    if chat_file:
        msgs = load_export(Path(chat_file))
        cache.write_text(json.dumps({"source": "file", "messages": msgs}), encoding="utf-8")
        return msgs
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))["messages"]
    if meta.get("local"):
        return []
    vid = meta["id"]
    if vid.startswith("twitchvod-"):
        src, msgs = "twitch", fetch_twitch(vid.split("-", 1)[1].lstrip("v"), meta["duration"])
    elif vid.startswith("youtube-") and meta.get("live_status") == "was_live":
        src, msgs = "youtube", fetch_youtube(meta["url"], job)
    else:
        return []
    cache.write_text(json.dumps({"source": src, "messages": msgs}), encoding="utf-8")
    return msgs


# ---------------------------------------------------------------- spikes

def _tokens(text: str) -> list[str]:
    t = text.lower()
    for k, v in _PHRASES.items():
        t = t.replace(k, f" {v} ")
    return _TOKEN.findall(t)


def spikes(msgs: list[list], duration: float, max_spikes: int = 40, min_gap: int = 60) -> list[dict]:
    """Moments where chat suddenly moves much faster than its normal pace around that time."""
    if len(msgs) < 50:
        return []
    nb = int(max(duration, msgs[-1][0]) // BUCKET) + 1
    score = np.zeros(nb, dtype=np.float32)
    count = np.zeros(nb, dtype=np.int32)
    hype_by = [Counter() for _ in range(nb)]
    for off, text in msgs:
        b = int(off // BUCKET)
        if not 0 <= b < nb:
            continue
        hits = [w for w in _tokens(text) if w in HYPE]
        count[b] += 1
        score[b] += 2.0 if hits else 1.0
        hype_by[b].update(set(hits))
    # baseline = median pace over the surrounding ±5 min, floored so a dead chat doesn't make 3 messages a "spike"
    k = 30
    pad = np.pad(score, (k, k), mode="edge")
    base = np.median(np.lib.stride_tricks.sliding_window_view(pad, 2 * k + 1), axis=1)[:nb]
    base = np.maximum(base, max(2.0, float(np.median(score[score > 0])) if (score > 0).any() else 2.0) * 0.5)
    z = (score - base) / np.sqrt(base + 1.0)  # Poisson-ish: how unusual is this bucket
    ratio = score / base
    picked: list[int] = []
    for i in np.argsort(-z):
        i = int(i)
        if z[i] < 3.0 or len(picked) >= max_spikes:
            break
        if ratio[i] < 2.0 or count[i] < 8:
            continue
        if all(abs(i - p) * BUCKET >= min_gap for p in picked):
            picked.append(i)
    out = []
    for i in sorted(picked):
        top = Counter()
        for j in range(i, min(nb, i + 2)):
            top.update(hype_by[j])
        out.append({"t": i * BUCKET, "msgs": int(count[i]), "x": round(float(ratio[i]), 1),
                    "z": round(float(z[i]), 1),
                    "top": " ".join(f"{w.upper()}×{n}" for w, n in top.most_common(3))})
    return out


def windows(spks: list[dict], n: int, lead: int, tail: int, duration: float) -> list[tuple[float, float]]:
    """Time ranges to transcribe around the n strongest spikes, merged where they overlap."""
    best = sorted(spks, key=lambda s: -s["z"])[:n]
    spans = sorted((max(0.0, s["t"] - lead), min(duration, s["t"] + BUCKET + tail)) for s in best)
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 5:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a > 5]


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"
