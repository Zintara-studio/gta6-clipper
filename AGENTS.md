# gta6-clipper: instructions for coding agents (Claude Code, Codex, Antigravity)

> If you were started headless to pick clips (the prompt asks for JSON clips), ignore this file and just answer the prompt.

This repo turns GTA 6 videos (streams, trailers, news, reactions) into captioned vertical Shorts and uploads them to the
user's own YouTube channel through YouTube Studio in **ego lite**. Each person runs it on their own machine and channel.

## Commands (always via `uv run`)

| Command | What it does |
|---|---|
| `uv run clip doctor` | check ffmpeg, node, agent CLI, ego-browser, font, music |
| `uv run clip run <url-or-file> [--type stream\|gameplay\|reaction\|news\|trailer] [--layout crop\|blur\|facecam]` | clip one video |
| `uv run clip harvest [--dry-run] [--limit N]` | find new videos from `sources.yaml` and clip them |
| `uv run clip upload [--count N] [--now] [--check] [--dry-run]` | upload ready clips via ego lite, scheduled across US prime time |
| `uv run clip auto` | harvest + upload (the daily command) |
| `uv run clip status` / `uv run clip skip <ids>` | queue overview / never upload these clips |

## Pipeline (src/clipper/)

1. `fetch.py`: yt-dlp. Videos under 20 min are downloaded in full; long VODs get audio only, and just the picked sections are downloaded later.
2. `transcribe.py`: word timestamps (mlx-whisper on Apple Silicon, faster-whisper elsewhere). Cached in `transcript.json`.
3. `signals.py`: loudness spikes, used for picking and for zoom punches.
4. `pick.py` + `agents.py`: the transcript is split into 12-minute chunks, each chunk goes to the user's agent CLI headless with a JSON schema, and the results are cleaned (cuts snapped to word boundaries, length limits, de-duplication, `min_score`). Cached in `picks.json`.
5. `render.py`: ffmpeg cuts `segment.mp4` and mixes `audio.m4a` (voice hook from edge-tts, music ducked under speech, -14 LUFS).
   **Remotion** (`editor/`) renders the visuals (layout, captions, hook, zoom punches) in one batch, and ffmpeg muxes `clip_NN.mp4`.
6. `upload.py` + `uploader/youtube_studio.mjs`: builds a manifest with US Eastern publish slots, runs the script in `ego-browser nodejs`, and records results in `workspace/clipper.db`.

Everything generated lives in `workspace/` (git-ignored). `config.yaml`, `sources.yaml` and `assets/music/*` are personal and git-ignored.

## Interactive picking (when the user asks YOU to pick the clips)

1. `uv run clip run <url> --agent manual` writes `pick_prompt_NN.md` + `pick_schema.json` into the job folder (`workspace/jobs/<job>/`).
2. Read the prompts and write `picks_raw.json` (`{"clips": [...]}` matching the schema) into that folder.
3. `uv run clip render workspace/jobs/<job>`.

## When the YouTube Studio upload breaks

Studio changes its HTML sometimes. Every selector is in the `SEL` object at the top of `uploader/youtube_studio.mjs`.
A failed upload saves `clip_<id>.png` + `clip_<id>.txt` (error + page snapshot) in `workspace/out/uploads/<run>/`.
Read those, fix the selector in `SEL`, then re-test with `uv run clip upload --check` (fills in ONE upload and stops
before publishing, so the user can check it in ego lite). Use the `ego-browser` skill/guide (`ego-browser skill`) to
inspect the page if needed. Do not switch to the YouTube Data API; the user chose browser uploads.

## Editing the look

`editor/src/Short.tsx` holds all the visuals. `cd editor && npm run studio` opens Remotion Studio for a live preview
(it uses `defaultProps`, so point `videoUrl` at any mp4 URL). Keep `npx tsc --noEmit` clean.

## Rules

- Never commit `config.yaml`, `sources.yaml`, `workspace/` or music.
- Don't add sources with no permission note for streamers; don't strip the "Credit:" line from descriptions.
- Keep uploads paced (`pause_seconds`, `daily_target`). Bursts get channels flagged.
