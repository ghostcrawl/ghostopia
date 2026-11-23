import { describe, it, expect } from "vitest";
import { facingFromVector, type Facing8 } from "./facing";

// Screen-space convention: +dx = right (east), +dy = DOWN (south).
// facingFromVector buckets the heading into 8 compass sectors (45deg each) and
// flags the west set (sw/w/nw) as mirror:true — those are drawn by horizontally
// flipping the authored east-side art (se/e/ne), never authored separately.

describe("facingFromVector — 8 cardinal/ordinal headings", () => {
  const cases: Array<[number, number, Facing8, boolean]> = [
    [0, 1, "s", false], // straight down = south
    [1, 1, "se", false], // down-right = south-east
    [1, 0, "e", false], // straight right = east
    [1, -1, "ne", false], // up-right = north-east
    [0, -1, "n", false], // straight up = north
    [-1, -1, "nw", true], // up-left = north-west (mirror of ne)
    [-1, 0, "w", true], // straight left = west (mirror of e)
    [-1, 1, "sw", true], // down-left = south-west (mirror of se)
  ];
  for (const [dx, dy, facing, mirror] of cases) {
    it(`(${dx},${dy}) -> ${facing} mirror=${mirror}`, () => {
      const r = facingFromVector(dx, dy);
      expect(r.facing).toBe(facing);
      expect(r.mirror).toBe(mirror);
    });
  }
});

describe("facingFromVector — magnitude independence + off-axis", () => {
  it("is scale-invariant (only heading matters)", () => {
    expect(facingFromVector(10, 0)).toEqual(facingFromVector(0.3, 0));
    expect(facingFromVector(-4, -4)).toEqual(facingFromVector(-1, -1));
  });

  it("snaps a shallow down-right angle to SE", () => {
    // ~30deg below the +x axis: nearer the SE bucket than pure E
    const r = facingFromVector(2, 1.5);
    expect(r.facing).toBe("se");
  });

  it("snaps a steep down-right angle to S", () => {
    const r = facingFromVector(1, 3);
    expect(r.facing).toBe("s");
  });

  it("mirror is true exactly for the west set", () => {
    expect(facingFromVector(-3, 1).mirror).toBe(true); // sw
    expect(facingFromVector(-3, -1).mirror).toBe(true); // nw
    expect(facingFromVector(3, 1).mirror).toBe(false); // se
  });
});

describe("facingFromVector — diagonal tie-breaks + zero vector", () => {
  it("resolves the exact E/SE boundary deterministically (round-half-up -> SE)", () => {
    // heading = 22.5deg exactly: dx=cos, dy=sin of 22.5deg
    const a = (22.5 * Math.PI) / 180;
    const r = facingFromVector(Math.cos(a), Math.sin(a));
    expect(r.facing).toBe("se");
  });

  it("resolves the exact S/SE boundary deterministically (round-half-up -> S)", () => {
    // heading = 67.5deg exactly
    const a = (67.5 * Math.PI) / 180;
    const r = facingFromVector(Math.cos(a), Math.sin(a));
    expect(r.facing).toBe("s");
  });

  it("a zero vector returns the idle default (facing s, no mirror)", () => {
    expect(facingFromVector(0, 0)).toEqual({ facing: "s", mirror: false });
  });
});
