// Runs inside `ego-browser nodejs`. `clip upload` prepends: const MANIFEST_PATH = "<abs path>";
// Fills YouTube Studio's upload dialog for every item in the manifest and writes results.json.
//
// Studio changes its markup now and then. Every selector lives in SEL below. If a step breaks,
// look at the screenshot + snapshot saved in the run folder and update SEL (agents: see AGENTS.md).

const SEL = {
  fileInput: ["ytcp-uploads-file-picker input[type=file]", "input[type=file]"],
  title: ["#title-textarea #textbox", "ytcp-social-suggestions-textbox#title-textarea #textbox"],
  description: ["#description-textarea #textbox", "ytcp-social-suggestions-textbox#description-textarea #textbox"],
  notForKids: ['tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]'],
  showMore: ["#toggle-button"],
  tagsInput: ['input[aria-label="Tags"]', "#tags-container input", "ytcp-video-metadata-editor-basics #text-input"],
  alteredNo: ['tp-yt-paper-radio-button[name="VIDEO_HAS_ALTERED_CONTENT_NO"]'],
  next: ["#next-button"],
  public: ['tp-yt-paper-radio-button[name="PUBLIC"]'],
  scheduleExpand: ["#second-container-expand-button"],
  dateTrigger: ["#datepicker-trigger"],
  dateInput: ["tp-yt-paper-dialog ytcp-date-picker tp-yt-paper-input input", "ytcp-date-picker input", "#datepicker-trigger input"],
  timeInput: ["#time-of-day-container input", "ytcp-datetime-picker #time-of-day-container input", "#time-of-day-trigger input"],
  videoLink: [".video-url-fadeable a", "ytcp-video-info a", "a.ytcp-video-info"],
  progress: ["ytcp-video-upload-progress", ".progress-label"],
  done: ["#done-button"],
  close: ["ytcp-uploads-still-processing-dialog #close-button", "ytcp-video-share-dialog #close-button", "#close-button"],
};

const fs = await import("node:fs/promises");
const path = await import("node:path");
const manifest = JSON.parse(await fs.readFile(MANIFEST_PATH, "utf8"));
const results = [];
const saveResults = () => fs.writeFile(manifest.resultsPath, JSON.stringify(results, null, 2));
const log = (...a) => console.log("[uploader]", ...a);

const task = await taskSpace(manifest.spaceName, manifest.profileId ? { profileId: manifest.profileId } : {});
const managed = await task.pages();
const page = managed.length ? managed[0] : await task.newPage();
log(`task space ${task.spaceId}, page ${page.label}`);

const sleep = (ms) => page.waitForTimeout(ms);

async function visible(cands, timeout = 5000) {
  const end = Date.now() + timeout;
  while (true) {
    const hit = await page.evaluate((list) => {
      for (const s of list) {
        const el = document.querySelector(s);
        if (!el) continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) return s;
      }
      return null;
    }, cands);
    if (hit || Date.now() > end) return hit;
    await sleep(300);
  }
}

async function attached(cands, timeout = 5000) {
  const end = Date.now() + timeout;
  while (true) {
    const hit = await page.evaluate((list) => list.find((s) => document.querySelector(s)) || null, cands);
    if (hit || Date.now() > end) return hit;
    await sleep(300);
  }
}

async function click(cands, label, timeout = 8000) {
  const s = await visible(cands, timeout);
  if (!s) throw new Error(`not found: ${label} (${cands[0]})`);
  try {
    await page.click(s, { label });
  } catch (e) {
    // ambiguous or covered: fall back to a DOM click on the first visible match
    await page.evaluate((sel) => {
      const el = [...document.querySelectorAll(sel)].find((n) => n.getBoundingClientRect().width > 0);
      el?.click();
    }, s);
  }
  return s;
}

async function typeInto(cands, text, label) {
  await click(cands, label, 15000);
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.press("Backspace");
  await page.keyboard.insertText(text);
}

async function bodyText() {
  return page.evaluate(() => document.body?.innerText?.slice(0, 20000) || "");
}

async function debugDump(id, err) {
  try {
    await page.screenshot({ path: path.join(manifest.debugDir, `clip_${id}.png`) });
    await fs.writeFile(path.join(manifest.debugDir, `clip_${id}.txt`), `${err}\n\n${await page.snapshot()}`);
  } catch {}
}

async function uploadOne(item) {
  await page.goto("https://www.youtube.com/upload", { timeout: 90000 });
  await sleep(2500);
  const url = await page.url();
  if (/accounts\.google\.com|ServiceLogin/.test(url)) throw Object.assign(new Error("not logged in"), { fatal: true });

  const input = await attached(SEL.fileInput, 60000);
  if (!input) throw new Error("upload file picker not found");
  await page.setInputFiles(input, [item.file]);

  const titleSel = await visible(SEL.title, 120000);
  if (!titleSel) {
    const t = await bodyText();
    if (/daily upload limit/i.test(t)) throw Object.assign(new Error("daily upload limit reached"), { fatal: true });
    throw new Error("details form did not open");
  }
  await sleep(1500);
  await typeInto(SEL.title, item.title, "set title");
  await typeInto(SEL.description, item.description, "set description");
  await click(SEL.notForKids, "not made for kids", 15000);

  if (item.tags) {
    try {
      await click(SEL.showMore, "show more options", 5000);
      const tagSel = await visible(SEL.tagsInput, 6000);
      if (tagSel) {
        await page.click(tagSel, { label: "tags field" });
        await page.keyboard.insertText(item.tags + ",");
      }
      if (await visible(SEL.alteredNo, 2000)) await click(SEL.alteredNo, "no altered content", 2000);
    } catch (e) {
      log(`  tags skipped: ${e.message}`);
    }
  }

  for (let i = 0; i < 3; i++) {
    await click(SEL.next, "next step", 15000);
    await sleep(1500);
  }

  if (item.publishDate) {
    await click(SEL.scheduleExpand, "open schedule", 15000);
    await click(SEL.dateTrigger, "open date picker", 10000);
    const d = await visible(SEL.dateInput, 8000);
    if (!d) throw new Error("schedule date input not found");
    await page.fill(d, item.publishDate);
    await page.press(d, "Enter");
    await sleep(800);
    const t = await visible(SEL.timeInput, 8000);
    if (!t) throw new Error("schedule time input not found");
    await page.fill(t, item.publishTime);
    await page.press(t, "Enter");
    await sleep(800);
  } else {
    await click(SEL.public, "public", 15000);
  }

  const link = await page.evaluate((list) => {
    for (const s of list) {
      const a = document.querySelector(s);
      if (a?.href) return a.href;
    }
    return "";
  }, SEL.videoLink);

  // let the file finish uploading before publishing (Shorts are small; cap at 15 min)
  await page.waitForFunction(
    (list) => {
      const el = list.map((s) => document.querySelector(s)).find(Boolean);
      const t = el?.textContent || "";
      return !/Uploading|Uploaded \d+%|\d+% uploaded/i.test(t);
    },
    SEL.progress,
    { timeout: 900000 },
  );

  if (manifest.checkMode) {
    log(`  CHECK MODE: everything is filled in. Look at the dialog in ego lite, then click ` +
        `${item.publishDate ? "Schedule" : "Publish"} yourself.`);
    return { url: link, handedOff: true };
  }

  await click(SEL.done, item.publishDate ? "schedule video" : "publish video", 15000);
  await sleep(3000);
  const t = await bodyText();
  if (/daily upload limit/i.test(t)) throw Object.assign(new Error("daily upload limit reached"), { fatal: true });
  const closeSel = await visible(SEL.close, 20000);
  if (closeSel) await click(SEL.close, "close dialog", 5000);
  return { url: link };
}

let stoppedForUser = false;
for (const [i, item] of manifest.items.entries()) {
  log(`(${i + 1}/${manifest.items.length}) #${item.id} ${item.title}`);
  try {
    const r = await uploadOne(item);
    results.push({ id: item.id, ok: true, url: r.url });
    log(`  ok ${r.url || ""}`);
    await saveResults();
    if (r.handedOff) {
      await task.handOff();
      stoppedForUser = true;
      break;
    }
  } catch (e) {
    log(`  FAILED: ${e.message}`);
    await debugDump(item.id, e.stack || e.message);
    results.push({ id: item.id, ok: false, error: e.message });
    await saveResults();
    if (e.fatal) break;
  }
  if (i < manifest.items.length - 1) {
    const [lo, hi] = manifest.pauseSeconds;
    await sleep(1000 * (lo + Math.random() * (hi - lo)));
  }
}

await saveResults();
if (!stoppedForUser) await task.finish({ keep: [] });
log("done");
