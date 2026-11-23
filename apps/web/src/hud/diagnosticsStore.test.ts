import { describe, expect, it } from "vitest";

import { diagnosticsStore, lastEventAgeMs } from "./diagnosticsStore";

describe("diagnosticsStore ( P18) — real system health", () => {
  it("applies a diagnostics.system payload with real pool fields", () => {
    diagnosticsStore.getState().applySystem({
      pool_active: 3,
      pool_max: 8,
      headroom: 5,
      ghost_count: 4,
      sections: { research: 2, extraction: 1 },
    });
    const s = diagnosticsStore.getState().system;
    expect(s).not.toBeNull();
    expect(s?.poolActive).toBe(3);
    expect(s?.poolMax).toBe(8);
    expect(s?.headroom).toBe(5);
    expect(s?.sections).toEqual({ research: 2, extraction: 1 });
    // : unsaturated snapshot has no back-pressure notice + zero queue depth.
    expect(s?.queueDepth).toBe(0);
    expect(s?.saturated).toBe(false);
    expect(s?.notice).toBeNull();
  });

  it("surfaces queue depth + saturation + the curated back-pressure notice ()", () => {
    diagnosticsStore.getState().applySystem({
      pool_active: 8,
      pool_max: 8,
      headroom: 0,
      queue_depth: 5,
      saturated: true,
      notice: "Waiting for a free lantern…",
      ghost_count: 8,
      sections: { extraction: 8 },
    });
    const s = diagnosticsStore.getState().system;
    expect(s?.queueDepth).toBe(5);
    expect(s?.saturated).toBe(true);
    expect(s?.notice).toBe("Waiting for a free lantern…");
  });

  it("clear() drops the snapshot", () => {
    diagnosticsStore.getState().applySystem({ pool_active: 1, pool_max: 8 });
    diagnosticsStore.getState().clear();
    expect(diagnosticsStore.getState().system).toBeNull();
  });
});

describe("lastEventAgeMs — a REAL freshness signal (never invented)", () => {
  it("returns null when a ghost has no events (shown as —, not a fake age)", () => {
    expect(lastEventAgeMs([], 10_000)).toBeNull();
  });

  it("computes the age of the newest event (ts in seconds → ms)", () => {
    // newest event at t=5s; now = 8000ms → age 3000ms.
    expect(lastEventAgeMs([{ ts: 2 }, { ts: 5 }, { ts: 3 }], 8000)).toBe(3000);
  });

  it("never returns a negative age", () => {
    expect(lastEventAgeMs([{ ts: 100 }], 0)).toBe(0);
  });
});
