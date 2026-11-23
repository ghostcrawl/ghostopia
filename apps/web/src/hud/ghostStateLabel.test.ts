import { describe, expect, it } from "vitest";

import { ghostStateLabel } from "./ghostStateLabel";

describe("ghostStateLabel", () => {
  it("maps engine states to ghost-appropriate phrases (no literal 'walking')", () => {
    expect(ghostStateLabel("WALKING")).toBe("drifting");
    expect(ghostStateLabel("RETURNING_HOME")).toBe("drifting home");
    expect(ghostStateLabel("IDLE")).toBe("resting");
    expect(ghostStateLabel("EXTRACTING")).toBe("gathering");
    expect(ghostStateLabel("OPENING_BROWSER")).toBe("conjuring");
    expect(ghostStateLabel("PROCESSING")).toBe("divining");
    expect(ghostStateLabel("ERROR")).toBe("spooked");
    expect(ghostStateLabel("COMPLETED")).toBe("delivered");
    // the non-schema live strings the pool/orchestrator can still emit
    expect(ghostStateLabel("WORKING")).toBe("haunting");
    expect(ghostStateLabel("CANCELLED")).toBe("dismissed");
  });

  it("falls back to a lowercase form for an unmapped state", () => {
    expect(ghostStateLabel("SOME_NEW_STATE")).toBe("some new state");
  });

  it("defaults null/empty to resting", () => {
    expect(ghostStateLabel(null)).toBe("resting");
    expect(ghostStateLabel(undefined)).toBe("resting");
    expect(ghostStateLabel("")).toBe("resting");
  });
});
