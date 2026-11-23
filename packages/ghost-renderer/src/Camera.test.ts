// ghostopia ghost-renderer — pure camera follow/lead math .
//
// Covers the follow look-ahead lead + the follow-lerp step used by the auto-follow camera.
// The "yield to manual input" (cancelling follow on a pan) lives in the render loop's input
// handlers (canvas-bound); here we prove the PURE lerp/lead the loop composes.

import { describe, expect, it } from "vitest";

import { followStep, leadPoint } from "./Camera.js";

describe("leadPoint", () => {
  it("pushes the target forward along its velocity by lookaheadMs", () => {
    // moving +2 px/ms in x, lookahead 100ms → +200px lead.
    expect(leadPoint(100, 50, 2, 0, 100)).toEqual({ x: 300, y: 50 });
  });
  it("returns the target unchanged for a stationary ghost", () => {
    expect(leadPoint(100, 50, 0, 0, 260)).toEqual({ x: 100, y: 50 });
  });
  it("tolerates non-finite inputs", () => {
    expect(leadPoint(10, 10, Number.NaN, 1, Number.POSITIVE_INFINITY)).toEqual({ x: 10, y: 10 });
  });
});

describe("followStep (follow-lerp)", () => {
  it("moves a fraction toward the target and snaps at alpha=1", () => {
    const cam = { x: 0, y: 0, zoom: 2 };
    const half = followStep(cam, 100, 0, 0.5);
    expect(half.x).toBe(50);
    const snap = followStep(cam, 100, 0, 1);
    expect(snap.x).toBe(100);
  });
  it("is a no-op at alpha=0 (a yielded follow does not drift the camera)", () => {
    const cam = { x: 10, y: 20, zoom: 3 };
    expect(followStep(cam, 999, 999, 0)).toEqual({ x: 10, y: 20, zoom: 3 });
  });
  it("keeps the zoom unchanged", () => {
    expect(followStep({ x: 0, y: 0, zoom: 4 }, 100, 100, 0.3).zoom).toBe(4);
  });
});
