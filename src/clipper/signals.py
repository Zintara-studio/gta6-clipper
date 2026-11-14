"""Cheap non-speech signals: loudness spikes (screams, explosions, hype moments)."""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np


def loudness_per_second(wav: Path) -> np.ndarray:
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        out = []
        while True:
            frames = w.readframes(sr)
            if not frames:
                break
            x = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
            out.append(20 * np.log10(max(rms, 1e-6)))
    return np.array(out, dtype=np.float32)


def find_peaks(db: np.ndarray, max_peaks: int = 40, min_gap: int = 20) -> list[dict]:
    if db.size < 10:
        return []
    # compare each second against its local 60 s context so a loud stream doesn't flag everything
    k = 60
    pad = np.pad(db, (k // 2, k // 2), mode="edge")
    local = np.convolve(pad, np.ones(k) / k, mode="valid")[: db.size]
    lift = db - local
    sd = float(np.std(lift)) or 1.0
    z = lift / sd
    order = np.argsort(-z)
    picked: list[int] = []
    for i in order:
        if z[i] < 2.0 or len(picked) >= max_peaks:
            break
        if all(abs(int(i) - p) >= min_gap for p in picked):
            picked.append(int(i))
    return [{"t": p, "z": round(float(z[p]), 1)} for p in sorted(picked)]


def compute(wav: Path, out_json: Path) -> dict:
    if out_json.exists():
        return json.loads(out_json.read_text(encoding="utf-8"))
    db = loudness_per_second(wav)
    data = {"duration": int(db.size), "peaks": find_peaks(db)}
    out_json.write_text(json.dumps(data), encoding="utf-8")
    return data
