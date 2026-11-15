# gta6-clipper

Automatic GTA 6 Shorts factory. Point it at streams, trailers and news videos and it:

1. **transcribes** them (word-level timestamps, runs locally),
2. has **your coding agent** (Claude Code / Codex / Antigravity, whichever you're logged into) pick the moments most likely to go viral,
   using the transcript, loudness spikes and, for streams, **chat spikes** (when chat explodes, that's the clip),
3. **edits** each one in **Remotion**: 9:16 layout (fill / blurred background / facecam split), word-by-word pop captions,
   a hook box, zoom punches on loud moments, an AI US-voice hook for trailers/news, ducked background music, -14 LUFS audio,
4. **uploads** them to *your* channel through YouTube Studio in **ego lite**, scheduled across US prime time
   (10 AM–11 PM Eastern), ~25 a day.

No review step, no API keys, no Google Cloud project. Mac + Windows.

```
sources.yaml ─► harvest ─► yt-dlp ─► whisper ─► agent picks ─► ffmpeg + Remotion ─► queue ─► ego lite ─► YouTube Studio (scheduled)
```

## Setup (once)

**Step-by-step guide for friends: [SETUP.md](SETUP.md).** Short version:

```bash
git clone <this repo> && cd gta6-clipper
./setup.sh                                          # Mac (Homebrew)
powershell -ExecutionPolicy Bypass -File setup.ps1  # Windows (winget)
```

Then:
1. `config.yaml` → set `agent:` to `claude`, `codex` or `agy`.
2. `sources.yaml` → the streamers/channels **you** clip (pick a different lane from your friends).
3. Put no-copyright music in `assets/music/`.
4. Create your channel and log into it in ego lite: **[docs/CHANNEL_SETUP.md](docs/CHANNEL_SETUP.md)**.
5. Read **[docs/RULES.md](docs/RULES.md)** (copyright + monetisation, 2 minutes).

## Use

**Dashboard:** `uv run clip dashboard` opens a local page where you paste links, watch progress, preview and
retitle clips, and start uploads.

Or from the terminal:

```bash
uv run clip run "https://www.twitch.tv/videos/123456789" --type stream   # clip one video
uv run clip chat "https://www.twitch.tv/videos/123456789"   # just show where chat spiked (with timestamped links)
uv run clip harvest                 # clip everything new from sources.yaml
uv run clip upload --check          # first time: fill in one upload, you click Schedule
uv run clip upload                  # upload the next 15, scheduled for US prime time
uv run clip auto                    # harvest + upload: the daily command
uv run clip status                  # what's queued / uploaded
```

You can also open the folder in your agent and say *"clip this: <url>"* or *"run the daily clipping"*. `AGENTS.md`
tells it how.

## Tuning

Everything is in `config.yaml`: clip lengths per source type, `min_score`, `daily_target`, upload window, voice, music
volume, caption colours. The visuals live in `editor/src/Short.tsx`; preview them with `cd editor && npm run studio`.

## Chat spikes (Twitch VODs + YouTube past streams)

The best stream clip finder is the stream's own chat. For every Twitch VOD (and YouTube stream with a chat replay)
the clipper downloads the whole chat replay (no login, no extra app), counts messages per 10 seconds (LMAO / KEKW /
CLIP IT / 💀 count double) and flags the moments where chat suddenly moves several times faster than normal. Chat
reacts *after* the moment, so the agent is told to start each clip ~20 s before the spike.

On long streams this also saves most of the work: instead of transcribing the first 4 hours, only ~80 s around each
of the biggest spikes is downloaded and transcribed, across the **whole** stream. Already have a chat export from
[TwitchDownloader](https://github.com/lay295/TwitchDownloader)? `uv run clip run <vod-url> --chat chat.json`.
Settings: `chat:` in `config.yaml`. Only clip streamers who allow it (see `sources.yaml`).

## Speed (M-series Mac)

A 3-minute trailer takes about 1 minute to transcribe and pick, plus about 20 s of Remotion render per clip. A long
stream with chat takes a few minutes (chat download + ~16 short windows); without chat it is mostly transcription time.
Only the picked sections are downloaded as video.
