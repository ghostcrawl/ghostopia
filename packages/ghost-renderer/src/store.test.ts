// ghostopia ghost-renderer — store + camera + map-data loader unit tests.
//
// These cover the PURE render bridge (no PixiJS, no canvas): the Zustand store
// mutates as the server envelope/seed shape expects, the camera clamps to sane
// bounds, and loadMapData types maps/graveyard.json. Runs in the vitest `node`
// env — no DOM required.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import { useWorldStore } from "./store";
import {
  boundsFromWorld,
  clampCamera,
  clampZoom,
  panByScreen,
  zoomByFactor,
  MAX_ZOOM,
  MIN_ZOOM,
  type Camera,
} from "./Camera";
import { loadMapData } from "./mapData";

const here = dirname(fileURLToPath(import.meta.url));
const mapPath = resolve(here, "../../../maps/graveyard.json");

function resetStore(): void {
  useWorldStore.setState({
    ghosts: {},
    bubbles: {},
    camera: { x: 0, y: 0, zoom: 2 },
    cameraBounds: null,
    selectedGhostId: null,
  });
}

describe("useWorldStore — bubbles (say/overlay render state)", () => {
  beforeEach(resetStore);

  it("pushBubble adds a bubble entity for a say command", () => {
    useWorldStore.getState().pushBubble("g1", "reading pricing…");
    const b = useWorldStore.getState().bubbles.g1;
    expect(b).toBeDefined();
    expect(b.text).toBe("reading pricing…");
    expect(b.kind).toBe("say"); // default kind
    expect(b.ttlMs).toBeGreaterThan(0);
    expect(b.createdMs).toBeGreaterThan(0);
  });

  it("pushBubble honors the overlay kind and replaces the prior bubble", () => {
    const s = useWorldStore.getState();
    s.pushBubble("g1", "first", "say");
    s.pushBubble("g1", "second", "overlay");
    const b = useWorldStore.getState().bubbles.g1;
    expect(b.text).toBe("second");
    expect(b.kind).toBe("overlay");
    expect(Object.keys(useWorldStore.getState().bubbles)).toHaveLength(1);
  });

  it("ignores an empty/whitespace bubble (no phantom entity)", () => {
    useWorldStore.getState().pushBubble("g1", "   ");
    expect(useWorldStore.getState().bubbles.g1).toBeUndefined();
  });

  it("clearBubble removes a ghost's bubble (the ticker calls it on fade-out)", () => {
    const s = useWorldStore.getState();
    s.pushBubble("g1", "hi");
    s.clearBubble("g1");
    expect(useWorldStore.getState().bubbles.g1).toBeUndefined();
  });

  it("removeGhost also drops its bubble", () => {
    const s = useWorldStore.getState();
    s.upsertGhost({ id: "g1", name: "Blip", home_grave: "grave-1" });
    s.pushBubble("g1", "bye");
    s.removeGhost("g1");
    expect(useWorldStore.getState().bubbles.g1).toBeUndefined();
  });
});

describe("useWorldStore — ghosts", () => {
  beforeEach(resetStore);

  it("upsertGhost inserts a full ghost with contract defaults", () => {
    useWorldStore.getState().upsertGhost({ id: "g1", name: "Blip", home_grave: "grave-1" });
    const g = useWorldStore.getState().ghosts.g1;
    expect(g).toBeDefined();
    expect(g.name).toBe("Blip");
    expect(g.state).toBe("IDLE"); // contract default
    expect(g.position).toBeNull();
    expect(g.section).toBeNull();
  });

  it("upsertGhost merges into an existing ghost (partial update)", () => {
    const s = useWorldStore.getState();
    s.upsertGhost({ id: "g1", name: "Blip", home_grave: "grave-1", section: "research" });
    s.upsertGhost({ id: "g1", state: "WALKING" });
    const g = useWorldStore.getState().ghosts.g1;
    expect(g.state).toBe("WALKING");
    expect(g.name).toBe("Blip"); // preserved through the merge
    expect(g.section).toBe("research");
  });

  it("applyStatusChanged mutates state by ghost_id (the server envelope shape)", () => {
    const s = useWorldStore.getState();
    s.upsertGhost({ id: "g1", name: "Blip", home_grave: "grave-1", state: "IDLE" });
    s.applyStatusChanged({ ghost_id: "g1", state: "EXTRACTING", section: "extraction" });
    const g = useWorldStore.getState().ghosts.g1;
    expect(g.state).toBe("EXTRACTING");
    expect(g.section).toBe("extraction");
  });

  it("applyStatusChanged for an unknown ghost is a no-op (no phantom ghost)", () => {
    useWorldStore.getState().applyStatusChanged({ ghost_id: "ghost-x", state: "ERROR" });
    expect(useWorldStore.getState().ghosts["ghost-x"]).toBeUndefined();
  });

  it("setGhostPosition places a ghost; removeGhost deletes it", () => {
    const s = useWorldStore.getState();
    s.upsertGhost({ id: "g1", name: "Blip", home_grave: "grave-1" });
    s.setGhostPosition("g1", { x: 12, y: 34 });
    expect(useWorldStore.getState().ghosts.g1.position).toEqual({ x: 12, y: 34 });
    s.removeGhost("g1");
    expect(useWorldStore.getState().ghosts.g1).toBeUndefined();
  });

  it("selectGhost only selects an existing ghost", () => {
    const s = useWorldStore.getState();
    s.selectGhost("nope");
    expect(useWorldStore.getState().selectedGhostId).toBeNull();
    s.upsertGhost({ id: "g1", name: "Blip", home_grave: "grave-1" });
    s.selectGhost("g1");
    expect(useWorldStore.getState().selectedGhostId).toBe("g1");
  });

  it("removing the selected ghost clears the selection", () => {
    const s = useWorldStore.getState();
    s.upsertGhost({ id: "g1", name: "Blip", home_grave: "grave-1" });
    s.selectGhost("g1");
    s.removeGhost("g1");
    expect(useWorldStore.getState().selectedGhostId).toBeNull();
  });

  it("is readable via getState() outside React (the ticker path)", () => {
    useWorldStore.getState().upsertGhost({ id: "g1", name: "Blip", home_grave: "grave-1" });
    // simulate the ticker reading the store without any React hook
    const snapshot = useWorldStore.getState();
    expect(Object.keys(snapshot.ghosts)).toContain("g1");
    expect(typeof snapshot.camera.zoom).toBe("number");
  });
});

describe("useWorldStore — camera", () => {
  beforeEach(resetStore);

  it("setCamera clamps zoom to sane bounds", () => {
    const s = useWorldStore.getState();
    s.setCamera({ zoom: 9999 });
    expect(useWorldStore.getState().camera.zoom).toBe(MAX_ZOOM);
    s.setCamera({ zoom: 0.0001 });
    expect(useWorldStore.getState().camera.zoom).toBe(MIN_ZOOM);
  });

  it("setCameraBounds re-clamps the camera centre into the world rect", () => {
    const s = useWorldStore.getState();
    s.setCamera({ x: 100000, y: -100000 });
    s.setCameraBounds({ minX: 0, maxX: 384, minY: 0, maxY: 288 });
    const cam = useWorldStore.getState().camera;
    expect(cam.x).toBe(384);
    expect(cam.y).toBe(0);
  });

  it("panCamera moves the world 1:1 under the cursor (screen delta / zoom)", () => {
    const s = useWorldStore.getState();
    s.setCamera({ x: 100, y: 100, zoom: 2 });
    s.panCamera(20, 40); // screen delta
    const cam = useWorldStore.getState().camera;
    expect(cam.x).toBe(100 - 20 / 2);
    expect(cam.y).toBe(100 - 40 / 2);
  });

  it("zoomCamera multiplies and clamps", () => {
    const s = useWorldStore.getState();
    s.setCamera({ zoom: 2 });
    s.zoomCamera(2);
    expect(useWorldStore.getState().camera.zoom).toBe(4);
    s.zoomCamera(1000);
    expect(useWorldStore.getState().camera.zoom).toBe(MAX_ZOOM);
  });
});

describe("Camera — pure helpers", () => {
  it("clampZoom pins to the sane range and handles NaN", () => {
    expect(clampZoom(1000)).toBe(MAX_ZOOM);
    expect(clampZoom(0)).toBe(MIN_ZOOM);
    expect(clampZoom(Number.NaN)).toBe(1);
  });

  it("clampCamera without bounds only clamps zoom", () => {
    const cam: Camera = { x: 99999, y: -99999, zoom: 100 };
    const out = clampCamera(cam);
    expect(out.x).toBe(99999);
    expect(out.zoom).toBe(MAX_ZOOM);
  });

  it("panByScreen and zoomByFactor respect bounds", () => {
    const bounds = boundsFromWorld(384, 288);
    const cam: Camera = { x: 0, y: 0, zoom: 1 };
    const panned = panByScreen(cam, -100000, -100000, bounds);
    expect(panned.x).toBe(384);
    expect(panned.y).toBe(288);
    const zoomed = zoomByFactor(cam, 0.001, bounds);
    expect(zoomed.zoom).toBe(MIN_ZOOM);
  });
});

describe("loadMapData — maps/graveyard.json", () => {
  it("types the shipped map: regions + graves + workstations + grid", () => {
    const json = JSON.parse(readFileSync(mapPath, "utf8"));
    const map = loadMapData(json);
    expect(map.name).toBe("graveyard");
    expect(map.width).toBe(40);
    expect(map.height).toBe(28);
    expect(map.tileSize).toBe(16); // tile_size -> tileSize
    // 195: regions are the labeled work sections + result departments (resting is
    // unlabeled land, so it carries no region).
    expect(Object.keys(map.regions)).toContain("mystery-books");
    expect(Object.keys(map.regions)).toContain("horror-books");
    expect(Object.keys(map.regions)).toContain("research");
    expect(Object.keys(map.regions)).toContain("extraction");
    expect(Object.keys(map.regions)).toContain("verify");
    expect(map.regions["resting"]).toBeUndefined();
    expect(map.regions["horror-books"]).toEqual({ x: 1, y: 1, w: 8, h: 5 });
    expect(map.graves.length).toBeGreaterThanOrEqual(5);
    expect(map.workstations.length).toBeGreaterThanOrEqual(5);
    expect(map.graves[0].id).toBe("grave-1");
    expect(map.walkable.length).toBe(28);
    expect(map.walkable[0].length).toBe(40);
  });

  it("throws on a malformed map (missing name)", () => {
    expect(() => loadMapData({ width: 1 })).toThrow();
  });
});
