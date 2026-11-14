# Rules that keep the channels alive

This is a volume game, and each of these mistakes can end a channel.

**Copyright**
- Streamers: only clip people who allow it, and keep the proof in `sources.yaml → permission`. If you get a claim or
  strike, that proof is how you dispute it.
- Trailers and news: these clips get an AI voice hook plus captions, and trailer clips are capped at 25 s. Clips with no
  added commentary are the ones that get claimed. Three copyright strikes = channel deleted.
- Music: only royalty-free or Audio Library tracks (see `assets/music/README.md`).

**Monetisation (YPP)**
- YouTube demonetises "inauthentic / mass-produced, repetitive" content. The pipeline varies caption styles, colours,
  music and hooks per clip, and spreads sources. Don't undo that, and don't post the same moment twice.
- YPP needs 1,000 subscribers plus 10M valid Shorts views in 90 days (or 4,000 watch hours).
- Shorts pay far less per view than long videos. Volume helps; quality per clip still decides whether views come.

**Uploading with a browser script**
- YouTube's terms don't allow automated access, so channels using automation risk restrictions. Keep it human-paced:
  random pauses between uploads (`pause_seconds`), ≤ `daily_target` per day, scheduled publishing.
- If the uploader reports "daily upload limit reached", stop for the day. Pushing past it gets flagged.

**Between friends**
- Every friend runs their own channel. Don't all clip the same streamer and the same moments; split up `sources.yaml`
  so each channel has its own lane.
