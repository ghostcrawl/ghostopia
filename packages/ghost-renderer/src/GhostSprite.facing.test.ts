// ghostopia ghost-renderer — stationary-facing + true-rest Zzz tests (Task 2).
//
// A stationary working ghost must FACE its workstation from the explicit server signal
// , not the collapsed dx=dy=0 movement delta (which always reads south). The Zzz
// overlay is reserved for a TRUE grave-rest state, never a between-walk pause.

import { describe, expect, it } from "vitest";
import { facingFromVector } from "@ghostopia/ghost-art";

import { clipNameForGhost, resolveFacing, restingZzzVisible } from "./GhostSprite.js";

describe("resolveFacing — honor explicit facing while stationary ()", () => {
  const stuck = facingFromVector(0, 0); // the collapsed idle delta → { facing: "s", mirror: false }

  it("stuck delta collapses to south (the bug we fix)", () => {
    expect(stuck).toEqual({ facing: "s", mirror: false });
  });

  it("uses the explicit facing when NOT moving instead of the stuck south delta", () => {
    expect(resolveFacing(false, "e", stuck).facing).toBe("e");
    expect(resolveFacing(false, "n", stuck).facing).toBe("n");
    expect(resolveFacing(false, "ne", stuck).facing).toBe("ne");
  });

  it("recomputes the mirror flag for the west set from the explicit facing", () => {
    expect(resolveFacing(false, "w", stuck).mirror).toBe(true);
    expect(resolveFacing(false, "sw", stuck).mirror).toBe(true);
    expect(resolveFacing(false, "nw", stuck).mirror).toBe(true);
    expect(resolveFacing(false, "e", stuck).mirror).toBe(false);
    expect(resolveFacing(false, "se", stuck).mirror).toBe(false);
  });

  it("IGNORES the explicit facing while MOVING (the walk cycle turns along the heading)", () => {
    const movingEast = facingFromVector(5, 0);
    expect(resolveFacing(true, "n", movingEast)).toEqual(movingEast);
    expect(resolveFacing(true, "n", movingEast).facing).toBe("e");
  });

  it("falls back to the movement facing when no explicit facing is present", () => {
    expect(resolveFacing(false, null, stuck)).toEqual(stuck);
    expect(resolveFacing(false, undefined, stuck)).toEqual(stuck);
  });

  it("a working (AT_WORKSTATION) ghost honors its workstation facing for the mirror", () => {
    // clip stays the front "work" pose, but the resolved facing drives the correct flip
    expect(clipNameForGhost("AT_WORKSTATION", "w", false)).toBe("work");
    expect(resolveFacing(false, "w", stuck).mirror).toBe(true);
    expect(resolveFacing(false, "e", stuck).mirror).toBe(false);
  });
});

describe("restingZzzVisible — Zzz reserved for TRUE grave rest ", () => {
  it("shows Zzz only on a true resting IDLE state", () => {
    expect(restingZzzVisible("IDLE")).toBe(true);
  });

  it("does NOT show Zzz during a between-walk pause (still WALKING / RETURNING_HOME)", () => {
    expect(restingZzzVisible("WALKING")).toBe(false);
    expect(restingZzzVisible("RETURNING_HOME")).toBe(false);
  });

  it("does NOT show Zzz on a working ghost", () => {
    expect(restingZzzVisible("AT_WORKSTATION")).toBe(false);
    expect(restingZzzVisible("EXTRACTING")).toBe(false);
    expect(restingZzzVisible("NAVIGATING")).toBe(false);
  });
});
