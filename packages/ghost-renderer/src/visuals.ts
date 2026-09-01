// ghostopia ghost-renderer — pure visual helpers (no PixiJS / DOM).
//
// Small, deterministic, testable helpers the render loop uses for tasteful
// polish: a stable per-cell hash (so ground variety / decor scatter is identical
// every load, never random flicker) and the section-tint map type.

/** section-name AND region-id -> base tint colour (0xRRGGBB). */
export type SectionTintMap = Record<string, number>;

/**
 * A deterministic hash of an integer cell (x, y) + a `salt`, in [0, 1). Stable
 * across loads — the ground noise, path spine, and decor scatter must NOT change
 * frame-to-frame or session-to-session.
 */
export function hash2(x: number, y: number, salt = 0): number {
  let h = (Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263) + Math.imul(salt | 0, 2246822519)) >>> 0;
  h = (h ^ (h >>> 13)) >>> 0;
  h = Math.imul(h, 1274126177) >>> 0;
  h = (h ^ (h >>> 16)) >>> 0;
  return h / 4294967296;
}
