# Setup instructions

Follow these steps once and you'll have your own GTA 6 Shorts channel posting about 25 clips a day.
Plan on about 30–45 minutes, most of it waiting for downloads.

> **Repo owner:** the repo is private. Invite each friend first: GitHub → this repo → Settings → Collaborators → Add people.

---

## 0. What you need

| | Mac | Windows |
|---|---|---|
| Computer | Apple Silicon (M1 or newer) is best; Intel works but transcribes slower | Windows 10/11, 16 GB RAM recommended; an NVIDIA GPU makes transcription much faster |
| Disk space | ~10 GB free | ~10 GB free |
| Coding agent | **one** of: Claude Code (`claude`), Codex (`codex`), Antigravity (`agy`), installed and logged in | same |
| Accounts | a GitHub account (invited to this repo) and a Google account for your channel | same |

Check that your agent works. Open a terminal and run whichever one you have:

```bash
claude -p "say hi"
codex exec "say hi"
agy --print="say hi"
```

If it replies, you're good. If it asks you to log in, log in first.

---

## 1. Get the code

**Mac:** open Terminal.

```bash
cd ~
git clone https://github.com/imajij/gta6-clipper.git
cd gta6-clipper
```

**Windows:** open PowerShell. If `git` isn't installed, run `winget install -e --id Git.Git` and open a new PowerShell window.

```powershell
cd ~
git clone https://github.com/imajij/gta6-clipper.git
cd gta6-clipper
```

If git asks for a password, sign in with GitHub in the browser window it opens, or run `gh auth login` first if you have
the GitHub CLI.

---

## 2. Install everything (one command)

**Mac** (needs [Homebrew](https://brew.sh); install it first if `brew` isn't found):

```bash
./setup.sh
```

**Windows:**

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

This installs ffmpeg, uv (Python), Node.js, the Python packages and the Remotion editor, then creates your
`config.yaml` and `sources.yaml`. On Windows, if it stops after installing something, **close PowerShell, open a new
one** and run it again.

At the end you'll see a checklist like this:

```
  ✓ ffmpeg
  ✓ ffprobe
  ✓ agent: claude
  ✓ node (Remotion editor)
  ✓ editor deps
  ✗ ego-browser (uploads)      ← fixed in step 4
  ✓ caption font
  ! music tracks: 0            ← fixed in step 5
```

You can re-run the check any time with `uv run clip doctor`.

---

## 3. Set your config

Open **`config.yaml`** in any text editor and change:

```yaml
creator: yourname
agent: claude          # claude | codex | agy (whichever you tested in step 0)
daily_target: 25       # Shorts per day
```

Leave everything else alone for now.

Open **`sources.yaml`**. This is the list of channels and streamers *you* clip. **Agree with your friends on who clips
what**, so the channels don't all post the same moments. Example:

```yaml
sources:
  - name: Rockstar Games
    url: https://www.youtube.com/@RockstarGames/videos
    type: trailer
    layout: blur

  - name: SomeStreamer
    url: https://www.twitch.tv/somestreamer/videos?filter=archives
    type: stream
    layout: crop
    permission: "https://... link to their 'clipping allowed' message"
```

- `type`: `stream`, `gameplay`, `reaction`, `news` or `trailer`
- `layout`: `crop` (fills the screen), `blur` (full picture on a blurred background) or `facecam` (webcam on top, game below)
- For `facecam`, add the webcam position as fractions of the frame: `facecam: [x, y, width, height]`.
  Top-right cam ≈ `[0.72, 0.0, 0.28, 0.28]`.
- Only add streamers who allow clipping, and paste the proof in `permission`.

---

## 4. Install ego lite and log into your channel

ego lite is the browser that uploads to YouTube Studio for you.

**Mac:** the easiest way is to ask your coding agent: *"install ego-browser"*. Or download it from
<https://lite.ego.app/>, drag it to Applications and open it.

**Windows:** download the installer from <https://lite.ego.app/> and run it. If SmartScreen blocks it, click
**More info → Run anyway**.

Then, on both:
1. Open ego lite and finish the first-run onboarding (you can import from Chrome).
2. **Open a new terminal** and check: `ego-browser nodejs -e "console.log('ego-browser ready')"` should print `ego-browser ready`.
   - Mac, if it says "command not found": run `export PATH="$HOME/.local/bin:$PATH"` and try again.
3. In ego lite, go to <https://youtube.com>, **sign in with your channel's Google account**, and create the channel
   (profile picture → **Create a channel**). Add a name, picture and banner.
4. Verify the channel by phone: <https://youtube.com/verify>. Unverified channels hit the daily upload limit sooner.
5. Open <https://studio.youtube.com> → **Settings**:
   - **General**: language **English (US)**. The uploader reads English button text.
   - **Upload defaults**: Category **Gaming**, Video language **English**.
   - **Channel → Advanced settings → Audience**: **No, not made for kids**.
6. Keep your channel's country as your **real** country. The US audience comes from English titles, the US voice and
   US posting times, not from that setting.

If your channel is in a non-default ego lite profile, run `ego-browser nodejs -e "console.log(await profiles())"`, find the
id and put it in `config.yaml` under `upload: ego_profile: "<id>"`.

---

## 5. Add background music

Put a few `.mp3` files in **`assets/music/`**. Use only tracks you're allowed to use on YouTube:
**YouTube Studio → Audio library → filter "No attribution required"** → download 10–20 hype/phonk/electronic tracks.

Copyrighted songs get your Shorts claimed (you earn nothing from them) or blocked.

---

## 6. First test run (supervised)

Clip one video:

```bash
uv run clip run "https://www.youtube.com/watch?v=QdBZY2fkU-0"
```

The first run downloads the speech model and a headless Chrome for Remotion (~2 GB), so it's slow. Later runs are fast.
You should get something like:

```
  3 clips picked, editing in Remotion...
  ✓ #1 7/10  14.2s  GTA 6 opens with Lucia in prison, and her answer says it all
  ✓ #2 7/10  18.4s  GTA 6's "Trust?" moment right before the trailer ends
  rendered 3/3 in 68s
```

Watch the clips in `workspace/jobs/<video>/clips/clip_01.mp4`.

Now do **one supervised upload**:

```bash
uv run clip upload --check
```

ego lite opens YouTube Studio, uploads one clip and fills in the title, description, tags, audience and schedule time,
then **stops**. Look at the dialog in ego lite. If it's all correct, click **Schedule** yourself.

If any step failed, the error plus a screenshot are saved in `workspace/out/uploads/<time>/`. Open the repo in your agent
and say *"the upload check failed, fix the uploader"*. `AGENTS.md` tells it what to do.

---

## 7. Daily use

One command does everything (finds new videos, clips them, uploads and schedules for US prime time):

```bash
uv run clip auto
```

Other useful commands:

```bash
uv run clip harvest --dry-run      # what new videos would be clipped
uv run clip run "<url>"            # clip a specific video now
uv run clip upload                 # upload the next 15 ready clips
uv run clip upload --dry-run       # show what would be uploaded and when (US time)
uv run clip status                 # what's ready / uploaded
uv run clip skip 12 13             # never upload clips #12 and #13
```

Clips are scheduled between **10 AM and 11 PM US Eastern**, so you can run it whenever you like (in India, that's your
evening through your morning). Run `clip auto` once or twice a day.

You can also just tell your agent, with the repo open: *"run the daily clipping"* or *"clip this stream: <url>"*.

---

## 8. Getting updates

When the template gets improvements:

```bash
git pull
uv sync
cd editor && npm install && cd ..
```

Your `config.yaml`, `sources.yaml`, music and clips are never touched by updates.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `agent: claude ✗` in doctor | The CLI isn't on your PATH or isn't logged in. Re-run the step 0 test. |
| `ego-browser ✗` | Finish ego lite onboarding, then **open a new terminal**. Mac: `export PATH="$HOME/.local/bin:$PATH"`. |
| `0 clips picked` | Nothing scored above `min_score`. Try a livelier video, or lower `min_score` in `config.yaml` to 5. |
| `picker chunk failed` | Your agent CLI errored or hit a usage limit. Run its "say hi" test from step 0. |
| Voice hook skipped | edge-tts had a network hiccup. The clip still renders without the voice. |
| Upload says "daily upload limit reached" | YouTube's cap for your channel. Stop for today; verified, older channels get higher caps. |
| Upload fails at some step | Check the screenshot in `workspace/out/uploads/<time>/`, then ask your agent to fix the uploader (see step 6). |
| Very slow transcription on Windows | Set `whisper: model: base` in `config.yaml` (faster, slightly less accurate). |
| Remotion render fails | `cd editor && npm install`, then retry. Make sure you have ~3 GB free disk space. |

Read **[docs/RULES.md](docs/RULES.md)** before you start posting. It's 2 minutes on copyright and monetisation, and it
can save your channel.
