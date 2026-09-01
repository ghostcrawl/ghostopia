// ghostopia ghost-art — 8-way facing selector ("the 360").
//
// The ONE cute-spooky ghost turns to face its A* heading. Only FIVE facings are
// hand-authored (s, se, e, ne, n); the west set (sw, w, nw) is produced by a
// horizontal MIRROR of the authored east art — never authored separately. This
// pure helper maps a movement vector to a compass facing + a mirror flag so the
// renderer can turn the ghost deterministically.
//
// Screen-space convention: +dx = right (east), +dy = DOWN (south) — the same
// axis a tile-grid / A* path uses. A zero vector means "not moving" and returns
// the idle default (south, no mirror).

/** The eight compass facings. */
export type Facing8 = "s" | "se" | "e" | "ne" | "n" | "nw" | "w" | "sw";

/** Facing + whether the renderer must horizontally flip the authored east art. */
export interface FacingResult {
  facing: Facing8;
  mirror: boolean;
}

// Sector index (atan2 bucket, 45deg each) -> compass facing.
// atan2(dy, dx) with +dy DOWN: 0=east, +pi/2=south, pi=west, -pi/2=north.
const SECTOR: readonly Facing8[] = ["e", "se", "s", "sw", "w", "nw", "n", "ne"];

/** The west set is drawn by mirroring its east-side authored source. */
const MIRRORED: ReadonlySet<Facing8> = new Set<Facing8>(["sw", "w", "nw"]);

/** East-side authored source a mirrored west facing flips from (renderer aid). */
export const MIRROR_SOURCE: Readonly<Record<Facing8, Facing8>> = {
  s: "s",
  se: "se",
  e: "e",
  ne: "ne",
  n: "n",
  sw: "se",
  w: "e",
  nw: "ne",
};

const QUARTER = Math.PI / 4;
// Tiny bias so an exact half-sector boundary rounds up deterministically
// (round-half-up), immune to atan2 float wobble at irrational headings.
const HALF_UP = 1e-9;

/**
 * Map a movement vector to one of 8 compass facings + a mirror flag.
 * - Buckets the heading into 8 sectors of 45deg (round-half-up on the boundary).
 * - `mirror` is true exactly for the west set (sw/w/nw): the renderer flips the
 *   authored east art (`MIRROR_SOURCE[facing]`) horizontally rather than drawing
 *   separate west frames.
 * - A zero vector returns `{ facing: "s", mirror: false }` (idle default).
 */
export function facingFromVector(dx: number, dy: number): FacingResult {
  if (dx === 0 && dy === 0) return { facing: "s", mirror: false };
  const angle = Math.atan2(dy, dx);
  let idx = Math.floor(angle / QUARTER + 0.5 + HALF_UP);
  idx = ((idx % 8) + 8) % 8;
  const facing = SECTOR[idx];
  return { facing, mirror: MIRRORED.has(facing) };
}
