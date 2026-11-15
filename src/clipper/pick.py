"""Choose the best moments: transcript + loudness peaks -> agent -> cleaned, deduped picks."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .agents import AgentError, run_agent

CHUNK_SECONDS = 12 * 60

CLIP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["clips"],
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["start", "end", "score", "title", "hook", "voice_hook", "description", "tags", "reason"],
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "score": {"type": "number"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "voice_hook": {"type": "string"},
                    "description": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}

TYPE_NOTES = {
    "stream": "a GTA 6 livestream VOD. Funny fails, chaos, rage, big reactions, clutch escapes and wild NPC moments do best.",
    "gameplay": "GTA 6 gameplay. Chaos, fails, stunts, crazy physics and funny NPC moments do best.",
    "reaction": "a reaction or commentary video about GTA 6. The strongest opinions, hype and laughs do best.",
    "news": "a GTA 6 news or leaks video. Concrete reveals, surprising facts, release info and price/map/feature details do best.",
    "trailer": "an official GTA 6 trailer. Little speech, so use the loud moments and the dialogue lines. Pick short iconic beats.",
}


def _fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def _inside(t: float, spans: list[tuple[float, float]]) -> bool:
    return any(a <= t < b for a, b in spans)


def build_prompt(title: str, source_type: str, segments: list[dict], peaks: list[dict],
                 spans: list[tuple[float, float]], want: int, lmin: int, lmax: int,
                 avoid: list[tuple[float, float]], chat: list[dict] | None = None) -> str:
    lines = [f"[{s['s']:.1f}-{s['e']:.1f}] {s['text']}" for s in segments
             if any(s["e"] > a and s["s"] < b for a, b in spans)]
    loud = [f"{p['t']}s (+{p['z']}σ)" for p in peaks if _inside(p["t"], spans)]
    hot = [f"{c['t']}s = {_fmt(c['t'])} ({c['msgs']} msgs/10s, {c['x']}x normal{', ' + c['top'] if c['top'] else ''})"
           for c in (chat or []) if _inside(c["t"], spans)]
    avoid_txt = ", ".join(f"{a:.0f}-{b:.0f}s" for a, b in avoid) or "none"
    where = (f"Section you are looking at: {_fmt(spans[0][0])} to {_fmt(spans[0][1])}." if len(spans) == 1 else
             "This is a long stream. You only see the parts around chat spikes: "
             + ", ".join(f"{_fmt(a)}-{_fmt(b)}" for a, b in spans) + ". Only pick clips inside these parts.")
    chat_txt = ""
    if chat is not None:
        chat_txt = f"""
CHAT SPIKES (live chat suddenly much faster than normal; the best sign that something clip-worthy happened):
{chr(10).join(hot) or "none in this section"}
Chat reacts AFTER the moment: for a spike at T the moment itself is usually 5-25 s before T. Start the clip right
before the setup (typically ~20 s before T) and end just after the payoff. Prefer moments with a chat spike.

"""
    return f"""You are the clip editor for a YouTube Shorts channel about GTA 6 aimed at viewers in the USA,
Canada, UK and Australia. Your job: find the moments in this video most likely to go viral as Shorts.

VIDEO: "{title}"
This is {TYPE_NOTES.get(source_type, TYPE_NOTES['stream'])}
{where}

TRANSCRIPT (seconds from start of video):
{chr(10).join(lines) or "(no speech detected)"}

LOUDNESS SPIKES (screams, explosions, hype, music hits): {", ".join(loud) or "none"}
{chat_txt}ALREADY CLIPPED, DO NOT OVERLAP: {avoid_txt}

RULES
- Return up to {want} clips, best first. Fewer is fine; return an empty list if nothing is good.
- Each clip must be {lmin}-{lmax} seconds and make sense to someone who never saw the video.
- Start right before the setup (no dead air), end right after the payoff/punchline. Use transcript timestamps.
- score: 1-10 for how likely a US viewer stops scrolling and watches to the end. Be strict; 8+ is rare.
- title: max 70 characters, American English, curiosity-driven, must contain "GTA 6". No fake claims, no ALL CAPS,
  at most one emoji. No hashtags in the title.
- hook: 2-6 word on-screen text shown at the top for the whole clip (e.g. "HE DID NOT SEE THAT COMING").
- voice_hook: one punchy sentence (max 14 words) a narrator says over the first seconds, adding context or a take
  (e.g. "Rockstar hid this detail in the trailer and nobody noticed."). Write it even if it may not be used.
- description: 1-2 short sentences that add context. No hashtags (they are added automatically).
- tags: 5-10 lowercase search tags (e.g. "gta 6 funny moments", "gta 6 leonida").
- reason: a few words on why this moment works.
- Never pick moments with slurs, sexual content, or real-world violence.
- Skip moments whose main appeal is a licensed song or music video (instant copyright claim). Speech, action and reveals only.
"""


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / max(1e-6, min(a[1] - a[0], b[1] - b[0]))


def _snap(words: list[dict], start: float, end: float) -> tuple[float, float]:
    """Move cut points so they don't chop a word in half."""
    for w in words:
        if w["s"] <= start < w["e"]:
            start = w["s"]
            break
    for w in words:
        if w["s"] < end <= w["e"]:
            end = w["e"]
            break
    return max(0.0, start - 0.15), end + 0.35


def clean(raw: list[dict], words: list[dict], duration: float, lmin: int, lmax: int,
          min_score: float, cap: int, avoid: list[tuple[float, float]]) -> list[dict]:
    out: list[dict] = []
    for c in sorted(raw, key=lambda c: -float(c.get("score", 0))):
        if float(c.get("score", 0)) < min_score:
            continue
        s, e = float(c["start"]), float(c["end"])
        if e <= s or s >= duration:
            continue
        s, e = _snap(words, s, min(e, duration))
        if e - s > lmax:
            e = s + lmax
        if e - s < lmin:
            e = min(duration, s + lmin)
            if e - s < lmin:
                s = max(0.0, e - lmin)
        if e - s < min(lmin, 6):
            continue
        rng = (s, e)
        if any(_overlap(rng, a) > 0.3 for a in avoid) or any(_overlap(rng, (o["start"], o["end"])) > 0.3 for o in out):
            continue
        c = {**c, "start": round(s, 2), "end": round(e, 2), "title": c["title"].strip()[:95]}
        out.append(c)
        if len(out) >= cap:
            break
    return out


def chunk_spans(duration: float, windows: list | None) -> list[list[tuple[float, float]]]:
    """What each agent call looks at: 12-minute chunks, or (chat mode) groups of chat-spike windows."""
    if not windows:
        n = max(1, math.ceil(duration / CHUNK_SECONDS))
        return [[(i * CHUNK_SECONDS, min(duration, (i + 1) * CHUNK_SECONDS + 30))] for i in range(n)]
    out, cur, size = [], [], 0.0
    for a, b in windows:
        if cur and size + (b - a) > CHUNK_SECONDS:
            out.append(cur)
            cur, size = [], 0.0
        cur.append((float(a), float(b)))
        size += b - a
    return out + ([cur] if cur else [])


def pick(job: Path, meta: dict, source_type: str, transcript: dict, signals: dict, cfg: dict,
         avoid: list[tuple[float, float]], agent: str | None = None) -> list[dict]:
    cache = job / "picks.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    duration = float(signals.get("duration") or meta.get("duration") or 0)
    lmin, lmax = cfg["clip_length"].get(source_type, [15, 55])
    cap = int(cfg["clips_per_video_max"])
    batches = chunk_spans(duration, signals.get("windows"))
    n_chunks = len(batches)
    want = max(2, math.ceil(cap * 1.5 / n_chunks))
    agent = agent or cfg["agent"]

    raw: list[dict] = []
    for i, spans in enumerate(batches):
        prompt = build_prompt(meta["title"], source_type, transcript["segments"], signals["peaks"],
                              spans, want, lmin, lmax, avoid, signals.get("chat"))
        if agent == "manual":
            (job / f"pick_prompt_{i:02d}.md").write_text(prompt, encoding="utf-8")
            continue
        try:
            res = run_agent(agent, prompt, CLIP_SCHEMA, cfg.get("agent_model", ""), job)
            raw += res.get("clips", [])
        except AgentError as e:
            print(f"  ! picker chunk {i + 1}/{n_chunks} failed: {e}")

    if agent == "manual":
        (job / "pick_schema.json").write_text(json.dumps(CLIP_SCHEMA, indent=2), encoding="utf-8")
        raise ManualPick(job)

    if signals.get("windows"):  # chat mode: only the windows were transcribed, so clips must sit inside them
        wins = [(a - 10, b + 10) for a, b in signals["windows"]]
        raw = [c for c in raw if _inside((float(c["start"]) + float(c["end"])) / 2, wins)]
    picks = clean(raw, transcript["words"], duration, lmin, lmax, float(cfg["min_score"]), cap, avoid)
    cache.write_text(json.dumps(picks, indent=2), encoding="utf-8")
    return picks


class ManualPick(Exception):
    """Raised when the picker is 'manual': prompts are written, an agent must write picks_raw.json."""


def load_manual(job: Path, transcript: dict, duration: float, source_type: str, cfg: dict,
                avoid: list[tuple[float, float]]) -> list[dict]:
    raw = json.loads((job / "picks_raw.json").read_text(encoding="utf-8"))
    raw = raw.get("clips", raw) if isinstance(raw, dict) else raw
    lmin, lmax = cfg["clip_length"].get(source_type, [15, 55])
    picks = clean(raw, transcript["words"], duration, lmin, lmax, float(cfg["min_score"]),
                  int(cfg["clips_per_video_max"]), avoid)
    (job / "picks.json").write_text(json.dumps(picks, indent=2), encoding="utf-8")
    return picks
