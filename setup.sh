#!/usr/bin/env bash
# macOS setup. Run once: ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v brew >/dev/null; then
  echo "Install Homebrew first: https://brew.sh" && exit 1
fi
for f in ffmpeg uv node; do
  command -v "$f" >/dev/null || brew install "$f"
done

uv sync
(cd editor && npm install --no-audit --no-fund)
uv run clip init

cat <<'EOF'

Setup done. Next:
  1. Edit config.yaml   (agent: claude | codex | agy)
  2. Edit sources.yaml  (streamers / channels to clip)
  3. Drop no-copyright music into assets/music/
  4. Install ego lite and log into YOUR YouTube channel in it (see docs/CHANNEL_SETUP.md)
  5. uv run clip run "<youtube or twitch url>"   then   uv run clip upload --check
EOF
