// ghostopia ghost-renderer — pure multi-touch gesture math (no PixiJS / DOM).
//
// The render loop drives one-finger pan + two-finger pinch-zoom from PointerEvents.
// The geometry (pointer distance, midpoint, pinch scale) is factored out here so it
// is unit-tested without a canvas — the same discipline as camera.ts / hitTest.ts.

/** A screen-space pointer position (client px). */
export interface PointerPos {
  x: number;
  y: number;
}

/** Euclidean distance between two pointers (the pinch span). */
export function pointerDistance(a: PointerPos, b: PointerPos): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/** Midpoint of two pointers (the pinch anchor / two-finger pan reference). */
export function pointerMidpoint(a: PointerPos, b: PointerPos): PointerPos {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

/**
 * The zoom factor for a pinch step: `curDist / prevDist`. A non-finite or
 * non-positive previous span yields `1` (no zoom) so a fresh/degenerate pinch
 * never divides by zero or snaps the camera.
 */
export function pinchScale(prevDist: number, curDist: number): number {
  if (!Number.isFinite(prevDist) || prevDist <= 0) return 1;
  if (!Number.isFinite(curDist) || curDist <= 0) return 1;
  return curDist / prevDist;
}
