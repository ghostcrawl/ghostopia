// ghostopia ghost-art — atlas build + preview generator (art:atlas).
//
// Reads the ORIGINAL JSON grids + manifest + palettes from ghostopia/assets,
// packs a PixiJS-consumable atlas (buildAtlas), and bakes two PNGs into the
// gitignored dist/ dir for the STAGE-0 art vibe-check:
//   - atlas.png    : the raw packed texture atlas
//   - preview.png  : the ONE ghost recolored across every section palette x a
//                    few status tints, at 4x nearest-neighbour, plus a strip of
//                    the graveyard tileset / terminals / landmarks / overlays.
// It imports ONLY this package's own runtime (no SDK, no backend).

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import zlib from "node:zlib";

import {
  expandGrid,
  resolveGrid,
  recolor,
  applyStatusTint,
  outline,
  loadManifest,
  loadPalettes,
  loadAnimations,
  loadPropCatalog,
  resolvePropRegion,
  buildAtlas,
  makeRaster,
} from "../dist/index.js";

const here = dirname(fileURLToPath(import.meta.url));
const pkgRoot = resolve(here, "..");
const assets = resolve(pkgRoot, "../../assets");
const outDir = resolve(pkgRoot, "dist");

const readJson = (p) => JSON.parse(readFileSync(p, "utf8"));

// ---- load original assets ----
const gridFiles = [
  "ghost/ghost.grids.json",
  "world/tiles.grids.json",
  "world/landmarks.grids.json",
  "world/sky.grids.json",
  "world/critters.grids.json",
  "world/props.grids.json",
  "overlays/overlays.grids.json",
];
const grids = {};
for (const f of gridFiles) {
  const doc = readJson(resolve(assets, f));
  for (const [name, compact] of Object.entries(doc.grids)) {
    grids[name] = expandGrid(compact);
  }
}
const manifest = loadManifest(readJson(resolve(assets, "sprites.manifest.json")), Object.keys(grids));
const palettes = loadPalettes(readJson(resolve(assets, "palettes.json")));
// the clip layer — throws loudly if any clip frame references a missing region
const animations = loadAnimations(readJson(resolve(assets, "animations.json")), manifest);
console.log(`animations: ${Object.keys(animations).length} clips (all frames resolve)`);
// the placeable-prop catalog — throws if any prop's grid/region/clip ref is missing
const propCatalog = loadPropCatalog(
  readJson(resolve(assets, "props.catalog.json")),
  manifest,
  animations,
);
console.log(`props: ${Object.keys(propCatalog.props).length} catalogued (all refs resolve)`);

// ---- rectangularity guard (loud, but non-fatal — resolveGrid pads) ----
for (const [name, g] of Object.entries(grids)) {
  const w = Math.max(...g.pixels.map((r) => r.length));
  const ragged = g.pixels.filter((r) => r.length !== w).length;
  if (ragged) console.warn(`  note: grid "${name}" has ${ragged} ragged row(s) (padded to ${w}px)`);
}

// ---- pack the atlas ----
const atlas = buildAtlas(manifest, grids);
console.log(`atlas: ${atlas.width}x${atlas.height}px, ${Object.keys(atlas.frames).length} frames`);

// ---- raster helpers ----
function crop(src, x, y, w, h) {
  const dst = makeRaster(w, h);
  for (let j = 0; j < h; j++) {
    for (let i = 0; i < w; i++) {
      const si = ((y + j) * src.width + (x + i)) * 4;
      const di = (j * w + i) * 4;
      dst.data[di] = src.data[si];
      dst.data[di + 1] = src.data[si + 1];
      dst.data[di + 2] = src.data[si + 2];
      dst.data[di + 3] = src.data[si + 3];
    }
  }
  return dst;
}
function blit(dst, src, ox, oy, scale = 1) {
  for (let y = 0; y < src.height; y++) {
    for (let x = 0; x < src.width; x++) {
      const si = (y * src.width + x) * 4;
      if (src.data[si + 3] === 0) continue;
      for (let sy = 0; sy < scale; sy++) {
        for (let sx = 0; sx < scale; sx++) {
          const dx = ox + x * scale + sx;
          const dy = oy + y * scale + sy;
          if (dx < 0 || dy < 0 || dx >= dst.width || dy >= dst.height) continue;
          const di = (dy * dst.width + dx) * 4;
          dst.data[di] = src.data[si];
          dst.data[di + 1] = src.data[si + 1];
          dst.data[di + 2] = src.data[si + 2];
          dst.data[di + 3] = src.data[si + 3];
        }
      }
    }
  }
}

// ---- minimal PNG (RGBA, filter 0) ----
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const typeBuf = Buffer.from(type, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0);
  return Buffer.concat([len, typeBuf, data, crc]);
}
function encodePng(raster) {
  const { width, height, data } = raster;
  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (stride + 1)] = 0; // filter: none
    for (let x = 0; x < stride; x++) raw[y * (stride + 1) + 1 + x] = data[y * stride + x];
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // colour type RGBA
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  return Buffer.concat([
    sig,
    chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// ---- compose the preview ----
const BG = [22, 18, 34, 255];
function fill(r, c) {
  for (let i = 0; i < r.width * r.height; i++) {
    r.data[i * 4] = c[0];
    r.data[i * 4 + 1] = c[1];
    r.data[i * 4 + 2] = c[2];
    r.data[i * 4 + 3] = c[3];
  }
}

// ---- region + transform helpers (v2 dynamic preview) ----
const resolvedGridCache = {};
function gridOf(name) {
  if (!resolvedGridCache[name]) resolvedGridCache[name] = resolveGrid(grids[name]);
  return resolvedGridCache[name];
}
function cropRegion(sprite, region) {
  const entry = manifest.sprites[sprite];
  const r = entry.regions[region];
  if (!r) throw new Error(`preview: missing region ${sprite}:${region}`);
  return crop(
    gridOf(entry.grid),
    r.col * entry.cellWidth,
    r.row * entry.cellHeight,
    r.cols * entry.cellWidth,
    r.rows * entry.cellHeight,
  );
}
function flipH(src) {
  const dst = makeRaster(src.width, src.height);
  for (let y = 0; y < src.height; y++) {
    for (let x = 0; x < src.width; x++) {
      const si = (y * src.width + x) * 4;
      const di = (y * src.width + (src.width - 1 - x)) * 4;
      dst.data[di] = src.data[si];
      dst.data[di + 1] = src.data[si + 1];
      dst.data[di + 2] = src.data[si + 2];
      dst.data[di + 3] = src.data[si + 3];
    }
  }
  return dst;
}
function band(dst, x, y, w, h, c) {
  for (let j = 0; j < h; j++) {
    for (let i = 0; i < w; i++) {
      const dx = x + i, dy = y + j;
      if (dx < 0 || dy < 0 || dx >= dst.width || dy >= dst.height) continue;
      const di = (dy * dst.width + dx) * 4;
      dst.data[di] = c[0];
      dst.data[di + 1] = c[1];
      dst.data[di + 2] = c[2];
      dst.data[di + 3] = c[3];
    }
  }
}

const preview = makeRaster(520, 820);
fill(preview, BG);
const HDR = [90, 80, 130, 255];

// ==== ROW 1 — the 8-way TURN RING ("the 360") at idle frame 0 ====
// 5 authored facings (s,se,e,ne,n) + 3 mirrored (sw,w,nw = east art flipped).
band(preview, 12, 18, 200, 2, HDR);
const RS = 4;
const ringCellW = 12 * RS + 8;
const ringCellH = 14 * RS + 8;
const ringX = 24;
const ringY = 28;
// compass -> 3x3 cell (col,row); center is empty
const compass = {
  nw: [0, 0], n: [1, 0], ne: [2, 0],
  w: [0, 1], e: [2, 1],
  sw: [0, 2], s: [1, 2], se: [2, 2],
};
const MIRROR_SRC = { sw: "se", w: "e", nw: "ne" };
for (const [facing, [cx, cy]] of Object.entries(compass)) {
  const authored = MIRROR_SRC[facing];
  let r = cropRegion("ghost", `${authored ?? facing}_idle0`);
  if (authored) r = flipH(r);
  r = outline(r);
  blit(preview, r, ringX + cx * ringCellW, ringY + cy * ringCellH, RS);
}

// ==== ROW 2 — a MOVE strip (one facing's move0..3 = a walk cycle) ====
const moveY = ringY + 3 * ringCellH + 14;
band(preview, 12, moveY - 10, 240, 2, HDR);
const moveFacing = "e"; // profile reads clearest as a walk
for (let i = 0; i < 4; i++) {
  const r = outline(cropRegion("ghost", `${moveFacing}_move${i}`));
  blit(preview, r, 24 + i * (12 * RS + 10), moveY, RS);
}
// same walk mirrored (the west-side render) beside it
for (let i = 0; i < 4; i++) {
  const r = outline(flipH(cropRegion("ghost", `${moveFacing}_move${i}`)));
  blit(preview, r, 260 + i * (12 * RS + 10), moveY, RS);
}

// ==== ROW 3 — animated OVERLAY frames (both frames of each) ====
const ovY = moveY + 14 * RS + 18;
band(preview, 12, ovY - 10, 300, 2, HDR);
const OS = 3;
const ovNames = ["captcha", "cooldown", "retry", "success", "zzz"];
let ovx = 24;
for (const name of ovNames) {
  for (const f of [0, 1]) {
    blit(preview, cropRegion("overlays", `${name}${f}`), ovx, ovY, OS);
    ovx += 13 * OS + 4;
  }
  ovx += 8; // gap between icons
}

// ==== ROW 4 — ambient WORLD (terminal idle/active/active1 + candle flicker) ====
const worldY = ovY + 13 * OS + 20;
band(preview, 12, worldY - 10, 300, 2, HDR);
const TS = 2;
let wx = 24;
for (const region of ["idle", "active", "active1"]) {
  blit(preview, outline(cropRegion("terminal", region)), wx, worldY, TS);
  wx += 16 * TS + 8;
}
wx += 12;
for (const region of ["candle0", "candle1"]) {
  blit(preview, outline(cropRegion("candle", region)), wx, worldY + 20, RS);
  wx += 8 * RS + 8;
}
// tileset + landmark + NIGHT DECOR cohesion swatch (night ground + graveyard decor)
wx += 12;
for (const [sprite, region] of [
  ["tile_grass", "base"], ["tile_path", "base"], ["grave", "home"],
  ["crypt", "base"], ["mausoleum", "base"], ["tree", "base"],
  ["fence", "seg"], ["cross", "base"], ["obelisk", "base"],
  ["moon", "base"], ["star", "base"],
]) {
  blit(preview, outline(cropRegion(sprite, region)), wx, worldY, TS);
  wx += manifest.sprites[sprite].cellWidth * TS + 6;
}
// overhead flyers (bat flap + will-o'-wisp flicker) + the ground CAT critter
wx += 8;
for (const [sprite, region] of [["bat", "flap0"], ["bat", "flap1"], ["wisp", "glow0"], ["wisp", "glow1"]]) {
  blit(preview, outline(cropRegion(sprite, region)), wx, worldY + 20, RS);
  wx += manifest.sprites[sprite].cellWidth * RS + 6;
}

// ==== ROW 5 — recolor sanity swatch (preserve the proof): the S idle
//      ghost across every section palette x status tints ====
const recY = worldY + 24 * TS + 22;
band(preview, 12, recY - 10, 300, 2, HDR);
const sections = ["base", ...Object.keys(palettes.sections)];
const statuses = ["idle", "working", "success", "error"];
const cellW = 12 * OS + 6;
const cellH = 14 * OS + 6;
for (let sIdx = 0; sIdx < sections.length; sIdx++) {
  const sec = sections[sIdx];
  for (let tIdx = 0; tIdx < statuses.length; tIdx++) {
    let r = cropRegion("ghost", "s_idle0");
    if (sec !== "base") r = recolor(r, palettes.sections[sec]);
    r = applyStatusTint(r, statuses[tIdx], palettes.statusTints);
    r = outline(r);
    blit(preview, r, 24 + sIdx * cellW, recY + tIdx * cellH, OS);
  }
}

// ==== ROW 6 — the ground CAT critter: a 4-frame walk cycle + a sit pose ====
const catY = recY + statuses.length * cellH + 6;
band(preview, 12, catY - 10, 200, 2, HDR);
let catX = 24;
for (const region of ["walk0", "walk1", "walk2", "walk3", "sit"]) {
  blit(preview, outline(cropRegion("cat", region)), catX, catY, RS);
  catX += manifest.sprites.cat.cellWidth * RS + 8;
}

// ==== ROW 7 — the PLACEABLE-PROP CATALOG: every catalogued prop drawn
//      purely from its def + resolved {orientation[,state]} region (no hard-coded prop art) ====
const propY = catY + 8 * RS + 24;
band(preview, 12, propY - 10, 340, 2, HDR);
let px = 20;
let py = propY;
let rowMaxH = 0;
const PPS = 2; // props preview scale
for (const def of Object.values(propCatalog.props)) {
  // draw the prop's default facing; for a stateful prop prefer its "on" state to show the glow.
  const state = def.states ? (def.states.on ? "on" : def.defaultState) : undefined;
  const { region, mirror } = resolvePropRegion(def, def.defaultOrientation, state);
  let r = outline(cropRegion(def.sprite, region));
  if (mirror) r = flipH(r);
  const wpx = r.width * PPS;
  const hpx = r.height * PPS;
  if (px + wpx > preview.width - 12) {
    px = 20;
    py += rowMaxH + 10;
    rowMaxH = 0;
  }
  blit(preview, r, px, py, PPS);
  px += wpx + 8;
  rowMaxH = Math.max(rowMaxH, hpx);
}

// ---- write outputs ----
mkdirSync(outDir, { recursive: true });
writeFileSync(resolve(outDir, "atlas.png"), encodePng(atlas));
writeFileSync(resolve(outDir, "preview.png"), encodePng(preview));
writeFileSync(
  resolve(outDir, "atlas.frames.json"),
  JSON.stringify({ width: atlas.width, height: atlas.height, frames: atlas.frames }, null, 2),
);

console.log(`\nwrote:`);
console.log(`  ${resolve(outDir, "atlas.png")}`);
console.log(`  ${resolve(outDir, "preview.png")}   <- open this for the vibe-check`);
console.log(`  ${resolve(outDir, "atlas.frames.json")}`);
