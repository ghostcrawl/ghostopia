// ghostopia web — inspectorStore.applyView .
//
// The server's `browser.view` envelope now carries an optional honest `reason` when the live
// browser frame can't stream yet (session not open, or the workspace live-view capability is
// off). The store keeps it per-ghost as `viewReason` so the Browser tab can show WHY instead of
// an eternal "No live view yet…" placeholder. A frame arriving clears the reason.

import { beforeEach, describe, expect, it } from "vitest";

import { inspectorStore, sessionFor } from "./inspectorStore";

describe("inspectorStore.applyView (R7 honest reason)", () => {
  beforeEach(() => {
    inspectorStore.setState({ sessions: {} });
  });

  it("stores an honest unavailability reason per ghost", () => {
    inspectorStore.getState().applyView("g1", "Live view isn't turned on for this workspace yet…");
    expect(sessionFor("g1")?.viewReason).toBe("Live view isn't turned on for this workspace yet…");
  });

  it("clears the reason when null is applied (the live view came up)", () => {
    const s = inspectorStore.getState();
    s.applyView("g1", "Waking a browser for this ghost…");
    s.applyView("g1", null);
    expect(sessionFor("g1")?.viewReason).toBeNull();
  });

  it("a fresh session has a null viewReason", () => {
    expect(sessionFor("g2")?.viewReason ?? null).toBeNull();
  });
});
