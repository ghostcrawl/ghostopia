// ghostopia ghost-renderer — link-line dash math tests .

import { describe, expect, it } from "vitest";

import { dashOffset, dashSegments } from "./linklines.js";

describe("dashOffset", () => {
  it("wraps into [0, period)", () => {
    expect(dashOffset(0, 0.03, 8)).toBe(0);
    expect(dashOffset(100, 0.03, 8)).toBeGreaterThanOrEqual(0);
    expect(dashOffset(100, 0.03, 8)).toBeLessThan(8);
    // 1000ms * 0.03 = 30 → 30 % 8 = 6
    expect(dashOffset(1000, 0.03, 8)).toBeCloseTo(6, 5);
  });

  it("is 0 for a static line (speed 0) and guards a non-positive period", () => {
    expect(dashOffset(5000, 0, 8)).toBe(0);
    expect(dashOffset(5000, 0.03, 0)).toBe(0);
  });
});

describe("dashSegments", () => {
  it("splits a horizontal line into on-dash segments along the pattern", () => {
    // a 20px line, dash 4/gap 4 (period 8), no offset → dashes at [0,4],[8,12],[16,20]
    const segs = dashSegments(0, 0, 20, 0, 4, 4, 0);
    expect(segs).toHaveLength(3);
    expect(segs[0]).toMatchObject({ x1: 0, y1: 0, x2: 4, y2: 0 });
    expect(segs[1].x1).toBeCloseTo(8, 5);
    expect(segs[2].x2).toBeCloseTo(20, 5); // clipped to the line length
  });

  it("marches the pattern as the offset grows (first dash clipped in)", () => {
    const a = dashSegments(0, 0, 20, 0, 4, 4, 0);
    const b = dashSegments(0, 0, 20, 0, 4, 4, 2);
    expect(b).not.toEqual(a); // the pattern shifted
    // with offset 2 the first visible dash is the tail [0,2]
    expect(b[0].x2).toBeCloseTo(2, 5);
  });

  it("returns nothing for a zero-length line or a degenerate dash", () => {
    expect(dashSegments(5, 5, 5, 5, 4, 4, 0)).toEqual([]);
    expect(dashSegments(0, 0, 10, 0, 0, 4, 0)).toEqual([]);
  });

  it("handles a diagonal line (unit direction preserved)", () => {
    const segs = dashSegments(0, 0, 6, 8, 5, 5, 0); // length 10
    expect(segs.length).toBeGreaterThan(0);
    // first dash [0,5] along the (0.6,0.8) unit vector
    expect(segs[0].x2).toBeCloseTo(3, 5);
    expect(segs[0].y2).toBeCloseTo(4, 5);
  });
});
