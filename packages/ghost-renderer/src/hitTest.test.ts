// ghostopia ghost-renderer — hit-test math (canvas ghost click-to-select, Task 3).

import { describe, expect, it } from "vitest";

import {
  CLICK_DRAG_THRESHOLD_PX,
  isClick,
  pointInBox,
  topmostHit,
  type HitEntity,
} from "./hitTest.js";

describe("pointInBox", () => {
  const box = { x: 10, y: 20, w: 30, h: 40 }; // covers [10,40] x [20,60]
  it("is true for a point inside (incl. edges)", () => {
    expect(pointInBox(25, 40, box)).toBe(true);
    expect(pointInBox(10, 20, box)).toBe(true); // top-left corner
    expect(pointInBox(40, 60, box)).toBe(true); // bottom-right corner
  });
  it("is false for a point outside", () => {
    expect(pointInBox(9, 40, box)).toBe(false);
    expect(pointInBox(25, 61, box)).toBe(false);
    expect(pointInBox(41, 40, box)).toBe(false);
  });
});

describe("isClick", () => {
  it("counts a tiny move as a click, a big move as a drag", () => {
    expect(isClick(0, 0)).toBe(true);
    expect(isClick(3, 3)).toBe(true); // ~4.24 px <= 5
    expect(isClick(10, 0)).toBe(false);
    expect(isClick(4, 4)).toBe(false); // ~5.66 px > 5
  });
  it("honors a custom threshold", () => {
    expect(isClick(8, 0, 10)).toBe(true);
    expect(isClick(12, 0, 10)).toBe(false);
  });
  it("exposes a sane default threshold", () => {
    expect(CLICK_DRAG_THRESHOLD_PX).toBeGreaterThan(0);
  });
});

describe("topmostHit", () => {
  const a: HitEntity = { id: "a", box: { x: 0, y: 0, w: 20, h: 20 }, depth: 5 };
  const b: HitEntity = { id: "b", box: { x: 10, y: 10, w: 20, h: 20 }, depth: 9 }; // overlaps a, nearer
  const c: HitEntity = { id: "c", box: { x: 100, y: 100, w: 10, h: 10 }, depth: 1 };

  it("returns the id of a sole hit", () => {
    expect(topmostHit(2, 2, [a, b, c])).toBe("a");
    expect(topmostHit(105, 105, [a, b, c])).toBe("c");
  });
  it("returns the top-most (greatest depth) entity on overlap", () => {
    // (15,15) is inside BOTH a and b; b is nearer (depth 9 > 5) so it wins.
    expect(topmostHit(15, 15, [a, b, c])).toBe("b");
    // order-independent.
    expect(topmostHit(15, 15, [b, a, c])).toBe("b");
  });
  it("returns null for a miss (deselect / no-op per BRIEF)", () => {
    expect(topmostHit(500, 500, [a, b, c])).toBeNull();
    expect(topmostHit(0, 0, [])).toBeNull();
  });
});
