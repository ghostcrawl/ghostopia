// ghostopia ghost-art — ORIGINAL living status overlays (art:author).
//
// Emits assets/overlays/overlays.grids.json: a 13x13 status-icon library, now
// a TWO-frame loop per icon (captcha pulse, cooldown sand-tick, retry
// quarter-turn, success pop, zzz drift). Icons are hand-authored here; the 2nd
// frame is a deterministic transform of the 1st so each reads as alive. Packed
// left-to-right: captcha0 captcha1 cooldown0 cooldown1 retry0 retry1 success0
// success1 zzz0 zzz1 (cols 0..9). No reference art traced or reused.

import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const C = 13; // cell width
const H = 13;
const pad = (row) => (row.length >= C ? row.slice(0, C) : row + " ".repeat(C - row.length));
const asIcon = (rows) => rows.map(pad);

function shiftRow(row, dx) {
  if (dx === 0) return row;
  if (dx > 0) return (" ".repeat(dx) + row).slice(0, C);
  return (row.slice(-dx) + " ".repeat(-dx)).slice(0, C);
}
const shift = (rows, dx) => rows.map((r) => shiftRow(r, dx));
const bob = (rows, dy) =>
  dy <= 0 ? rows.slice() : [...Array(dy).fill(" ".repeat(C)), ...rows.slice(0, H - dy)];
const flipV = (rows) => rows.slice().reverse();

// captcha: warning triangle with a "!" (pulse = a 1px rise + a brighter core)
const captcha = asIcon([
  "      y      ",
  "      y      ",
  "     yyy     ",
  "     yky     ",
  "    yykyy    ",
  "    yykyy    ",
  "   yyykyyy   ",
  "   yyyyyyy   ",
  "  yyyykyyyy  ",
  "  yyyyyyyyy  ",
  " yyyyyyyyyyy ",
  "yyyyyyyyyyyyy",
  "             ",
]);
// cooldown: hourglass; tick = sand falls (flip the frame)
const cooldown = asIcon([
  " kkkkkkkkkkk ",
  " kwwwwwwwwwk ",
  "  kwwwwwwwk  ",
  "   kwwwwwk   ",
  "    kwwwk    ",
  "     kwk     ",
  "      k      ",
  "     k k     ",
  "    k w k    ",
  "   k www k   ",
  "  k wwwww k  ",
  " kwwwwwwwwwk ",
  " kkkkkkkkkkk ",
]);
// retry: circular arrows; quarter-turn = shift the head
const retry = asIcon([
  "   cccccc    ",
  "  cc    cc   ",
  " cc      cc  ",
  " c        c  ",
  "cc        c c",
  "c   cccc  ccc",
  "c  c    c  c ",
  "c        c   ",
  " c        c  ",
  " cc      cc  ",
  "  cc    cc   ",
  "   cccccc    ",
  "             ",
]);
// success: check mark; pop = a 1px hop + a sparkle
const success = asIcon([
  "             ",
  "          g  ",
  "         gg  ",
  "        gg   ",
  "       gg    ",
  "  g   gg     ",
  "  gg gg      ",
  "   ggg       ",
  "    g        ",
  "             ",
  "             ",
  "             ",
  "             ",
]);
// zzz: sleep z's; drift = slide up-right
const zzz = asIcon([
  "  vvvvv      ",
  "     v       ",
  "    v        ",
  "   v         ",
  "  vvvvv      ",
  "        vvv  ",
  "         v   ",
  "        v    ",
  "        vvv  ",
  "             ",
  "           vv",
  "           v ",
  "          vv ",
]);

const frames = [
  captcha,
  bob(captcha, 1), // pulse
  cooldown,
  flipV(cooldown), // sand tick
  retry,
  shift(retry, 1), // quarter-turn nudge
  success,
  bob(success, 1), // pop
  zzz,
  shift(bob(zzz, 1), 1), // drift up-right
];

const rows = [];
for (let y = 0; y < H; y++) rows.push(frames.map((f) => f[y]).join(""));

const doc = {
  _note:
    "ghostopia ORIGINAL living status overlays — a 13x13 icon library, TWO frames per icon " +
    "(captcha pulse, cooldown sand-tick, retry quarter-turn, success pop, zzz drift). Packed " +
    "L->R: captcha0/1 cooldown0/1 retry0/1 success0/1 zzz0/1. Palette KEYS y=warn k=dark w=white " +
    "c=cyan g=green v=lavender; ' ' = transparent. Frame 1 is a deterministic transform of the " +
    "hand-authored frame 0 (see scripts/author_overlays.mjs). Hand-authored, original.",
  grids: {
    overlays: {
      transparent: " ",
      legend: {
        y: "#ffd23f",
        k: "#241f38",
        w: "#e8ecff",
        c: "#8ff0ff",
        g: "#6ee87a",
        v: "#b6a7ff",
      },
      rows,
    },
  },
};

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, "../../../assets/overlays/overlays.grids.json");
writeFileSync(out, JSON.stringify(doc, null, 2) + "\n");
console.log(`wrote ${out} (${rows.length} rows x ${rows[0].length} cols)`);
