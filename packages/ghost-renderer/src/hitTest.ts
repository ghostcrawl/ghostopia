// ghostopia ghost-renderer — pure pointer hit-testing math (canvas ghost click-to-select).
//
// The render loop hit-tests a click against the ghost sprites so clicking a ghost on the
// PixiJS canvas selects it — the SAME `ghost.select` the roster row fires. These helpers are PURE (no PixiJS) so the math is unit
// tested directly: point-in-box, top-most-on-overlap, and the drag-vs-click threshold.

/** An axis-aligned world-space box (a ghost sprite's footprint). */
export interface HitBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A hit-testable entity: its id, its world box, and a depth (larger = nearer the camera). */
export interface HitEntity {
  id: string;
  box: HitBox;
  /** ground Y — larger is drawn on top (matches the render loop's depth sort). */
  depth: number;
}

/** The pointer travel (screen px) at/under which a pointerup counts as a CLICK, not a pan. */
export const CLICK_DRAG_THRESHOLD_PX = 5;

/** True when the world point (px,py) lies inside `box`. */
export function pointInBox(px: number, py: number, box: HitBox): boolean {
  return px >= box.x && px <= box.x + box.w && py >= box.y && py <= box.y + box.h;
}

/** True when a pointer moved little enough between down and up to be a click (not a drag/pan). */
export function isClick(dxPx: number, dyPx: number, threshold: number = CLICK_DRAG_THRESHOLD_PX): boolean {
  return Math.hypot(dxPx, dyPx) <= threshold;
}

/**
 * The id of the TOP-MOST entity whose box contains (px,py), or `null` for a miss. On overlap
 * the entity with the greatest `depth` (nearest the camera / drawn last) wins — matching what
 * the viewer sees on top.
 */
export function topmostHit(px: number, py: number, entities: readonly HitEntity[]): string | null {
  let best: HitEntity | null = null;
  for (const e of entities) {
    if (!pointInBox(px, py, e.box)) continue;
    if (best === null || e.depth > best.depth) best = e;
  }
  return best ? best.id : null;
}
