// ghostopia ghost-renderer — pure editor-overlay geometry tests .

import { describe, expect, it } from "vitest";

import { tileFromWorld } from "./editorOverlay.js";

describe("tileFromWorld", () => {
  it("floor-divides a world-pixel point into its tile", () => {
    expect(tileFromWorld(0, 0, 16)).toEqual({ x: 0, y: 0 });
    expect(tileFromWorld(15.9, 0.1, 16)).toEqual({ x: 0, y: 0 });
    expect(tileFromWorld(16, 33, 16)).toEqual({ x: 1, y: 2 });
    expect(tileFromWorld(400, 288, 16)).toEqual({ x: 25, y: 18 });
  });

  it("handles negative (off-map) coords by flooring toward -inf", () => {
    expect(tileFromWorld(-1, -1, 16)).toEqual({ x: -1, y: -1 });
    expect(tileFromWorld(-16, -17, 16)).toEqual({ x: -1, y: -2 });
  });
});
