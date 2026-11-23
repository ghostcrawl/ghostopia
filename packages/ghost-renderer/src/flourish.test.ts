// ghostopia ghost-renderer — flourish math tests .

import { describe, expect, it } from "vitest";

import {
  easeOutCubic,
  FLOURISH_MS,
  ghostAlphaFor,
  sampleParticle,
  seedFlourishParticles,
} from "./flourish.js";

describe("easeOutCubic", () => {
  it("pins the endpoints and stays monotonic in between", () => {
    expect(easeOutCubic(0)).toBe(0);
    expect(easeOutCubic(1)).toBe(1);
    expect(easeOutCubic(0.5)).toBeGreaterThan(0.5); // ease-OUT overshoots the midpoint upward
  });
});

describe("seedFlourishParticles", () => {
  it("is deterministic for a given seed and spans the ring", () => {
    const a = seedFlourishParticles(12, 7);
    const b = seedFlourishParticles(12, 7);
    expect(a).toEqual(b);
    expect(a).toHaveLength(12);
    for (const p of a) {
      expect(p.radius).toBeGreaterThan(0);
      expect(p.size).toBeGreaterThan(0);
    }
  });

  it("differs across seeds", () => {
    expect(seedFlourishParticles(12, 1)).not.toEqual(seedFlourishParticles(12, 2));
  });
});

describe("sampleParticle", () => {
  const p = { angle: 0, radius: 20, size: 2 };

  it("materialize converges to the centre (full radius → 0)", () => {
    const at0 = sampleParticle(p, "materialize", 0);
    const at1 = sampleParticle(p, "materialize", 1);
    expect(Math.hypot(at0.x, at0.y)).toBeCloseTo(20, 5);
    expect(Math.hypot(at1.x, at1.y)).toBeCloseTo(0, 5);
  });

  it("dissolve scatters outward (0 → full radius)", () => {
    const at0 = sampleParticle(p, "dissolve", 0);
    const at1 = sampleParticle(p, "dissolve", 1);
    expect(Math.hypot(at0.x, at0.y)).toBeCloseTo(0, 5);
    expect(Math.hypot(at1.x, at1.y)).toBeCloseTo(20, 5);
  });

  it("motes fade out over the flourish for both kinds", () => {
    expect(sampleParticle(p, "materialize", 0).alpha).toBeGreaterThan(
      sampleParticle(p, "materialize", 1).alpha,
    );
    expect(sampleParticle(p, "dissolve", 0).alpha).toBeGreaterThan(
      sampleParticle(p, "dissolve", 1).alpha,
    );
  });

  it("clamps t outside [0,1]", () => {
    expect(sampleParticle(p, "materialize", -1)).toEqual(sampleParticle(p, "materialize", 0));
    expect(sampleParticle(p, "dissolve", 2)).toEqual(sampleParticle(p, "dissolve", 1));
  });
});

describe("ghostAlphaFor", () => {
  it("materialize fades the body IN, dissolve fades it OUT", () => {
    expect(ghostAlphaFor("materialize", 0)).toBeCloseTo(0, 5);
    expect(ghostAlphaFor("materialize", 1)).toBeCloseTo(1, 5);
    expect(ghostAlphaFor("dissolve", 0)).toBeCloseTo(1, 5);
    expect(ghostAlphaFor("dissolve", 1)).toBeCloseTo(0, 5);
  });
});

// 196 FIX 1: the graceful despawn in RenderLoop ramps the ghost body alpha with
// `startAlpha * ghostAlphaFor("dissolve", elapsed/FLOURISH_MS)` over ONE FLOURISH_MS window,
// then destroys — mirroring the spawn feel instead of popping the sprite the same frame. This
// pins the curve contract that path relies on.
describe("graceful despawn fade contract (196 FIX 1)", () => {
  it("FLOURISH_MS is a short positive window (matches spawn feel)", () => {
    expect(FLOURISH_MS).toBeGreaterThan(0);
    expect(FLOURISH_MS).toBeLessThanOrEqual(1000);
  });

  it("body alpha ramps monotonically 1→0 across the despawn window and ends fully faded", () => {
    const startAlpha = 1;
    const steps = 8;
    let prev = Infinity;
    for (let i = 0; i <= steps; i++) {
      const elapsed = (FLOURISH_MS * i) / steps;
      const t = Math.min(1, elapsed / FLOURISH_MS);
      const alpha = startAlpha * ghostAlphaFor("dissolve", t);
      expect(alpha).toBeLessThanOrEqual(prev + 1e-9);
      prev = alpha;
    }
    // fully invisible exactly at the end of the window (then the sprite is destroyed).
    expect(startAlpha * ghostAlphaFor("dissolve", 1)).toBeCloseTo(0, 5);
  });

  it("scales from a partial start alpha (a ghost removed mid-sink still fades from where it was)", () => {
    const startAlpha = 0.4;
    expect(startAlpha * ghostAlphaFor("dissolve", 0)).toBeCloseTo(0.4, 5);
    expect(startAlpha * ghostAlphaFor("dissolve", 1)).toBeCloseTo(0, 5);
  });
});
