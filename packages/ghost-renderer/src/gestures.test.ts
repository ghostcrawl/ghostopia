// ghostopia ghost-renderer — gesture + pointer-anchored-zoom + follow math tests.

import { describe, expect, it } from "vitest";

import { pointerDistance, pointerMidpoint, pinchScale } from "./gestures.js";
import { zoomAtPoint, followStep, MIN_ZOOM, MAX_ZOOM, type Camera } from "./Camera.js";

describe("gestures — pinch geometry", () => {
  it("distance is Euclidean and symmetric", () => {
    expect(pointerDistance({ x: 0, y: 0 }, { x: 3, y: 4 })).toBe(5);
    expect(pointerDistance({ x: 3, y: 4 }, { x: 0, y: 0 })).toBe(5);
  });

  it("midpoint averages both pointers", () => {
    expect(pointerMidpoint({ x: 0, y: 0 }, { x: 10, y: 4 })).toEqual({ x: 5, y: 2 });
  });

  it("pinch scale is the ratio of spans", () => {
    expect(pinchScale(100, 200)).toBe(2); // fingers spread apart -> zoom in 2x
    expect(pinchScale(200, 100)).toBe(0.5); // pinch together -> zoom out
  });

  it("a degenerate previous span never divides by zero", () => {
    expect(pinchScale(0, 100)).toBe(1);
    expect(pinchScale(Number.NaN, 100)).toBe(1);
    expect(pinchScale(100, 0)).toBe(1);
  });
});

describe("zoomAtPoint — pointer-anchored zoom", () => {
  const cam: Camera = { x: 100, y: 100, zoom: 2 };

  it("keeps the anchored world point fixed under the cursor", () => {
    // The world point under the cursor must map to the same screen offset after zoom.
    const P = { x: 160, y: 100 };
    const factor = 1.5;
    const next = zoomAtPoint(cam, factor, P.x, P.y);
    // screen offset of P from centre before vs after (offset = (P - C) * zoom).
    const before = (P.x - cam.x) * cam.zoom;
    const after = (P.x - next.x) * next.zoom;
    expect(after).toBeCloseTo(before, 6);
    expect(next.zoom).toBeCloseTo(3, 6);
  });

  it("zooming at the centre leaves the centre unmoved", () => {
    const next = zoomAtPoint(cam, 1.5, cam.x, cam.y);
    expect(next.x).toBeCloseTo(cam.x, 6);
    expect(next.y).toBeCloseTo(cam.y, 6);
  });

  it("clamps zoom and does not pan past a zoom limit", () => {
    const atMax: Camera = { x: 100, y: 100, zoom: MAX_ZOOM };
    const next = zoomAtPoint(atMax, 4, 200, 200);
    expect(next.zoom).toBe(MAX_ZOOM);
    expect(next.x).toBeCloseTo(100, 6); // k = z/z' = 1 → no pan
    expect(next.y).toBeCloseTo(100, 6);
    const atMin: Camera = { x: 100, y: 100, zoom: MIN_ZOOM };
    expect(zoomAtPoint(atMin, 0.1, 200, 200).zoom).toBe(MIN_ZOOM);
  });
});

describe("followStep — smooth follow-lerp", () => {
  const cam: Camera = { x: 0, y: 0, zoom: 2 };

  it("moves a fraction toward the target, keeping zoom", () => {
    const next = followStep(cam, 100, 50, 0.5);
    expect(next.x).toBe(50);
    expect(next.y).toBe(25);
    expect(next.zoom).toBe(2);
  });

  it("alpha 0 is a no-op; alpha 1 snaps; out-of-range clamps", () => {
    expect(followStep(cam, 100, 100, 0)).toMatchObject({ x: 0, y: 0 });
    expect(followStep(cam, 100, 100, 1)).toMatchObject({ x: 100, y: 100 });
    expect(followStep(cam, 100, 100, 5)).toMatchObject({ x: 100, y: 100 });
    expect(followStep(cam, 100, 100, Number.NaN)).toMatchObject({ x: 0, y: 0 });
  });

  it("respects camera bounds", () => {
    const bounded = followStep(cam, 1000, 1000, 1, { minX: 0, maxX: 10, minY: 0, maxY: 10 });
    expect(bounded.x).toBe(10);
    expect(bounded.y).toBe(10);
  });
});
