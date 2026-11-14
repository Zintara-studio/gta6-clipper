"""Word-level transcription: mlx-whisper on Apple Silicon, faster-whisper everywhere else."""
from __future__ import annotations

import importlib.util
import json
import platform
import sys
from pathlib import Path

MLX_REPOS = {
    "tiny": "mlx-community/whisper-tiny",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo": "mlx-community/whisper-large-v3-turbo",
}


def _use_mlx(backend: str) -> bool:
    if backend == "faster":
        return False
    apple = sys.platform == "darwin" and platform.machine() == "arm64"
    ok = apple and importlib.util.find_spec("mlx_whisper") is not None
    if backend == "mlx" and not ok:
        raise SystemExit("whisper.backend=mlx needs an Apple Silicon Mac (mlx-whisper).")
    return ok


def _cuda() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def transcribe(wav: Path, out_json: Path, cfg: dict) -> dict:
    if out_json.exists():
        return json.loads(out_json.read_text(encoding="utf-8"))
    w = cfg["whisper"]
    lang = w.get("language") or None
    words, segments = [], []

    if _use_mlx(w.get("backend", "auto")):
        import mlx_whisper

        model = w.get("model", "auto")
        repo = MLX_REPOS.get("large-v3-turbo" if model == "auto" else model, model)
        r = mlx_whisper.transcribe(str(wav), path_or_hf_repo=repo, word_timestamps=True,
                                   language=lang, condition_on_previous_text=False)
        for s in r.get("segments", []):
            segments.append({"s": round(s["start"], 2), "e": round(s["end"], 2), "text": s["text"].strip()})
            for x in s.get("words", []):
                words.append({"w": x["word"].strip(), "s": round(x["start"], 2), "e": round(x["end"], 2)})
    else:
        from faster_whisper import WhisperModel

        cuda = _cuda()
        model = w.get("model", "auto")
        if model == "auto":
            model = "large-v3-turbo" if cuda else "small"
        m = WhisperModel(model, device="cuda" if cuda else "cpu",
                         compute_type="float16" if cuda else "int8")
        segs, _ = m.transcribe(str(wav), word_timestamps=True, vad_filter=True, language=lang,
                               condition_on_previous_text=False)
        for s in segs:
            segments.append({"s": round(s.start, 2), "e": round(s.end, 2), "text": s.text.strip()})
            for x in s.words or []:
                words.append({"w": x.word.strip(), "s": round(x.start, 2), "e": round(x.end, 2)})

    words = [x for x in words if x["w"]]
    data = {"segments": segments, "words": words}
    out_json.write_text(json.dumps(data), encoding="utf-8")
    return data
