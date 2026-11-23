// ghostopia ghost-renderer — 50-ghost batched position-write probe (Task 3).
//
// /Pitfall 2: the rAF walk-interpolation loop must flush ALL moved ghosts in ONE
// store `set` per frame, not N. This headless probe counts store writes (subscriber
// notifications) — not wall-clock — so it is deterministic in CI. It proves the frame
// budget stays flat with ghost count: a 50-ghost frame is a SINGLE store write, so the
// HUD does not re-render per position tick.

import { beforeEach, describe, expect, it } from "vitest";

import { useWorldStore } from "./store";
import type { Point } from "./contract.js";

function reset(): void {
  useWorldStore.setState({ ghosts: {} });
}

function seed(n: number): string[] {
  const ids: string[] = [];
  const upsert = useWorldStore.getState().upsertGhost;
  for (let i = 0; i < n; i++) {
    const id = `g${i}`;
    ids.push(id);
    upsert({ id, name: id, state: "WALKING", position: { x: 0, y: 0 } });
  }
  return ids;
}

describe("setGhostPositions — batched per-frame flush (50-ghost perf probe)", () => {
  beforeEach(reset);

  it("a 50-ghost frame flushes as exactly ONE store write (frame budget stays flat)", () => {
    const ids = seed(50);
    // simulate one frame's worth of moved positions (what advanceWalks accumulates)
    const moved: Record<string, Point> = {};
    for (let i = 0; i < ids.length; i++) moved[ids[i]] = { x: i, y: i * 2 };

    let writes = 0;
    const unsub = useWorldStore.subscribe(() => {
      writes += 1;
    });
    useWorldStore.getState().setGhostPositions(moved);
    unsub();

    // ONE batched write for all 50 ghosts — not 50.
    expect(writes).toBe(1);
    // every ghost's position was applied by the single write
    const ghosts = useWorldStore.getState().ghosts;
    for (let i = 0; i < ids.length; i++) {
      expect(ghosts[ids[i]].position).toEqual({ x: i, y: i * 2 });
    }
  });

  it("the un-batched path would storm the store (N writes) — the regression this guards", () => {
    const ids = seed(50);
    let writes = 0;
    const unsub = useWorldStore.subscribe(() => {
      writes += 1;
    });
    const setOne = useWorldStore.getState().setGhostPosition;
    for (let i = 0; i < ids.length; i++) setOne(ids[i], { x: i, y: i });
    unsub();
    expect(writes).toBe(50); // documents the write storm the batch avoids
  });

  it("skips unknown ids (no phantom placement) and does not churn the ghosts object on a no-op frame", () => {
    seed(3);
    const before = useWorldStore.getState().ghosts;
    // empty frame → the ghosts object reference is UNCHANGED (no GC churn)
    useWorldStore.getState().setGhostPositions({});
    expect(useWorldStore.getState().ghosts).toBe(before);
    // a frame referencing only unknown ghosts → no fresh object, no phantom entry
    useWorldStore.getState().setGhostPositions({ nope: { x: 9, y: 9 } });
    expect(useWorldStore.getState().ghosts).toBe(before);
    expect(useWorldStore.getState().ghosts["nope"]).toBeUndefined();
  });

  it("a mixed frame (known + unknown) writes once and places only the known ghosts", () => {
    const ids = seed(2);
    let writes = 0;
    const unsub = useWorldStore.subscribe(() => {
      writes += 1;
    });
    useWorldStore.getState().setGhostPositions({
      [ids[0]]: { x: 5, y: 5 },
      [ids[1]]: { x: 6, y: 6 },
      ghostzero: { x: 100, y: 100 },
    });
    unsub();
    expect(writes).toBe(1);
    expect(useWorldStore.getState().ghosts[ids[0]].position).toEqual({ x: 5, y: 5 });
    expect(useWorldStore.getState().ghosts["ghostzero"]).toBeUndefined();
  });
});
