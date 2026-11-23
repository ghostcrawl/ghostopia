// ghostopia web — overlay-kind → glyph mapping tests (196 FIX 2).

import { describe, expect, it } from "vitest";

import { OVERLAY_GLYPH, overlayGlyph } from "./overlayGlyph";

describe("overlayGlyph (196 FIX 2)", () => {
  it("maps the `work` overlay kind to its working-bubble glyph", () => {
    expect(overlayGlyph("work")).toBe(OVERLAY_GLYPH.work);
    expect(overlayGlyph("work")).not.toBe("");
  });

  it("falls back to the `work` glyph for an unknown overlay kind", () => {
    expect(overlayGlyph("something-else")).toBe(OVERLAY_GLYPH.work);
  });

  it("always yields a non-empty string so the bubble actually renders", () => {
    for (const kind of ["work", "", "unknown", "captcha"]) {
      expect(overlayGlyph(kind).length).toBeGreaterThan(0);
    }
  });
});
