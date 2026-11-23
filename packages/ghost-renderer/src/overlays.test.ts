// ghostopia ghost-renderer — overlays pure-helper unit tests .
//
// Covers the canvas-free bits: name-tag filler-strip, deterministic identity badge, and the
// map clamp. The PixiJS draw funcs need a canvas and are exercised by the running app/E2E,
// not here.

import { describe, expect, it } from "vitest";

import { clampToBounds, fillerStrip, hashId, identityBadge, BADGE_EMOJI } from "./overlays.js";

describe("fillerStrip", () => {
  it("reduces a URL to its host (dropping scheme/path/www)", () => {
    expect(fillerStrip("https://www.example.com/pricing?x=1")).toBe("example.com");
    expect(fillerStrip("http://acme.io/a/b/c")).toBe("acme.io");
  });
  it("drops filler words from prose", () => {
    expect(fillerStrip("find the SaaS pricing for me")).toBe("SaaS pricing");
    expect(fillerStrip("scrape all listings")).toBe("listings");
  });
  it("truncates overlong labels with an ellipsis", () => {
    const out = fillerStrip("supercalifragilisticexpialidocious brand name", 12);
    expect(out.length).toBeLessThanOrEqual(12);
    expect(out.endsWith("…")).toBe(true);
  });
  it("is empty for empty input", () => {
    expect(fillerStrip("")).toBe("");
    expect(fillerStrip("   ")).toBe("");
  });
});

describe("identityBadge", () => {
  it("is deterministic for a given ghost id", () => {
    const a = identityBadge("ghost-42");
    const b = identityBadge("ghost-42");
    expect(a).toEqual(b);
    expect(BADGE_EMOJI).toContain(a.emoji);
    expect(a.color).toBeGreaterThanOrEqual(0);
    expect(a.color).toBeLessThanOrEqual(0xffffff);
  });
  it("differs across a spread of ids (no single collision bucket)", () => {
    const badges = new Set<string>();
    for (let i = 0; i < 40; i++) {
      const b = identityBadge(`ghost-${i}`);
      badges.add(`${b.color}:${b.emoji}`);
    }
    // not requiring all-unique, but the space must clearly spread.
    expect(badges.size).toBeGreaterThan(20);
  });
  it("hashId is stable + non-negative", () => {
    expect(hashId("x")).toBe(hashId("x"));
    expect(hashId("abc")).toBeGreaterThanOrEqual(0);
  });
});

describe("clampToBounds", () => {
  it("clamps into [lo,hi] and tolerates inverted bounds", () => {
    expect(clampToBounds(5, 0, 10)).toBe(5);
    expect(clampToBounds(-3, 0, 10)).toBe(0);
    expect(clampToBounds(99, 0, 10)).toBe(10);
    expect(clampToBounds(5, 10, 0)).toBe(10); // inverted → pin to lo
    expect(clampToBounds(Number.NaN, 2, 8)).toBe(2);
  });
});
