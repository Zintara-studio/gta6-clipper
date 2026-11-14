// One vertical Short: background layout + zoom punches + hook box + word-by-word captions.
// Audio is mixed separately with ffmpeg (voice hook, ducked music, loudness) and muxed afterwards.
import React, { useMemo } from "react";
import {
  AbsoluteFill,
  OffthreadVideo,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont } from "@remotion/fonts";

loadFont({ family: "Anton", url: staticFile("Anton-Regular.ttf") });

export type Word = { w: string; s: number; e: number };
export type ShortProps = {
  videoUrl: string;
  srcWidth: number;
  srcHeight: number;
  duration: number;
  fps: number;
  layout: "blur" | "crop" | "facecam";
  facecam: [number, number, number, number] | null; // x, y, w, h as fractions of the frame
  words: Word[];
  hook: string;
  accent: string;
  captionStyle: "stroke" | "box";
  wordsPerGroup: number;
  punches: number[]; // seconds (loud moments) that get a quick zoom
};

export const defaultProps: ShortProps = {
  videoUrl: "",
  srcWidth: 1920,
  srcHeight: 1080,
  duration: 10,
  fps: 30,
  layout: "blur",
  facecam: null,
  words: [],
  hook: "",
  accent: "#FFE600",
  captionStyle: "stroke",
  wordsPerGroup: 3,
  punches: [],
};

const W = 1080;
const H = 1920;

// where captions / hook sit for each layout (px from top)
const POS = {
  blur: { caption: 1330, hook: 330 },
  crop: { caption: 1240, hook: 230 },
  facecam: { caption: 1380, hook: 560 },
} as const;

function zoomAt(t: number, punches: number[]): number {
  let z = 1;
  for (const p of punches) {
    const dt = t - p;
    if (dt < 0 || dt > 1.2) continue;
    const env = dt < 0.1 ? dt / 0.1 : Math.exp(-(dt - 0.1) * 5);
    z += 0.09 * env;
  }
  return z;
}

const Video: React.FC<{ src: string; style?: React.CSSProperties }> = ({ src, style }) => (
  <OffthreadVideo src={src} muted style={style} />
);

const Background: React.FC<{ p: ShortProps; zoom: number }> = ({ p, zoom }) => {
  if (p.layout === "crop") {
    return (
      <AbsoluteFill style={{ transform: `scale(${zoom})` }}>
        <Video src={p.videoUrl} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      </AbsoluteFill>
    );
  }

  if (p.layout === "facecam" && p.facecam) {
    const [x, y, fw, fh] = p.facecam;
    const camH = Math.round(H / 3);
    const cw = fw * p.srcWidth;
    const ch = fh * p.srcHeight;
    const s = Math.max(W / cw, camH / ch);
    const vw = p.srcWidth * s;
    const vh = p.srcHeight * s;
    const left = -x * p.srcWidth * s + (W - cw * s) / 2;
    const top = -y * p.srcHeight * s + (camH - ch * s) / 2;
    return (
      <AbsoluteFill style={{ backgroundColor: "black" }}>
        <div style={{ position: "absolute", top: 0, left: 0, width: W, height: camH, overflow: "hidden" }}>
          <Video src={p.videoUrl} style={{ position: "absolute", left, top, width: vw, height: vh }} />
        </div>
        <div
          style={{
            position: "absolute", top: camH, left: 0, width: W, height: H - camH, overflow: "hidden",
            borderTop: `6px solid ${p.accent}`,
          }}
        >
          <Video
            src={p.videoUrl}
            style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${zoom})` }}
          />
        </div>
      </AbsoluteFill>
    );
  }

  // blur: full frame centred on a blurred copy of itself
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <AbsoluteFill style={{ transform: "scale(1.2)", filter: "blur(36px) brightness(0.55) saturate(1.2)" }}>
        <Video src={p.videoUrl} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      </AbsoluteFill>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
        <div style={{ width: W, transform: `scale(${zoom * 1.06})` }}>
          <Video src={p.videoUrl} style={{ width: "100%", display: "block" }} />
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const Hook: React.FC<{ text: string; top: number }> = ({ text, top }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (!text.trim()) return null;
  const s = spring({ frame, fps, config: { damping: 14, stiffness: 160 } });
  return (
    <div
      style={{
        position: "absolute", top, left: 0, right: 0, display: "flex", justifyContent: "center",
        transform: `translateY(${interpolate(s, [0, 1], [-40, 0])}px) scale(${interpolate(s, [0, 1], [0.9, 1])})`,
        opacity: s,
      }}
    >
      <div
        style={{
          maxWidth: 920, padding: "14px 30px", background: "white", borderRadius: 18,
          fontFamily: "Anton", fontSize: 72, lineHeight: 1.1, color: "#111", textAlign: "center",
          textTransform: "uppercase", boxShadow: "0 10px 30px rgba(0,0,0,0.45)",
        }}
      >
        {text}
      </div>
    </div>
  );
};

type Group = { words: Word[]; start: number; end: number };

function groupWords(words: Word[], per: number, duration: number): Group[] {
  const groups: Word[][] = [];
  let cur: Word[] = [];
  words.forEach((w, i) => {
    cur.push(w);
    const next = words[i + 1];
    const sentence = /[.?!,]$/.test(w.w);
    const gap = next && next.s - w.e > 0.6;
    if (cur.length >= per || sentence || gap || !next) {
      groups.push(cur);
      cur = [];
    }
  });
  return groups.map((g, i) => {
    const nextStart = groups[i + 1]?.[0].s ?? duration;
    return { words: g, start: g[0].s, end: Math.min(nextStart, g[g.length - 1].e + 0.5) };
  });
}

const clean = (w: string) => w.replace(/[^\p{L}\p{N}'?!$%&-]/gu, "").toUpperCase();

const Captions: React.FC<{ p: ShortProps; top: number }> = ({ p, top }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const groups = useMemo(
    () => groupWords(p.words.filter((w) => clean(w.w)), p.wordsPerGroup, p.duration),
    [p.words, p.wordsPerGroup, p.duration],
  );
  const g = groups.find((x) => t >= x.start && t < x.end);
  if (!g) return null;
  const sinceStart = frame - Math.round(g.start * fps);
  const pop = spring({ frame: sinceStart, fps, config: { damping: 12, stiffness: 220 } });
  let active = 0;
  g.words.forEach((w, i) => {
    if (t >= w.s) active = i;
  });

  return (
    <div
      style={{
        position: "absolute", top, left: 60, right: 60, display: "flex", flexWrap: "wrap",
        justifyContent: "center", gap: "0 26px", transform: `scale(${interpolate(pop, [0, 1], [0.8, 1])})`,
      }}
    >
      {g.words.map((w, i) => {
        const on = i === active;
        const base: React.CSSProperties = {
          fontFamily: "Anton", fontSize: 104, lineHeight: 1.15, textTransform: "uppercase",
          display: "inline-block", transform: on ? "scale(1.12)" : "scale(1)",
        };
        if (p.captionStyle === "box") {
          return (
            <span
              key={i}
              style={{
                ...base, color: on ? "#111" : "white", background: on ? p.accent : "transparent",
                padding: "0 14px", borderRadius: 14,
                WebkitTextStroke: on ? "0px" : "12px black", paintOrder: "stroke fill",
                textShadow: on ? "none" : "0 8px 18px rgba(0,0,0,0.6)",
              }}
            >
              {clean(w.w)}
            </span>
          );
        }
        return (
          <span
            key={i}
            style={{
              ...base, color: on ? p.accent : "white", WebkitTextStroke: "14px black",
              paintOrder: "stroke fill", textShadow: "0 8px 18px rgba(0,0,0,0.6)",
            }}
          >
            {clean(w.w)}
          </span>
        );
      })}
    </div>
  );
};

export const Short: React.FC<ShortProps> = (p) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const zoom = zoomAt(frame / fps, p.punches);
  const pos = POS[p.layout] ?? POS.blur;
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <Background p={p} zoom={zoom} />
      <Hook text={p.hook} top={pos.hook} />
      <Captions p={p} top={pos.caption} />
    </AbsoluteFill>
  );
};
