// Batch renderer: node render.mjs <batch.json>
// batch.json = { root: "<abs dir served over http>", crf, items: [{ props: {..., videoPath: "<path under root>"}, out: "<abs mp4>" }] }
// Bundles the Remotion project once, serves clip files from a local http server, renders every item (muted).
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const batch = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const root = path.resolve(batch.root);

const TYPES = { ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav" };

const server = http.createServer((req, res) => {
  const rel = decodeURIComponent(new URL(req.url, "http://x").pathname);
  const file = path.resolve(path.join(root, rel));
  if (!file.startsWith(root) || !fs.existsSync(file)) {
    res.writeHead(404).end();
    return;
  }
  const size = fs.statSync(file).size;
  const headers = { "Content-Type": TYPES[path.extname(file).toLowerCase()] || "application/octet-stream", "Accept-Ranges": "bytes", "Access-Control-Allow-Origin": "*" };
  const m = /bytes=(\d*)-(\d*)/.exec(req.headers.range || "");
  if (m) {
    const start = m[1] ? Number(m[1]) : 0;
    const end = m[2] ? Math.min(Number(m[2]), size - 1) : size - 1;
    res.writeHead(206, { ...headers, "Content-Range": `bytes ${start}-${end}/${size}`, "Content-Length": end - start + 1 });
    fs.createReadStream(file, { start, end }).pipe(res);
  } else {
    res.writeHead(200, { ...headers, "Content-Length": size });
    fs.createReadStream(file).pipe(res);
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;

// Remotion serves the bundle on :3000 by default; another dev server there breaks it, so pick a free port
const port = await new Promise((resolve) => {
  const probe = http.createServer().listen(0, () => {
    const p = probe.address().port;
    probe.close(() => resolve(p));
  });
});

const serveUrl = await bundle({ entryPoint: path.join(here, "src", "index.ts"), publicDir: path.join(here, "public") });

let failed = 0;
for (const item of batch.items) {
  const t = Date.now();
  try {
    const inputProps = { ...item.props, videoUrl: `${base}/${item.props.videoPath.split("/").map(encodeURIComponent).join("/")}` };
    const composition = await selectComposition({ serveUrl, id: "Short", inputProps, port, logLevel: "error" });
    await renderMedia({
      composition, serveUrl, inputProps, port, codec: "h264", muted: true,
      outputLocation: item.out, crf: batch.crf ?? 20, x264Preset: batch.preset ?? "veryfast",
      logLevel: "error",
    });
    console.log(JSON.stringify({ ok: true, out: item.out, seconds: Math.round((Date.now() - t) / 1000) }));
  } catch (e) {
    failed++;
    console.log(JSON.stringify({ ok: false, out: item.out, error: String(e?.stack || e).slice(0, 1500) }));
  }
}
server.close();
process.exit(failed && failed === batch.items.length ? 1 : 0);
