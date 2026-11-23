import { describe, expect, it } from "vitest";

import { SURFACE_LABELS, surfaceLabel } from "./surfaceVocab";

describe("surfaceVocab", () => {
  it("remaps the S6 engine/UI words to a cohesive ghostly vocabulary (display-only)", () => {
    expect(surfaceLabel("workstation")).toBe("haunt");
    expect(surfaceLabel("walk")).toBe("drift");
    expect(surfaceLabel("walking")).toBe("drift");
    expect(surfaceLabel("mission")).toBe("summoning");
    expect(surfaceLabel("home")).toBe("resting place");
    expect(surfaceLabel("drag to pan")).toBe("drag to drift the view");
    // the residual run-workforce trigger label : a user-facing "summon the departments"
    expect(surfaceLabel("Run workforce")).toBe("summon the departments");
  });

  it("keeps clarity words literal — never obscures department / findings / Data Graveyard", () => {
    // an unmapped clarity word passes through UNCHANGED (not browser_behavior_applied-uppercase) — these are
    // already lowercase UI copy and must stay perfectly clear (clarity guardrail).
    expect(surfaceLabel("department")).toBe("department");
    expect(surfaceLabel("findings")).toBe("findings");
    expect(surfaceLabel("Data Graveyard")).toBe("Data Graveyard");
    expect(surfaceLabel("roster")).toBe("roster");
  });

  it("returns an unmapped phrase verbatim (no SCREAMING_CASE browser_behavior_engine)", () => {
    expect(surfaceLabel("scroll to zoom")).toBe("scroll to zoom");
    expect(surfaceLabel("")).toBe("");
  });

  it("matches case-insensitively so a stray Cased literal still themes", () => {
    expect(surfaceLabel("WORKSTATION")).toBe("haunt");
    expect(surfaceLabel("Mission")).toBe("summoning");
  });

  it("exposes the raw map so callers can compose it (parity with GHOST_STATE_LABELS)", () => {
    expect(SURFACE_LABELS.workstation).toBe("haunt");
    expect(SURFACE_LABELS["drag to pan"]).toBe("drag to drift the view");
  });
});
