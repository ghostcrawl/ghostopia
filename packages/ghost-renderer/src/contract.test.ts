// ghostopia ghost-renderer — additive-optional contract field tests (Task 1).
//
// The Ghost contract gained three ADDITIVE-OPTIONAL fields consumed downstream by
// the phase (facing, workKind, persona). This test is the single
// definition-site guard: a legacy Ghost WITHOUT the new fields must still be a
// valid Ghost (backward compatible), and the new fields must be optional + typed.

import { describe, expect, it } from "vitest";

import type { Facing, Ghost, GhostStatusChanged } from "./contract.js";

describe("Ghost contract — additive optional facing/workKind/persona ()", () => {
  it("a legacy Ghost with none of the new fields still type-checks + is a valid object", () => {
    const legacy: Ghost = {
      id: "g1",
      name: "Wisp",
      home_grave: "",
      state: "IDLE",
    };
    expect(legacy.facing).toBeUndefined();
    expect(legacy.workKind).toBeUndefined();
    expect(legacy.persona).toBeUndefined();
  });

  it("carries an 8-way facing, a discriminated workKind, and a sanitized persona string", () => {
    const facings: Facing[] = ["s", "se", "e", "ne", "n", "nw", "w", "sw"];
    for (const f of facings) {
      const g: Ghost = {
        id: "g2",
        name: "Wisp",
        home_grave: "",
        state: "AT_WORKSTATION",
        facing: f,
        workKind: "browser-nav",
        persona: "browsing as a Chrome-class desktop from a US locale",
      };
      expect(g.facing).toBe(f);
    }
    const apiOnly: Ghost = {
      id: "g3",
      name: "Wisp",
      home_grave: "",
      state: "EXTRACTING",
      workKind: "api-only",
    };
    expect(apiOnly.workKind).toBe("api-only");
  });

  it("GhostStatusChanged mirrors the additive facing so the face command can thread it", () => {
    const upd: GhostStatusChanged = { ghost_id: "g1", state: "AT_WORKSTATION", facing: "e" };
    expect(upd.facing).toBe("e");
    // legacy status update without facing still valid
    const legacy: GhostStatusChanged = { ghost_id: "g1", state: "WALKING" };
    expect(legacy.facing).toBeUndefined();
  });
});
