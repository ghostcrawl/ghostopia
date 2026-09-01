// ghostopia web — the Graveyard Builder PURE edit ops + draft map model.
//
// Every editor edit is a PURE function over an immutable `DraftMap` (a base-terrain map +
// a placed-props/areas/destinations layer). No live-world mutation happens here — the editor
// edits a DRAFT cloned from the live map and only a validated `map.save` swaps the live world.
// These ops are the unit-tested brain of the editor; the store just sequences them + records
// history. NOTHING here imports PixiJS, the SDK, or a key — pure data in / pure data out.

/** A tile coordinate. */
export interface Tile {
  x: number;
  y: number;
}

/** A tile-rectangle (region / plot bounds). */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A placed prop in the draft (mirrors the shared PlacedProp + an editor tint). */
export interface DraftProp {
  catalogId: string;
  tile: Tile;
  orientation: string;
  state: string | null;
  /** optional 0xRRGGBB recolor tint (editor "recolor"); null = the prop's native art. */
  tint: number | null;
}

/** A painted section PLOT (area) the editor edits. */
export interface DraftArea {
  id: string;
  section: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A one-way directional edge mask entry (preserved through the editor, not edited by it). */
export interface DirCollision {
  x: number;
  y: number;
  blocked: string[];
}

/** A destination (grave / workstation) the editor places + moves. */
export interface DraftDestination {
  id: string;
  type: string;
  x: number;
  y: number;
  region?: string | null;
  section?: string | null;
  occupied_by?: string | null;
}

/** The full editable draft map (BASE terrain — prop footprints are NOT folded into `walkable`). */
export interface DraftMap {
  name: string;
  width: number;
  height: number;
  tileSize: number;
  /** row-major BASE walkable grid (1 = walkable terrain, 0 = blocked). */
  walkable: number[][];
  regions: Record<string, Rect>;
  areas: DraftArea[];
  placedProps: DraftProp[];
  graves: DraftDestination[];
  workstations: DraftDestination[];
  /** directional edge masks — preserved verbatim through edits + round-trips (not edited). */
  directionalCollision: DirCollision[];
}

/** catalog_id -> footprint {w,h} in tiles (derived from the prop catalog). */
export type Footprints = Record<string, { w: number; h: number }>;

/** A structural deep clone of a draft (so edits never alias the live map). */
export function cloneDraft(d: DraftMap): DraftMap {
  return {
    name: d.name,
    width: d.width,
    height: d.height,
    tileSize: d.tileSize,
    walkable: d.walkable.map((row) => row.slice()),
    regions: Object.fromEntries(Object.entries(d.regions).map(([k, r]) => [k, { ...r }])),
    areas: d.areas.map((a) => ({ ...a })),
    placedProps: d.placedProps.map((p) => ({ ...p, tile: { ...p.tile } })),
    graves: d.graves.map((g) => ({ ...g })),
    workstations: d.workstations.map((w) => ({ ...w })),
    directionalCollision: d.directionalCollision.map((c) => ({ ...c, blocked: c.blocked.slice() })),
  };
}

/** The tiles a prop's footprint covers (top-left `tile` + its {w,h}); unknown id → 1×1. */
export function footprintTiles(
  catalogId: string,
  tile: Tile,
  footprints: Footprints,
): Tile[] {
  const fp = footprints[catalogId] ?? { w: 1, h: 1 };
  const out: Tile[] = [];
  for (let dy = 0; dy < fp.h; dy++) {
    for (let dx = 0; dx < fp.w; dx++) out.push({ x: tile.x + dx, y: tile.y + dy });
  }
  return out;
}

/** The union of every placed prop's footprint tiles as a `"x,y"` key set. */
export function occupiedTiles(draft: DraftMap, footprints: Footprints): Set<string> {
  const out = new Set<string>();
  for (const p of draft.placedProps) {
    for (const t of footprintTiles(p.catalogId, p.tile, footprints)) out.add(`${t.x},${t.y}`);
  }
  return out;
}

/**
 * Is a prop of `catalogId` VALID to place at `tile`? (the green/red preview tint.) Every
 * footprint tile must be IN BOUNDS, on BASE-walkable terrain, and not overlap an existing
 * prop's footprint (unless `ignoreIndex` — the prop being moved). Pure; unit-tested.
 */
export function footprintValid(
  draft: DraftMap,
  catalogId: string,
  tile: Tile,
  footprints: Footprints,
  ignoreIndex = -1,
): boolean {
  const occupied = new Set<string>();
  draft.placedProps.forEach((p, i) => {
    if (i === ignoreIndex) return;
    for (const t of footprintTiles(p.catalogId, p.tile, footprints)) occupied.add(`${t.x},${t.y}`);
  });
  for (const t of footprintTiles(catalogId, tile, footprints)) {
    if (t.x < 0 || t.y < 0 || t.x >= draft.width || t.y >= draft.height) return false;
    if (draft.walkable[t.y]?.[t.x] !== 1) return false; // blocked terrain
    if (occupied.has(`${t.x},${t.y}`)) return false; // overlaps another prop
  }
  return true;
}

/** The topmost placed-prop index whose footprint covers `tile` (last-placed wins), or -1. */
export function propIndexAt(draft: DraftMap, tile: Tile, footprints: Footprints): number {
  for (let i = draft.placedProps.length - 1; i >= 0; i--) {
    const p = draft.placedProps[i];
    for (const t of footprintTiles(p.catalogId, p.tile, footprints)) {
      if (t.x === tile.x && t.y === tile.y) return i;
    }
  }
  return -1;
}

// --------------------------------------------------------------------------------------
// Pure edit ops — each returns a NEW DraftMap (the input is never mutated).
// --------------------------------------------------------------------------------------

/** Place a new prop (append). Returns the draft unchanged when the placement is invalid. */
export function placeProp(
  draft: DraftMap,
  catalogId: string,
  tile: Tile,
  footprints: Footprints,
  opts: { orientation?: string; state?: string | null; tint?: number | null } = {},
): DraftMap {
  if (!footprintValid(draft, catalogId, tile, footprints)) return draft;
  const next = cloneDraft(draft);
  next.placedProps.push({
    catalogId,
    tile: { ...tile },
    orientation: opts.orientation ?? "s",
    state: opts.state ?? null,
    tint: opts.tint ?? null,
  });
  return next;
}

/** Delete the prop at `index`. */
export function deleteProp(draft: DraftMap, index: number): DraftMap {
  if (index < 0 || index >= draft.placedProps.length) return draft;
  const next = cloneDraft(draft);
  next.placedProps.splice(index, 1);
  return next;
}

/** Cycle the prop's orientation through the provided list (wrap-around). */
export function rotateProp(draft: DraftMap, index: number, orientations: string[]): DraftMap {
  if (index < 0 || index >= draft.placedProps.length || orientations.length === 0) return draft;
  const next = cloneDraft(draft);
  const cur = next.placedProps[index].orientation;
  const i = orientations.indexOf(cur);
  next.placedProps[index].orientation = orientations[(i + 1 + orientations.length) % orientations.length];
  return next;
}

/** Cycle the prop's on/off (or arbitrary) state through the provided list (wrap-around). */
export function toggleProp(draft: DraftMap, index: number, states: string[]): DraftMap {
  if (index < 0 || index >= draft.placedProps.length || states.length === 0) return draft;
  const next = cloneDraft(draft);
  const cur = next.placedProps[index].state;
  const i = cur === null ? -1 : states.indexOf(cur);
  next.placedProps[index].state = states[(i + 1 + states.length) % states.length];
  return next;
}

/** Cycle the prop's recolor tint through the provided palette (or clear when list empty). */
export function recolorProp(draft: DraftMap, index: number, tints: number[]): DraftMap {
  if (index < 0 || index >= draft.placedProps.length) return draft;
  const next = cloneDraft(draft);
  if (tints.length === 0) {
    next.placedProps[index].tint = null;
    return next;
  }
  const cur = next.placedProps[index].tint;
  const i = cur === null ? -1 : tints.indexOf(cur);
  next.placedProps[index].tint = tints[(i + 1 + tints.length) % tints.length];
  return next;
}

/** Move the prop at `index` to a new tile. Returns unchanged when the move is invalid. */
export function moveProp(
  draft: DraftMap,
  index: number,
  tile: Tile,
  footprints: Footprints,
): DraftMap {
  if (index < 0 || index >= draft.placedProps.length) return draft;
  const p = draft.placedProps[index];
  if (!footprintValid(draft, p.catalogId, tile, footprints, index)) return draft;
  const next = cloneDraft(draft);
  next.placedProps[index].tile = { ...tile };
  return next;
}

/** Paint a plot's SECTION (reassign an area to a different section). */
export function paintPlot(draft: DraftMap, areaId: string, section: string): DraftMap {
  const idx = draft.areas.findIndex((a) => a.id === areaId);
  if (idx < 0 || !section) return draft;
  const next = cloneDraft(draft);
  next.areas[idx].section = section;
  return next;
}

/** Rename a plot (its stable id). No-op on collision / unknown id / empty name. */
export function renamePlot(draft: DraftMap, areaId: string, newId: string): DraftMap {
  const idx = draft.areas.findIndex((a) => a.id === areaId);
  if (idx < 0 || !newId || draft.areas.some((a) => a.id === newId)) return draft;
  const next = cloneDraft(draft);
  next.areas[idx].id = newId;
  return next;
}

/** Move a workstation to a new tile. */
export function moveWorkstation(draft: DraftMap, wsId: string, tile: Tile): DraftMap {
  const idx = draft.workstations.findIndex((w) => w.id === wsId);
  if (idx < 0) return draft;
  const next = cloneDraft(draft);
  next.workstations[idx].x = tile.x;
  next.workstations[idx].y = tile.y;
  return next;
}

/** Move a grave to a new tile. */
export function moveGrave(draft: DraftMap, graveId: string, tile: Tile): DraftMap {
  const idx = draft.graves.findIndex((g) => g.id === graveId);
  if (idx < 0) return draft;
  const next = cloneDraft(draft);
  next.graves[idx].x = tile.x;
  next.graves[idx].y = tile.y;
  return next;
}
