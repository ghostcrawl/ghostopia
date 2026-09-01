// ghostopia ghost-renderer — typed loader for maps/graveyard.json.
//
// The world model is PYTHON-server-authoritative (emits the map as
// language-neutral DATA at maps/graveyard.json). This loader turns that JSON
// into a typed `WorldMapData` the renderer draws — a DATA import, NOT a Python
// package import. It validates the shape and normalizes `tile_size` -> tileSize.

/** A tile rectangle on the map (contract: Bounds). */
export interface Bounds {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A placed destination (grave / workstation) in TILE coordinates. */
export interface MapDestination {
  id: string;
  type: string;
  x: number;
  y: number;
  region?: string | null;
  section?: string | null;
  occupied_by?: string | null;
}

/**
 * A painted spatial PLOT: a named area rect that maps to a section id. The
 * renderer tints/labels each plot as its section; the in-app editor's area tool will
 * edit this layer. Bounds are TILE coords (a plot IS a region, spatialized to a section).
 */
export interface Area {
  id: string;
  section: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * A placed prop on the map: a catalog prop at a tile, with a
 * facing + optional state. The SERVER owns this layer (its footprint is folded into the
 * walkable grid so A* routes around it); the renderer draws each prop purely from its
 * catalog def + this {tile, orientation, state}. This IS the data model the in-app editor edits.
 */
export interface PlacedProp {
  catalogId: string;
  tile: { x: number; y: number };
  orientation: string;
  state: string | null;
}

/** The typed world map the renderer draws (regions/graves/workstations + grid). */
export interface WorldMapData {
  name: string;
  /** map width in TILES. */
  width: number;
  /** map height in TILES. */
  height: number;
  /** pixel size of one tile. */
  tileSize: number;
  /** region id -> tile bounds (also the section partition). */
  regions: Record<string, Bounds>;
  /** painted section PLOTS — named area rects each mapping to a section. */
  areas: Area[];
  /** server-owned placed-props layer — the decor the renderer draws from the catalog. */
  placedProps: PlacedProp[];
  graves: MapDestination[];
  workstations: MapDestination[];
  /** row-major walkable grid (1 = walkable, 0 = blocked). */
  walkable: number[][];
}

/** The section a painted area maps to (pure lookup; null when the area id is unknown). */
export function sectionForArea(areas: Area[], areaId: string): string | null {
  const a = areas.find((x) => x.id === areaId);
  return a ? a.section : null;
}

/** The painted areas (plots) that map to a section (pure; a section may span multiple plots). */
export function areasForSection(areas: Area[], section: string): Area[] {
  return areas.filter((a) => a.section === section);
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function num(v: unknown, where: string): number {
  if (typeof v !== "number" || !Number.isFinite(v)) {
    throw new Error(`map data: expected a number at ${where}`);
  }
  return v;
}

function asBounds(v: unknown, where: string): Bounds {
  if (!isObject(v)) throw new Error(`map data: invalid bounds at ${where}`);
  return { x: num(v.x, `${where}.x`), y: num(v.y, `${where}.y`), w: num(v.w, `${where}.w`), h: num(v.h, `${where}.h`) };
}

function asDestination(v: unknown, where: string): MapDestination {
  if (!isObject(v)) throw new Error(`map data: invalid destination at ${where}`);
  if (typeof v.id !== "string") throw new Error(`map data: ${where} missing string "id"`);
  if (typeof v.type !== "string") throw new Error(`map data: ${where} missing string "type"`);
  return {
    id: v.id,
    type: v.type,
    x: num(v.x, `${where}.x`),
    y: num(v.y, `${where}.y`),
    region: typeof v.region === "string" ? v.region : null,
    section: typeof v.section === "string" ? v.section : null,
    occupied_by: typeof v.occupied_by === "string" ? v.occupied_by : null,
  };
}

/**
 * Parse + validate `maps/graveyard.json` into a typed `WorldMapData`. Throws
 * loudly on a malformed map so a bad data drop never renders an empty world.
 */
export function loadMapData(json: unknown): WorldMapData {
  if (!isObject(json)) throw new Error("map data: expected an object");
  if (typeof json.name !== "string") throw new Error('map data: missing string "name"');

  const width = num(json.width, "width");
  const height = num(json.height, "height");
  const tileSize = num(json.tile_size, "tile_size");

  if (!Array.isArray(json.walkable)) throw new Error('map data: missing "walkable" grid');
  const walkable = json.walkable.map((row, r) => {
    if (!Array.isArray(row)) throw new Error(`map data: walkable row ${r} is not an array`);
    return row.map((cell, c) => num(cell, `walkable[${r}][${c}]`));
  });

  const regions: Record<string, Bounds> = {};
  if (isObject(json.regions)) {
    for (const [name, b] of Object.entries(json.regions)) {
      regions[name] = asBounds(b, `regions.${name}`);
    }
  }

  // painted section plots. Optional: a map without an `areas` layer falls back
  // to one plot per region (each region IS a section partition), so older maps still render.
  const areas: Area[] = [];
  if (Array.isArray(json.areas)) {
    for (const [i, raw] of json.areas.entries()) {
      if (!isObject(raw)) throw new Error(`map data: invalid area at areas[${i}]`);
      if (typeof raw.id !== "string") throw new Error(`map data: areas[${i}] missing "id"`);
      if (typeof raw.section !== "string") throw new Error(`map data: areas[${i}] missing "section"`);
      const b = asBounds(raw, `areas[${i}]`);
      areas.push({ id: raw.id, section: raw.section, x: b.x, y: b.y, w: b.w, h: b.h });
    }
  } else {
    for (const [id, b] of Object.entries(regions)) {
      areas.push({ id, section: id, x: b.x, y: b.y, w: b.w, h: b.h });
    }
  }

  const dests = isObject(json.destinations) ? json.destinations : {};
  const rawGraves = Array.isArray((dests as Record<string, unknown>).graves)
    ? ((dests as Record<string, unknown>).graves as unknown[])
    : [];
  const rawWorkstations = Array.isArray((dests as Record<string, unknown>).workstations)
    ? ((dests as Record<string, unknown>).workstations as unknown[])
    : [];

  const graves = rawGraves.map((g, i) => asDestination(g, `graves[${i}]`));
  const workstations = rawWorkstations.map((w, i) => asDestination(w, `workstations[${i}]`));

  // placed-props layer. Optional: a map without it renders no catalog decor.
  const placedProps: PlacedProp[] = [];
  if (Array.isArray(json.placed_props)) {
    for (const [i, raw] of json.placed_props.entries()) {
      if (!isObject(raw)) throw new Error(`map data: invalid placed prop at placed_props[${i}]`);
      if (typeof raw.catalog_id !== "string") {
        throw new Error(`map data: placed_props[${i}] missing "catalog_id"`);
      }
      if (!Array.isArray(raw.tile) || raw.tile.length !== 2) {
        throw new Error(`map data: placed_props[${i}] "tile" must be [x, y]`);
      }
      placedProps.push({
        catalogId: raw.catalog_id,
        tile: { x: num(raw.tile[0], `placed_props[${i}].tile.x`), y: num(raw.tile[1], `placed_props[${i}].tile.y`) },
        orientation: typeof raw.orientation === "string" ? raw.orientation : "s",
        state: typeof raw.state === "string" ? raw.state : null,
      });
    }
  }

  return { name: json.name, width, height, tileSize, regions, areas, placedProps, graves, workstations, walkable };
}
