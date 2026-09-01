// ghostopia ghost-art — ORIGINAL directional ghost-sheet author (art:author).
//
// Emits assets/ghost/ghost.grids.json: the ONE cute-spooky 12x14 ghost, now
// turning. FIVE facings are hand-authored here as palette-key silhouettes
// (s, se, e, ne, n); the west set (sw/w/nw) is NEVER authored — the renderer
// mirrors the east art (see facing.ts / animations.json mirror clips). Each
// facing's 4-frame idle + 4-frame move cycle is DERIVED from its rest silhouette
// by deterministic float transforms (bob + wisp sway + a 1-frame blink) — the
// keyframes are hand-authored, the in-between motion is procedural. Nothing is
// traced or reused from any reference sheet.
//
// Layout the manifest expects (cellWidth 12, cellHeight 14):
//   row 0: s   frames  idle0..3 move0..3   (cols 0..7)
//   row 1: se  ""      row 2: e   ""      row 3: ne  ""     row 4: n  ""
//   row 5: expr  work0 work1 work2 success0 success1 error0 error1 (cols 0..6)

import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const W = 12;
const H = 14;
const BLANK = " ".repeat(W);

// --- hand-authored rest silhouettes (palette KEYS: b body, s shade, r rim/shine,
//     e eye, p blush; ' ' = transparent). Facing is read from eye placement +
//     rim/shine side + (for n) a back-of-head crown. ---
const S = [
  "    bbbb    ",
  "   bbbbbb   ",
  "  bbbbbbbb  ",
  " bbbbbbbbbr ",
  " bbbbbbbbbr ",
  " bbeebbeebr ",
  " bbeebbeebr ",
  " bbbbbbbbbr ",
  " bbbbppbbbr ",
  " bbbbbbbbbr ",
  " sbbbbbbbsr ",
  " sbbbbbbbsr ",
  " bb bb bb b ",
  "  s  s  s   ",
];
const SE = [
  "    bbbb    ",
  "   bbbbbbr  ",
  "  bbbbbbbbr ",
  " bbbbbbbbbrr",
  " bbbbbbbbbrr",
  " bbbeebeebrr",
  " bbbeebeebrr",
  " bbbbbbbbbrr",
  " bbbbbppbbrr",
  " bbbbbbbbbrr",
  " sbbbbbbbsrr",
  " sbbbbbbbsr ",
  "  bb bb bb b",
  "   s  s  s  ",
];
const E = [
  "    bbbb    ",
  "   bbbbbbr  ",
  "  bbbbbbbbr ",
  " bbbbbbbbbrr",
  " bbbbbbbbbrr",
  " bbbbbbeebrr",
  " bbbbbbeebrr",
  " bbbbbbbbbrr",
  " bbbbbbppbrr",
  " bbbbbbbbbrr",
  " sbbbbbbbsrr",
  " sbbbbbbbsr ",
  "  bb bb bb b",
  "   s  s  s  ",
];
const NE = [
  "    rrrr    ",
  "   brrrrbr  ",
  "  bbbbbbbbr ",
  " bbbbbbbbbrr",
  " bbbbbbbbbrr",
  " bbbbbbbbbrr",
  " bbbbbbebbrr",
  " bbbbbbbbbrr",
  " bbbbbbbbbrr",
  " bbbbbbbbbrr",
  " sbbbbbbbsrr",
  " sbbbbbbbsr ",
  "  bb bb bb b",
  "   s  s  s  ",
];
const N = [
  "    rrrr    ",
  "   rrrrrr   ",
  "  brrrrrrb  ",
  " bbbbbbbbbr ",
  " bbbbbbbbbr ",
  " bbbbbbbbbr ",
  " bbbbbbbbbr ",
  " bbbbbbbbbr ",
  " bbbbbbbbbr ",
  " bbbbbbbbbr ",
  " sbbbbbbbsr ",
  " sbbbbbbbsr ",
  " bb bb bb b ",
  "  s  s  s   ",
];

// --- front expression poses (based on S) ---
const WORK = [
  "    bbbb    ",
  "   bbbbbb   ",
  "  bbbbbbbb  ",
  " bbbbbbbbbr ",
  " bbbbbbbbbr ",
  " bbbbbbbbbr ",
  " bbeebbeebr ",
  " bbeebbeebr ",
  " bbbbppbbbr ",
  " bbbbbbbbbr ",
  " sbbbbbbbsr ",
  " sbbbbbbbsr ",
  " bb bb bb b ",
  "  s  s  s   ",
];
const HAPPY = [
  "    bbbb    ",
  "   bbbbbb   ",
  "  bbbbbbbb  ",
  " bbbbbbbbbr ",
  " bbbbbbbbbr ",
  " bebbbbebbr ",
  " bbebbbebbr ",
  " bbbbbbbbbr ",
  " bbppppppbr ",
  " bbbbbbbbbr ",
  " sbbbbbbbsr ",
  " sbbbbbbbsr ",
  " bb bb bb b ",
  "  s  s  s   ",
];
const SAD = [
  "    bbbb    ",
  "   bbbbbb   ",
  "  bbbbbbbb  ",
  " bbbbbbbbbr ",
  " bbbbbbbbbr ",
  " bbeebbeebr ",
  " bbeebbeebr ",
  " bbbbbbbbbr ",
  " bbbeeebbbr ",
  " bbebbbbebr ",
  " sbbbbbbbsr ",
  " sbbbbbbbsr ",
  " bb bb bb b ",
  "  s  s  s   ",
];

// --- deterministic float transforms (keyframe -> in-between motion) ---
const pad = (row) => (row.length >= W ? row.slice(0, W) : row + " ".repeat(W - row.length));
const asGrid = (rows) => rows.map(pad);

/** shift a single row horizontally (dx>0 = right); vacated cells transparent. */
function shiftRow(row, dx) {
  if (dx === 0) return row;
  if (dx > 0) return (" ".repeat(dx) + row).slice(0, W);
  return (row.slice(-dx) + " ".repeat(-dx)).slice(0, W);
}

/** float bob: push the whole body DOWN by `dy` rows within the 14-row cell. */
function bob(rows, dy) {
  if (dy <= 0) return rows.slice();
  return [...Array(dy).fill(BLANK), ...rows.slice(0, H - dy)];
}

/** wisp sway: slide only the two tail rows (12,13) sideways. */
function sway(rows, dx) {
  const out = rows.slice();
  out[12] = shiftRow(out[12], dx);
  out[13] = shiftRow(out[13], dx);
  return out;
}

/** blink: close the eyes for one frame (top eye row -> body). */
function blink(rows) {
  const out = rows.slice();
  for (let y = 0; y < out.length; y++) {
    if (out[y].includes("e")) {
      out[y] = out[y].replace(/e/g, "b");
      break;
    }
  }
  return out;
}

/** 4-frame idle: gentle bob 0,1,1,0 + wisp sway + a blink on frame 2. */
function idleCycle(base) {
  const g = asGrid(base);
  return [
    g, // rest
    sway(bob(g, 1), 1), // down, wisp right
    sway(blink(bob(g, 1)), -1), // down + blink, wisp left
    sway(g, 1), // rest, wisp settle
  ];
}

/** 4-frame move: stronger bob 0,1,0,1 + wisp leaning toward travel. */
function moveCycle(base) {
  const g = asGrid(base);
  return [
    sway(g, 2), // lean
    sway(bob(g, 1), 1), // step down
    sway(g, 2), // lean
    sway(bob(g, 1), 3), // step down, strong lean
  ];
}

/** lay frames side-by-side into `H` rows of (frames*W) width. */
function strip(frames) {
  const rows = [];
  for (let y = 0; y < H; y++) rows.push(frames.map((f) => f[y]).join(""));
  return rows;
}

const facings = [S, SE, E, NE, N];
const rows = [];
for (const base of facings) {
  const block = strip([...idleCycle(base), ...moveCycle(base)]); // 8 frames -> 96 wide
  rows.push(...block);
}
// expr row (7 frames) padded to the 96-wide block
const exprFrames = [
  asGrid(WORK),
  sway(asGrid(WORK), 1),
  bob(asGrid(WORK), 1),
  asGrid(HAPPY),
  bob(asGrid(HAPPY), 1),
  asGrid(SAD),
  sway(asGrid(SAD), -1),
];
for (const r of strip(exprFrames)) rows.push((r + " ".repeat(96)).slice(0, 96));

const doc = {
  _note:
    "ghostopia ORIGINAL directional ghost — the ONE cute-spooky 12x14 ghost that TURNS. " +
    "Sheet rows = facings (s,se,e,ne,n), each row = idle0..3 then move0..3; the last row = " +
    "front expression frames (work0..2, success0..1, error0..1). Palette KEYS b=body s=shade " +
    "r=rim/shine e=eye p=blush; ' ' = transparent. Facing read from eye placement + shine side " +
    "+ (n) a back-of-head crown. The west set (sw/w/nw) is NOT authored — the renderer mirrors " +
    "the east art (facing.ts + animations.json mirror clips). idle/move in-betweens are derived " +
    "from hand-authored rest keyframes by deterministic bob+sway+blink (see scripts/author_ghost.mjs). " +
    "No reference-repo sprite was traced or reused.",
  grids: {
    ghost: {
      transparent: " ",
      legend: {
        b: "#e8ecff",
        s: "#b0b8e0",
        r: "#8ff0ff",
        e: "#101018",
        p: "#ff9ec4",
      },
      rows,
    },
  },
};

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, "../../../assets/ghost/ghost.grids.json");
writeFileSync(out, JSON.stringify(doc, null, 2) + "\n");
console.log(`wrote ${out} (${rows.length} rows x ${Math.max(...rows.map((r) => r.length))} cols)`);
