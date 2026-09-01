// ghostopia ghost-renderer — mission/section link lines.
//
// An animated "marching-ants" spectral dashed line connecting a mission / section anchor to
// each of its fan-out ghosts, so the orchestration hierarchy is VISIBLE. Drawn in an overhead
// layer above the world; `prefers-reduced-motion` freezes the march to a static dashed line.
//
// The pure dash math (a wrapping offset + the drawable dash segments between two points) lives
// here and is unit-tested; the PixiJS draw fn is a thin loop over it. Imports only PixiJS.

import type { Graphics } from "pixi.js";

/** A single drawable dash segment between two world points. */
export interface DashSegment {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

/**
 * The marching-ants scroll OFFSET at a wall-clock time — a value in `[0, period)` that advances
 * the dash pattern along the line. `speedPxPerMs` is the march speed; `period = dash + gap`.
 * Pure + wrapping (unit-tested). A static line passes `speedPxPerMs = 0`.
 */
export function dashOffset(timeMs: number, speedPxPerMs: number, period: number): number {
  if (period <= 0) return 0;
  const raw = timeMs * speedPxPerMs;
  const m = raw % period;
  return m < 0 ? m + period : m;
}

/**
 * The drawable dash segments between `(ax,ay)` and `(bx,by)` for a `dash`/`gap` pattern scrolled
 * by `offset`. Marching ants: the first dash is clipped by `offset` so the pattern appears to
 * move along the line as `offset` grows. Pure — returns the clipped on-segments only.
 */
export function dashSegments(
  ax: number,
  ay: number,
  bx: number,
  by: number,
  dash: number,
  gap: number,
  offset: number,
): DashSegment[] {
  const segs: DashSegment[] = [];
  const dx = bx - ax;
  const dy = by - ay;
  const len = Math.hypot(dx, dy);
  if (len < 1e-6 || dash <= 0 || gap < 0) return segs;
  const ux = dx / len;
  const uy = dy / len;
  const period = dash + gap;
  // start the pattern "behind" the line by the offset so the first dash marches in.
  let d = -(((offset % period) + period) % period);
  while (d < len) {
    const start = Math.max(0, d);
    const end = Math.min(len, d + dash);
    if (end > start) {
      segs.push({
        x1: ax + ux * start,
        y1: ay + uy * start,
        x2: ax + ux * end,
        y2: ay + uy * end,
      });
    }
    d += period;
  }
  return segs;
}

/**
 * Draw a marching-ants link line into `g` (does NOT clear — the caller batches many lines into
 * one Graphics + clears once). `offset` scrolls the dashes; a static line passes `offset` fixed.
 */
export function drawLinkLine(
  g: Graphics,
  ax: number,
  ay: number,
  bx: number,
  by: number,
  offset: number,
  options: { dash?: number; gap?: number; color?: number; width?: number; alpha?: number } = {},
): void {
  const dash = options.dash ?? 4;
  const gap = options.gap ?? 4;
  const color = options.color ?? 0x8ff0ff;
  const width = options.width ?? 1;
  const alpha = options.alpha ?? 0.5;
  for (const s of dashSegments(ax, ay, bx, by, dash, gap, offset)) {
    g.moveTo(s.x1, s.y1).lineTo(s.x2, s.y2).stroke({ color, width, alpha });
  }
}
