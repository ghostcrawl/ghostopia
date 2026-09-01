// ghostopia ghost-renderer — per-ghost canvas overlays (liveness cluster).
//
// The reusable, ORIGINAL graveyard-idiom overlays a GhostSprite wears: a pulsing spectral "!"
// ALERT when the ghost needs the operator, a bobbing Zzz over an idle ghost, a drifting WORKING
// dots bubble over a busy ghost, a map-clamped NAME TAG, a deterministic per-ghost IDENTITY
// BADGE (spooky dot/emoji), and a crisp SELECTION OUTLINE. The pure bits (filler-strip,
// deterministic badge, clamp math) are unit-tested here; the PixiJS draw funcs are thin and
// canvas-only.
//
// Imports only PixiJS. No SDK, no backend package, no server text authored here — the name
// tag renders ONLY the server-provided subject.

import { Container, Graphics, Text } from "pixi.js";

// --------------------------------------------------------------------------------------
// PURE — name-tag filler strip (unit-tested)
// --------------------------------------------------------------------------------------

/** Words a task/mission subject picks up that add no meaning to a floating name tag. */
const FILLER_WORDS = new Set([
  "the",
  "a",
  "an",
  "of",
  "for",
  "to",
  "and",
  "find",
  "search",
  "get",
  "fetch",
  "scrape",
  "crawl",
  "extract",
  "list",
  "me",
  "please",
  "all",
  "some",
  "www",
]);

/**
 * Reduce a task/mission subject to a SHORT, readable name-tag label. Strips a URL scheme + path
 * (keeping the host), drops filler words, collapses whitespace, and truncates. Deterministic
 * and pure so it is unit-testable; never authors copy — it only shortens the server subject.
 */
export function fillerStrip(subject: string, maxLen = 22): string {
  let s = (subject ?? "").trim();
  if (!s) return "";
  // URL → host (drop scheme + path/query).
  const urlMatch = /^[a-z][a-z0-9+.-]*:\/\/([^/\s?#]+)/i.exec(s);
  if (urlMatch) {
    s = urlMatch[1];
    if (s.toLowerCase().startsWith("www.")) s = s.slice(4);
  } else {
    // plain prose: drop filler words token-wise (keep original order + casing of keepers).
    const kept = s
      .split(/\s+/)
      .filter((w) => w.length > 0 && !FILLER_WORDS.has(w.toLowerCase()));
    if (kept.length > 0) s = kept.join(" ");
  }
  s = s.replace(/\s+/g, " ").trim();
  if (s.length > maxLen) s = `${s.slice(0, maxLen - 1).trimEnd()}…`;
  return s;
}

// --------------------------------------------------------------------------------------
// PURE — deterministic identity badge (unit-tested)
// --------------------------------------------------------------------------------------

/** A spectral badge: a stable colour + spooky glyph derived from the ghost id. */
export interface IdentityBadge {
  color: number;
  emoji: string;
}

/** The spooky glyph palette an identity badge draws from (graveyard idiom, original set). */
export const BADGE_EMOJI: readonly string[] = ["👻", "🎃", "🦇", "🕷️", "💀", "🕯️", "🌙", "⚰️"];

/** A cheap deterministic 32-bit string hash (FNV-1a) — stable across loads/agents. */
export function hashId(id: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/**
 * A stable per-ghost identity badge (colour + spooky glyph) derived ONLY from `ghostId`, so
 * the same ghost always reads the same everywhere (roster dot == canvas dot). Pure.
 */
export function identityBadge(ghostId: string): IdentityBadge {
  const h = hashId(ghostId);
  const hue = h % 360;
  const emoji = BADGE_EMOJI[(h >>> 9) % BADGE_EMOJI.length];
  return { color: hslToRgb(hue, 0.62, 0.62), emoji };
}

/** HSL (h in [0,360), s/l in [0,1]) → a packed 0xRRGGBB int. */
export function hslToRgb(h: number, s: number, l: number): number {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const hp = (((h % 360) + 360) % 360) / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  let r = 0;
  let g = 0;
  let b = 0;
  if (hp < 1) [r, g, b] = [c, x, 0];
  else if (hp < 2) [r, g, b] = [x, c, 0];
  else if (hp < 3) [r, g, b] = [0, c, x];
  else if (hp < 4) [r, g, b] = [0, x, c];
  else if (hp < 5) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  const m = l - c / 2;
  const to = (v: number): number => Math.max(0, Math.min(255, Math.round((v + m) * 255)));
  return (to(r) << 16) | (to(g) << 8) | to(b);
}

// --------------------------------------------------------------------------------------
// PURE — map clamp (unit-tested)
// --------------------------------------------------------------------------------------

/** Clamp a scalar into `[lo, hi]` (tolerates inverted bounds by pinning to `lo`). */
export function clampToBounds(v: number, lo: number, hi: number): number {
  if (!Number.isFinite(v)) return lo;
  if (hi < lo) return lo;
  return Math.min(hi, Math.max(lo, v));
}

// --------------------------------------------------------------------------------------
// PixiJS draw funcs (thin, canvas-only — not unit-tested)
// --------------------------------------------------------------------------------------

/** Build a spectral "!" alert marker container (a glowing disc + a bold "!"). */
export function makeAlertMarker(): Container {
  const c = new Container();
  c.label = "alert";
  const halo = new Graphics().circle(0, 0, 9).fill({ color: 0xe0574a, alpha: 0.28 });
  halo.blendMode = "add";
  const disc = new Graphics()
    .circle(0, 0, 6)
    .fill({ color: 0xe0574a, alpha: 0.9 })
    .stroke({ color: 0xffe9e6, width: 1, alpha: 0.9 });
  const bang = new Text({
    text: "!",
    style: { fontFamily: "ui-monospace, monospace", fontSize: 9, fontWeight: "900", fill: 0xfff4f2 },
  });
  bang.anchor.set(0.5);
  bang.resolution = 2;
  c.addChild(halo, disc, bang);
  return c;
}

/** Build a bobbing "Zzz" idle marker (three staggered z glyphs). */
export function makeZzz(): Container {
  const c = new Container();
  c.label = "zzz";
  const style = {
    fontFamily: "ui-monospace, monospace",
    fontWeight: "700" as const,
    fill: 0xbcc6ea,
  };
  const z0 = new Text({ text: "z", style: { ...style, fontSize: 7 } });
  const z1 = new Text({ text: "z", style: { ...style, fontSize: 9 } });
  const z2 = new Text({ text: "z", style: { ...style, fontSize: 11 } });
  z0.position.set(0, 6);
  z1.position.set(5, 1);
  z2.position.set(11, -5);
  for (const z of [z0, z1, z2]) z.resolution = 2;
  c.addChild(z0, z1, z2);
  return c;
}

/** Build a 3-dot "waiting" marker; `update(t)` animates the dot alphas (a marching pulse). */
export function makeWaitingDots(): { container: Container; update: (t: number) => void } {
  const c = new Container();
  c.label = "waiting";
  const dots = [0, 1, 2].map((i) => {
    const d = new Graphics().circle(0, 0, 1.6).fill({ color: 0xdfe7ff, alpha: 0.9 });
    d.position.set((i - 1) * 5, 0);
    c.addChild(d);
    return d;
  });
  const update = (t: number): void => {
    for (let i = 0; i < dots.length; i++) {
      const phase = (t / 260 - i * 0.5) % 3;
      dots[i].alpha = 0.25 + 0.7 * Math.max(0, Math.sin(phase * Math.PI));
    }
  };
  return { container: c, update };
}

/** The action kinds an action glyph can draw (P8 per-kind work poses). */
export const ACTION_GLYPH_KINDS = [
  "navigating",
  "searching",
  "reading",
  "scrolling",
  "extracting",
] as const;
export type ActionGlyphKind = (typeof ACTION_GLYPH_KINDS)[number];

/**
 * Build a small ORIGINAL spectral ACTION GLYPH that floats beside a working ghost so an
 * observer can tell WHAT it is doing: a compass arrow (navigating), a magnifier
 * (searching), a page (reading), up/down chevrons (scrolling), a down-tray (extracting). All
 * hand-authored vector marks in the graveyard idiom — no atlas frame, no reference art.
 */
export function makeActionGlyph(kind: ActionGlyphKind): Container {
  const c = new Container();
  c.label = `action.${kind}`;
  const ink = 0xbfe9ff;
  const glow = 0x8ff0ff;
  // a soft spectral disc behind every glyph so it reads on the dark ground
  const halo = new Graphics().circle(0, 0, 7).fill({ color: glow, alpha: 0.16 });
  halo.blendMode = "add";
  c.addChild(halo);
  const g = new Graphics();
  switch (kind) {
    case "navigating": {
      // a NE-pointing travel arrow
      g.moveTo(-4, 4).lineTo(4, -4).stroke({ color: ink, width: 1.6, alpha: 0.95 });
      g.moveTo(4, -4).lineTo(0, -4).moveTo(4, -4).lineTo(4, 0).stroke({ color: ink, width: 1.6, alpha: 0.95 });
      break;
    }
    case "searching": {
      // a magnifier: a ring + a handle
      g.circle(-1, -1, 3.2).stroke({ color: ink, width: 1.4, alpha: 0.95 });
      g.moveTo(1.4, 1.4).lineTo(4.5, 4.5).stroke({ color: ink, width: 1.6, alpha: 0.95 });
      break;
    }
    case "reading": {
      // a small page with text lines
      g.roundRect(-4, -5, 8, 10, 1).stroke({ color: ink, width: 1.2, alpha: 0.9 });
      for (const y of [-2.5, 0, 2.5]) g.moveTo(-2.5, y).lineTo(2.5, y).stroke({ color: ink, width: 1, alpha: 0.85 });
      break;
    }
    case "scrolling": {
      // up + down chevrons
      g.moveTo(-3, -1.5).lineTo(0, -4.5).lineTo(3, -1.5).stroke({ color: ink, width: 1.4, alpha: 0.95 });
      g.moveTo(-3, 1.5).lineTo(0, 4.5).lineTo(3, 1.5).stroke({ color: ink, width: 1.4, alpha: 0.95 });
      break;
    }
    case "extracting": {
      // a down-arrow dropping into a tray
      g.moveTo(0, -5).lineTo(0, 1).stroke({ color: ink, width: 1.6, alpha: 0.95 });
      g.moveTo(-2.5, -1.5).lineTo(0, 1).lineTo(2.5, -1.5).stroke({ color: ink, width: 1.6, alpha: 0.95 });
      g.moveTo(-4, 3).lineTo(-4, 5).lineTo(4, 5).lineTo(4, 3).stroke({ color: ink, width: 1.4, alpha: 0.9 });
      break;
    }
  }
  c.addChild(g);
  return c;
}

/** Build a name tag: an identity badge dot + the (already filler-stripped) label text. */
export function makeNameTag(label: string, badge: IdentityBadge): Container {
  const c = new Container();
  c.label = "nametag";
  const pad = 3;
  const dotR = 3;
  const text = new Text({
    text: label,
    style: {
      fontFamily: "ui-monospace, monospace",
      fontSize: 9,
      fontWeight: "600",
      fill: 0xeef0ff,
    },
  });
  text.resolution = 2;
  const tw = Math.ceil(text.width);
  const th = Math.ceil(text.height);
  const w = pad * 2 + dotR * 2 + 3 + tw;
  const h = pad * 2 + Math.max(th, dotR * 2);
  const bg = new Graphics()
    .roundRect(-w / 2, -h / 2, w, h, 4)
    .fill({ color: 0x141020, alpha: 0.82 })
    .stroke({ color: 0x2a2340, width: 1, alpha: 0.7 });
  const dot = new Graphics()
    .circle(-w / 2 + pad + dotR, 0, dotR)
    .fill({ color: badge.color, alpha: 0.95 });
  text.anchor.set(0, 0.5);
  text.position.set(-w / 2 + pad + dotR * 2 + 3, 0);
  c.addChild(bg, dot, text);
  return c;
}

/**
 * Draw/refresh a crisp selection outline into `g` — a rounded silhouette ring around the
 * ghost's footprint (an OutlineFilter-free approach that needs no per-ghost filter). `w`/`h`
 * are the sprite footprint; the ring sits at the sprite's bottom-centre anchor.
 */
export function drawSelectionOutline(g: Graphics, w: number, h: number): void {
  g.clear();
  const rw = Math.max(10, w) + 4;
  const rh = Math.max(10, h) + 4;
  g.roundRect(-rw / 2, -rh, rw, rh, 4)
    .stroke({ color: 0xbfe9ff, width: 1.5, alpha: 0.95 })
    .stroke({ color: 0x8ff0ff, width: 3, alpha: 0.22 });
}
