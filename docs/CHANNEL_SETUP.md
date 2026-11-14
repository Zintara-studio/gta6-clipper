# Channel + ego lite setup (each person does this once for their own channel)

## 1. Create the channel
1. Use a Google account for the channel (a fresh one is fine).
2. youtube.com → profile picture → **Create a channel**. Name it, add a picture and a banner.
3. **Verify the channel by phone**: youtube.com/verify. Unverified channels hit the daily upload cap sooner.
4. Keep the channel's country set to where you actually live. The US/UK/CA/AU audience comes from English titles,
   US-voice hooks and US-time scheduling, not from the country setting (faking it causes YPP/payment problems).

## 2. YouTube Studio defaults
studio.youtube.com → **Settings**:
- **Upload defaults → Basic info**: Category **Gaming**, Video language **English**, Title/description language English.
- **Channel → Advanced settings → Audience**: **No, set this channel as not made for kids**.
- **General**: set your Studio language to **English (US)**. The uploader looks for English text such as "Daily upload limit".

## 3. ego lite
1. Install ego lite. On Mac, ask your agent: "install ego-browser". It follows the ego-browser skill's install guide
   (Windows works too).
2. Open ego lite and **sign in to the Google account that owns your channel** in the profile you'll use.
   If that isn't the default profile, run `ego-browser nodejs -e 'console.log(await profiles())'` and put its id in
   `config.yaml → upload.ego_profile`.
3. Check: open https://studio.youtube.com in ego lite. You should land on your channel dashboard.

## 4. First upload (supervised)
```bash
uv run clip run "https://www.youtube.com/watch?v=QdBZY2fkU-0"   # any GTA 6 video
uv run clip upload --check
```
`--check` fills in the title, description, tags, audience and schedule for ONE clip and then stops. Look at the dialog
in ego lite. If everything is right, click **Schedule** yourself. If a step failed, see "When the YouTube Studio upload
breaks" in AGENTS.md (or just ask your agent to fix it).

After that, `uv run clip auto` (or `clip harvest` + `clip upload`) runs unattended.

## Daily rhythm (example: you're in India, audience in the US)
- Morning/afternoon IST: `uv run clip auto` → clips get scheduled for 10 AM–11 PM US Eastern.
- Run it again later if the queue has more clips; it fills the next free US slots, up to `daily_target` per day.
