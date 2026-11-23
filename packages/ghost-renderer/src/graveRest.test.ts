// ghostopia ghost-renderer — grave-rest presence tests (196).
//
// Pure-logic (no PixiJS): entering IDLE de-materializes (alpha ramps DOWN to ~0 +
// bob suppressed + a dissolve flourish); leaving IDLE materializes (alpha ramps UP
// to 1 + a materialize flourish); reduced motion snaps to steady endpoints, no motes.

import { describe, expect, it } from "vitest";

import {
  REST_FADE_MS,
  REST_REDUCED_ALPHA,
  REST_SUNK_ALPHA,
  initialPresence,
  isGraveRestState,
  stepPresence,
  type PresenceState,
} from "./graveRest.js";

describe("isGraveRestState", () => {
  it("is true ONLY for IDLE (a between-walk pause / working phase never rests)", () => {
    expect(isGraveRestState("IDLE")).toBe(true);
    expect(isGraveRestState("WALKING")).toBe(false);
    expect(isGraveRestState("RETURNING_HOME")).toBe(false);
    expect(isGraveRestState("SEARCHING")).toBe(false);
    expect(isGraveRestState("COMPLETED")).toBe(false);
  });
});

describe("initialPresence", () => {
  it("starts a first-seen resting ghost SUNK in the stone (no arrival)", () => {
    expect(initialPresence("IDLE", false)).toEqual({ phase: "sunk", alpha: REST_SUNK_ALPHA });
  });
  it("starts a first-seen active ghost rising from 0 (spawn fade-in)", () => {
    expect(initialPresence("WALKING", false)).toEqual({ phase: "rising", alpha: 0 });
  });
  it("under reduced motion: resting=steady faded, active=fully visible (no fade)", () => {
    expect(initialPresence("IDLE", true)).toEqual({ phase: "sunk", alpha: REST_REDUCED_ALPHA });
    expect(initialPresence("SEARCHING", true)).toEqual({ phase: "visible", alpha: 1 });
  });
});

describe("stepPresence — entering IDLE (de-materialize)", () => {
  it("emits a dissolve flourish, suppresses the bob, and ramps alpha DOWN toward 0", () => {
    const start: PresenceState = { phase: "visible", alpha: 1 };
    const first = stepPresence(start, true, 60, false);
    expect(first.flourish).toBe("dissolve");
    expect(first.suppressBob).toBe(true);
    expect(first.state.phase).toBe("sinking");
    expect(first.state.alpha).toBeLessThan(1);

    // keep stepping to full rest — alpha reaches the sunk floor and phase settles to sunk.
    let s = first.state;
    for (let i = 0; i < Math.ceil(REST_FADE_MS / 60) + 2; i++) {
      const step = stepPresence(s, true, 60, false);
      s = step.state;
      expect(step.flourish).toBeNull(); // the flourish fires ONCE, on the transition frame only
      expect(step.suppressBob).toBe(true);
    }
    expect(s.phase).toBe("sunk");
    expect(s.alpha).toBe(REST_SUNK_ALPHA);
  });

  it("monotonically decreases alpha while sinking", () => {
    let s: PresenceState = { phase: "visible", alpha: 1 };
    let prevAlpha = 1;
    for (let i = 0; i < 5; i++) {
      s = stepPresence(s, true, 40, false).state;
      expect(s.alpha).toBeLessThanOrEqual(prevAlpha);
      prevAlpha = s.alpha;
    }
  });
});

describe("stepPresence — leaving IDLE (materialize)", () => {
  it("emits a materialize flourish, releases the bob, and ramps alpha UP toward 1", () => {
    const sunk: PresenceState = { phase: "sunk", alpha: REST_SUNK_ALPHA };
    const first = stepPresence(sunk, false, 60, false);
    expect(first.flourish).toBe("materialize");
    expect(first.suppressBob).toBe(false);
    expect(first.state.phase).toBe("rising");
    expect(first.state.alpha).toBeGreaterThan(REST_SUNK_ALPHA);

    let s = first.state;
    for (let i = 0; i < Math.ceil(REST_FADE_MS / 60) + 2; i++) {
      const step = stepPresence(s, false, 60, false);
      s = step.state;
      expect(step.flourish).toBeNull();
    }
    expect(s.phase).toBe("visible");
    expect(s.alpha).toBe(1);
  });
});

describe("stepPresence — steady phases fire no flourish", () => {
  it("holds a visible active ghost at alpha 1 with no transition", () => {
    const step = stepPresence({ phase: "visible", alpha: 1 }, false, 100, false);
    expect(step.flourish).toBeNull();
    expect(step.suppressBob).toBe(false);
    expect(step.state).toEqual({ phase: "visible", alpha: 1 });
  });
  it("holds a sunk resting ghost at the floor with no transition", () => {
    const step = stepPresence({ phase: "sunk", alpha: REST_SUNK_ALPHA }, true, 100, false);
    expect(step.flourish).toBeNull();
    expect(step.suppressBob).toBe(true);
    expect(step.state).toEqual({ phase: "sunk", alpha: REST_SUNK_ALPHA });
  });
});

describe("stepPresence — reduced motion snaps (no ramp, no motes rendered)", () => {
  it("entering IDLE snaps straight to the steady faded sunk state", () => {
    const step = stepPresence({ phase: "visible", alpha: 1 }, true, 16, true);
    expect(step.state).toEqual({ phase: "sunk", alpha: REST_REDUCED_ALPHA });
    expect(step.suppressBob).toBe(true);
  });
  it("leaving IDLE snaps straight to fully visible", () => {
    const step = stepPresence({ phase: "sunk", alpha: REST_REDUCED_ALPHA }, false, 16, true);
    expect(step.state).toEqual({ phase: "visible", alpha: 1 });
    expect(step.suppressBob).toBe(false);
  });
});
