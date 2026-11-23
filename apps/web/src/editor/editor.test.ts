// ghostopia web — Graveyard Builder pure-logic tests (tools + history + mapio).

import { describe, expect, it } from "vitest";

import { History, MIN_HISTORY_DEPTH } from "./history.js";
import {
  cloneDraft,
  deleteProp,
  footprintTiles,
  footprintValid,
  moveProp,
  paintPlot,
  placeProp,
  propIndexAt,
  recolorProp,
  renamePlot,
  rotateProp,
  toggleProp,
  type DraftMap,
  type Footprints,
} from "./tools.js";
import { draftToWire, exportMapJson, importMapJson, wireToDraft } from "./mapio.js";

const FOOTPRINTS: Footprints = { lantern: { w: 1, h: 1 }, crypt: { w: 2, h: 2 } };

function fixture(): DraftMap {
  // a 5×5 all-walkable yard with one plot + one grave + one workstation.
  return {
    name: "test-yard",
    width: 5,
    height: 5,
    tileSize: 16,
    walkable: Array.from({ length: 5 }, () => Array.from({ length: 5 }, () => 1)),
    regions: { home: { x: 0, y: 0, w: 5, h: 5 } },
    areas: [{ id: "home", section: "home", x: 0, y: 0, w: 5, h: 5 }],
    placedProps: [],
    graves: [{ id: "grave-1", type: "grave", x: 0, y: 0, region: "home" }],
    workstations: [{ id: "ws-1", type: "workstation", x: 4, y: 4, section: "home" }],
    directionalCollision: [{ x: 2, y: 2, blocked: ["N"] }],
  };
}

describe("tools — footprint geometry", () => {
  it("footprintTiles covers the full box for a 2×2 prop", () => {
    expect(footprintTiles("crypt", { x: 1, y: 1 }, FOOTPRINTS)).toEqual([
      { x: 1, y: 1 },
      { x: 2, y: 1 },
      { x: 1, y: 2 },
      { x: 2, y: 2 },
    ]);
  });

  it("footprintValid rejects out-of-bounds, blocked terrain, and overlaps", () => {
    const d = fixture();
    expect(footprintValid(d, "lantern", { x: 2, y: 2 }, FOOTPRINTS)).toBe(true);
    expect(footprintValid(d, "crypt", { x: 4, y: 4 }, FOOTPRINTS)).toBe(false); // hangs off edge
    const blocked = cloneDraft(d);
    blocked.walkable[2][2] = 0;
    expect(footprintValid(blocked, "lantern", { x: 2, y: 2 }, FOOTPRINTS)).toBe(false);
    const withProp = placeProp(d, "lantern", { x: 2, y: 2 }, FOOTPRINTS);
    expect(footprintValid(withProp, "lantern", { x: 2, y: 2 }, FOOTPRINTS)).toBe(false); // overlap
  });
});

describe("tools — edit ops are pure + immutable", () => {
  it("placeProp appends without mutating the input", () => {
    const d = fixture();
    const next = placeProp(d, "lantern", { x: 1, y: 1 }, FOOTPRINTS, { state: "on" });
    expect(d.placedProps).toHaveLength(0); // input untouched
    expect(next.placedProps).toHaveLength(1);
    expect(next.placedProps[0]).toMatchObject({ catalogId: "lantern", state: "on", tint: null });
  });

  it("placeProp is a no-op on an invalid placement", () => {
    const d = fixture();
    expect(placeProp(d, "crypt", { x: 4, y: 4 }, FOOTPRINTS)).toBe(d);
  });

  it("rotate/toggle/recolor cycle with wrap-around", () => {
    let d = placeProp(fixture(), "lantern", { x: 1, y: 1 }, FOOTPRINTS);
    d = rotateProp(d, 0, ["s", "e", "n", "w"]);
    expect(d.placedProps[0].orientation).toBe("e");
    d = toggleProp(d, 0, ["off", "on"]);
    expect(d.placedProps[0].state).toBe("off");
    d = toggleProp(d, 0, ["off", "on"]);
    expect(d.placedProps[0].state).toBe("on");
    d = recolorProp(d, 0, [0x111111, 0x222222]);
    expect(d.placedProps[0].tint).toBe(0x111111);
  });

  it("moveProp respects validity; deleteProp removes; propIndexAt finds the topmost", () => {
    let d = placeProp(fixture(), "lantern", { x: 1, y: 1 }, FOOTPRINTS);
    expect(propIndexAt(d, { x: 1, y: 1 }, FOOTPRINTS)).toBe(0);
    d = moveProp(d, 0, { x: 3, y: 3 }, FOOTPRINTS);
    expect(d.placedProps[0].tile).toEqual({ x: 3, y: 3 });
    d = deleteProp(d, 0);
    expect(d.placedProps).toHaveLength(0);
    expect(propIndexAt(d, { x: 3, y: 3 }, FOOTPRINTS)).toBe(-1);
  });

  it("paintPlot reassigns a plot's section; renamePlot avoids collisions", () => {
    let d = fixture();
    d = paintPlot(d, "home", "research");
    expect(d.areas[0].section).toBe("research");
    d = renamePlot(d, "home", "yard");
    expect(d.areas[0].id).toBe("yard");
    // renaming to an existing id is a no-op.
    const two = { ...d, areas: [...d.areas, { id: "other", section: "x", x: 0, y: 0, w: 1, h: 1 }] };
    expect(renamePlot(two, "yard", "other")).toBe(two);
  });
});

describe("history — bounded undo/redo", () => {
  it("undo/redo walk the snapshot stack", () => {
    const h = new History<number>(0, 30);
    h.push(1);
    h.push(2);
    expect(h.current).toBe(2);
    expect(h.canUndo).toBe(true);
    expect(h.undo()).toBe(1);
    expect(h.undo()).toBe(0);
    expect(h.canUndo).toBe(false);
    expect(h.redo()).toBe(1);
    // a new push forks history (clears redo).
    h.push(9);
    expect(h.canRedo).toBe(false);
    expect(h.current).toBe(9);
  });

  it("holds at least the minimum depth and caps growth", () => {
    const h = new History<number>(0, 10); // requested below the floor → clamped up
    for (let i = 1; i <= 100; i++) h.push(i);
    expect(h.depth).toBeGreaterThanOrEqual(MIN_HISTORY_DEPTH);
    // an identical push records no history.
    const before = h.depth;
    h.push(h.current);
    expect(h.depth).toBe(before);
  });
});

describe("mapio — wire round-trip", () => {
  it("draftToWire → wireToDraft is lossless", () => {
    let d = placeProp(fixture(), "lantern", { x: 2, y: 2 }, FOOTPRINTS, { state: "on" });
    d = recolorProp(d, 0, [0xabcdef]);
    const round = wireToDraft(draftToWire(d));
    expect(round.width).toBe(d.width);
    expect(round.placedProps[0]).toMatchObject({ catalogId: "lantern", state: "on", tint: 0xabcdef });
    expect(round.directionalCollision).toEqual(d.directionalCollision);
    expect(round.workstations[0].id).toBe("ws-1");
  });

  it("exportMapJson → importMapJson round-trips through JSON text", () => {
    const d = placeProp(fixture(), "lantern", { x: 1, y: 3 }, FOOTPRINTS);
    const text = exportMapJson(d);
    const back = importMapJson(text);
    expect(back.placedProps[0].tile).toEqual({ x: 1, y: 3 });
    expect(JSON.parse(text).placed_props[0].catalog_id).toBe("lantern");
  });

  it("importMapJson throws loudly on malformed input", () => {
    expect(() => importMapJson("{not json")).toThrow(/JSON/);
    expect(() => importMapJson(JSON.stringify({ width: 2, height: 2, walkable: [[1, 1]] }))).toThrow(/height/);
  });
});
