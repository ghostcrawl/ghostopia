// ghostopia web — the workforce-presence predicate .
//
// The client's "is the workforce running?" derivation must match the server's authoritative
// workforce set (gc_event_source _has_live_workforce/_stop_workforce): workforce-*/dept-*
// AND the background baton stage-* ghosts. The regression: stage-* ghosts alone (featured
// dept-* transiently gone between loop cycles) must still read as running.

import { describe, expect, it } from "vitest";

import { anyWorkforceGhost, isWorkforceGhostId } from "./workforce";

describe("isWorkforceGhostId (mirrors the server authoritative set)", () => {
  it("matches workforce-, dept-, and stage- prefixes", () => {
    expect(isWorkforceGhostId("workforce-1")).toBe(true);
    expect(isWorkforceGhostId("dept-horror-books-0")).toBe(true);
    expect(isWorkforceGhostId("stage-books-3")).toBe(true);
  });

  it("does not match unrelated ghost ids", () => {
    expect(isWorkforceGhostId("ghost-42")).toBe(false);
    expect(isWorkforceGhostId("mission-abc")).toBe(false);
  });
});

describe("anyWorkforceGhost (drives workforceRunning)", () => {
  it("reads running when ONLY background stage-* ghosts are live ()", () => {
    // the featured dept-* ghosts are transiently absent between loop cycles — the baton
    // stage-* ghosts are still live, so the workforce is running (matches the server).
    expect(anyWorkforceGhost(["stage-verify-1", "stage-extraction-2"])).toBe(true);
  });

  it("reads running for featured workforce/dept ghosts", () => {
    expect(anyWorkforceGhost(["dept-spooky-masks-0"])).toBe(true);
    expect(anyWorkforceGhost(["workforce-0"])).toBe(true);
  });

  it("reads NOT running with an empty world or only non-workforce ghosts", () => {
    expect(anyWorkforceGhost([])).toBe(false);
    expect(anyWorkforceGhost(["ghost-1", "mission-x"])).toBe(false);
  });
});
