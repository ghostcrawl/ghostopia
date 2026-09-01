// ghostopia web — Graveyard Builder map import/export + wire conversion.
//
// The editor's DRAFT map (a client-side, camelCase shape) converts to/from the SERVER wire
// shape (`EditableMap` — snake_case, the exact JSON `map.save` sends + `world.snapshot`
// carries). This module owns that conversion PLUS export→JSON blob (download) and import→
// parse+client-validate (upload), which round-trip through the SAME wire shape so a shared
// graveyard file is portable. Client validation here is structural only — the SERVER remains
// authoritative (catalog allowlist + reachability). No PixiJS / SDK / key.

import type { WorldMapData } from "@ghostopia/ghost-renderer";

import type { DraftDestination, DraftMap, DraftProp } from "./tools.js";

/** The server wire shape (mirrors `ghostopia_shared.EditableMap.model_dump()`). */
export interface WireMap {
  name: string;
  width: number;
  height: number;
  tile_size: number;
  walkable: number[][];
  regions: Record<string, { x: number; y: number; w: number; h: number }>;
  areas: Array<{ id: string; section: string; x: number; y: number; w: number; h: number }>;
  placed_props: Array<{
    catalog_id: string;
    tile: [number, number];
    orientation: string;
    state: string | null;
    tint: number | null;
  }>;
  graves: Array<{ id: string; type: string; x: number; y: number; region: string | null }>;
  workstations: Array<{
    id: string;
    type: string;
    x: number;
    y: number;
    section: string | null;
    occupied_by: string | null;
  }>;
  directional_collision: Array<{ x: number; y: number; blocked: string[] }>;
}

/** Convert an editor DRAFT into the server wire shape (`map.save` payload). */
export function draftToWire(d: DraftMap): WireMap {
  return {
    name: d.name,
    width: d.width,
    height: d.height,
    tile_size: d.tileSize,
    walkable: d.walkable.map((row) => row.slice()),
    regions: Object.fromEntries(Object.entries(d.regions).map(([k, r]) => [k, { ...r }])),
    areas: d.areas.map((a) => ({ id: a.id, section: a.section, x: a.x, y: a.y, w: a.w, h: a.h })),
    placed_props: d.placedProps.map((p) => ({
      catalog_id: p.catalogId,
      tile: [p.tile.x, p.tile.y],
      orientation: p.orientation,
      state: p.state,
      tint: p.tint,
    })),
    graves: d.graves.map((g) => ({
      id: g.id,
      type: g.type,
      x: g.x,
      y: g.y,
      region: g.region ?? null,
    })),
    workstations: d.workstations.map((w) => ({
      id: w.id,
      type: w.type,
      x: w.x,
      y: w.y,
      section: w.section ?? null,
      occupied_by: w.occupied_by ?? null,
    })),
    directional_collision: d.directionalCollision.map((c) => ({
      x: c.x,
      y: c.y,
      blocked: c.blocked.slice(),
    })),
  };
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function num(v: unknown, where: string): number {
  if (typeof v !== "number" || !Number.isFinite(v)) throw new Error(`map: expected number at ${where}`);
  return v;
}

/** Convert a server wire map (from `world.snapshot` or an imported file) into an editor DRAFT. */
export function wireToDraft(w: unknown): DraftMap {
  if (!isObject(w)) throw new Error("map: expected an object");
  const width = num(w.width, "width");
  const height = num(w.height, "height");
  if (!Array.isArray(w.walkable) || w.walkable.length !== height) {
    throw new Error("map: walkable grid does not match height");
  }
  const walkable = (w.walkable as unknown[]).map((row, r) => {
    if (!Array.isArray(row) || row.length !== width) {
      throw new Error(`map: walkable row ${r} does not match width`);
    }
    return (row as unknown[]).map((c, ci) => {
      const n = num(c, `walkable[${r}][${ci}]`);
      if (n !== 0 && n !== 1) throw new Error(`map: walkable[${r}][${ci}] must be 0 or 1`);
      return n;
    });
  });

  const regions: Record<string, { x: number; y: number; w: number; h: number }> = {};
  if (isObject(w.regions)) {
    for (const [k, r] of Object.entries(w.regions)) {
      if (!isObject(r)) throw new Error(`map: invalid region ${k}`);
      regions[k] = { x: num(r.x, `regions.${k}.x`), y: num(r.y, `regions.${k}.y`), w: num(r.w, `regions.${k}.w`), h: num(r.h, `regions.${k}.h`) };
    }
  }

  const areas: DraftMap["areas"] = [];
  if (Array.isArray(w.areas)) {
    for (const [i, raw] of (w.areas as unknown[]).entries()) {
      if (!isObject(raw) || typeof raw.id !== "string" || typeof raw.section !== "string") {
        throw new Error(`map: invalid area at areas[${i}]`);
      }
      areas.push({ id: raw.id, section: raw.section, x: num(raw.x, `areas[${i}].x`), y: num(raw.y, `areas[${i}].y`), w: num(raw.w, `areas[${i}].w`), h: num(raw.h, `areas[${i}].h`) });
    }
  }

  const placedProps: DraftProp[] = [];
  if (Array.isArray(w.placed_props)) {
    for (const [i, raw] of (w.placed_props as unknown[]).entries()) {
      if (!isObject(raw) || typeof raw.catalog_id !== "string") {
        throw new Error(`map: invalid prop at placed_props[${i}]`);
      }
      if (!Array.isArray(raw.tile) || raw.tile.length !== 2) {
        throw new Error(`map: placed_props[${i}].tile must be [x, y]`);
      }
      placedProps.push({
        catalogId: raw.catalog_id,
        tile: { x: num(raw.tile[0], `placed_props[${i}].tile.x`), y: num(raw.tile[1], `placed_props[${i}].tile.y`) },
        orientation: typeof raw.orientation === "string" ? raw.orientation : "s",
        state: typeof raw.state === "string" ? raw.state : null,
        tint: typeof raw.tint === "number" ? raw.tint : null,
      });
    }
  }

  const dest = (list: unknown, kind: "grave" | "workstation"): DraftDestination[] => {
    const out: DraftDestination[] = [];
    if (Array.isArray(list)) {
      for (const [i, raw] of (list as unknown[]).entries()) {
        if (!isObject(raw) || typeof raw.id !== "string") throw new Error(`map: invalid ${kind}[${i}]`);
        out.push({
          id: raw.id,
          type: typeof raw.type === "string" ? raw.type : kind,
          x: num(raw.x, `${kind}[${i}].x`),
          y: num(raw.y, `${kind}[${i}].y`),
          region: typeof raw.region === "string" ? raw.region : null,
          section: typeof raw.section === "string" ? raw.section : null,
          occupied_by: typeof raw.occupied_by === "string" ? raw.occupied_by : null,
        });
      }
    }
    return out;
  };

  const dirColl: DraftMap["directionalCollision"] = [];
  if (Array.isArray(w.directional_collision)) {
    for (const raw of w.directional_collision as unknown[]) {
      if (isObject(raw) && typeof raw.x === "number" && typeof raw.y === "number") {
        const blocked = Array.isArray(raw.blocked) ? (raw.blocked as unknown[]).filter((s): s is string => typeof s === "string") : [];
        dirColl.push({ x: raw.x, y: raw.y, blocked });
      }
    }
  }

  return {
    name: typeof w.name === "string" ? w.name : "graveyard",
    width,
    height,
    tileSize: typeof w.tile_size === "number" ? w.tile_size : 16,
    walkable,
    regions,
    areas,
    placedProps,
    graves: dest(w.graves, "grave"),
    workstations: dest(w.workstations, "workstation"),
    directionalCollision: dirColl,
  };
}

/**
 * Build an editor DRAFT from the renderer's loaded `WorldMapData` (the enter-editor path when
 * no server snapshot has arrived yet). The renderer map already carries base terrain + the
 * areas/props/destinations layers; directional collision is not in the renderer shape, so it
 * defaults to empty (a fresh save recomputes collision anyway).
 */
export function draftFromMapData(m: WorldMapData): DraftMap {
  return {
    name: m.name,
    width: m.width,
    height: m.height,
    tileSize: m.tileSize,
    walkable: m.walkable.map((row) => row.slice()),
    regions: Object.fromEntries(Object.entries(m.regions).map(([k, r]) => [k, { ...r }])),
    areas: m.areas.map((a) => ({ id: a.id, section: a.section, x: a.x, y: a.y, w: a.w, h: a.h })),
    placedProps: m.placedProps.map((p) => ({
      catalogId: p.catalogId,
      tile: { x: p.tile.x, y: p.tile.y },
      orientation: p.orientation,
      state: p.state,
      tint: null,
    })),
    graves: m.graves.map((g) => ({ id: g.id, type: g.type, x: g.x, y: g.y, region: g.region ?? null })),
    workstations: m.workstations.map((w) => ({
      id: w.id,
      type: w.type,
      x: w.x,
      y: w.y,
      section: w.section ?? null,
      occupied_by: w.occupied_by ?? null,
    })),
    directionalCollision: [],
  };
}

/**
 * Convert an editor DRAFT into the renderer's `WorldMapData` (the reverse of
 * `draftFromMapData`). Used to apply a server `world.snapshot` to the live render loop: the
 * snapshot is the wire shape → `wireToDraft` → this → the render loop redraws the new world.
 */
export function draftToMapData(d: DraftMap): WorldMapData {
  return {
    name: d.name,
    width: d.width,
    height: d.height,
    tileSize: d.tileSize,
    regions: Object.fromEntries(Object.entries(d.regions).map(([k, r]) => [k, { ...r }])),
    areas: d.areas.map((a) => ({ id: a.id, section: a.section, x: a.x, y: a.y, w: a.w, h: a.h })),
    placedProps: d.placedProps.map((p) => ({
      catalogId: p.catalogId,
      tile: { x: p.tile.x, y: p.tile.y },
      orientation: p.orientation,
      state: p.state,
    })),
    graves: d.graves.map((g) => ({ id: g.id, type: g.type, x: g.x, y: g.y, region: g.region ?? null, section: null, occupied_by: null })),
    workstations: d.workstations.map((w) => ({ id: w.id, type: w.type, x: w.x, y: w.y, region: null, section: w.section ?? null, occupied_by: w.occupied_by ?? null })),
    walkable: d.walkable.map((row) => row.slice()),
  };
}

/** Serialize a draft to a pretty JSON string (the download payload). */
export function exportMapJson(d: DraftMap): string {
  return JSON.stringify(draftToWire(d), null, 2);
}

/** Parse + client-validate an uploaded JSON string into a draft (throws on a malformed file). */
export function importMapJson(text: string): DraftMap {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    throw new Error(`map: not valid JSON (${err instanceof Error ? err.message : String(err)})`);
  }
  return wireToDraft(parsed);
}
