// ghostopia web — load the language-neutral world DATA + the art atlas.
//
// A THIN renderer boundary: this reads maps/graveyard.json (emitted by the
// Python world) + the @ghostopia/ghost-art asset set (grids/manifest/
// palettes/animations) and builds the PixiJS-consumable atlas + clip
// book. It NEVER imports the GhostCrawl SDK, a Python package, or any key — the
// data arrives as JSON fetched from bundled asset URLs.

import {
  buildAtlas,
  expandGrid,
  loadAnimations,
  loadManifest,
  loadPalettes,
  loadPropCatalog,
  type AnimationBook,
  type Atlas,
  type PaletteBook,
  type PropCatalog,
  type SpriteGrid,
} from "@ghostopia/ghost-art";
import {
  loadMapData,
  DEFAULT_WORLD_THEME,
  type SectionTintMap,
  type WorldMapData,
  type WorldTheme,
} from "@ghostopia/ghost-renderer";

// Bundled asset URLs (Vite copies these into the build; no SDK/Python import).
const gridUrls = [
  new URL("../../../../assets/ghost/ghost.grids.json", import.meta.url),
  new URL("../../../../assets/world/tiles.grids.json", import.meta.url),
  new URL("../../../../assets/world/landmarks.grids.json", import.meta.url),
  new URL("../../../../assets/world/sky.grids.json", import.meta.url),
  new URL("../../../../assets/world/critters.grids.json", import.meta.url),
  new URL("../../../../assets/world/props.grids.json", import.meta.url),
  new URL("../../../../assets/overlays/overlays.grids.json", import.meta.url),
];
const manifestUrl = new URL("../../../../assets/sprites.manifest.json", import.meta.url);
const palettesUrl = new URL("../../../../assets/palettes.json", import.meta.url);
const animationsUrl = new URL("../../../../assets/animations.json", import.meta.url);
const propCatalogUrl = new URL("../../../../assets/props.catalog.json", import.meta.url);
const mapUrl = new URL("../../../../maps/graveyard.json", import.meta.url);

async function fetchJson(url: URL): Promise<unknown> {
  const res = await fetch(url.href);
  if (!res.ok) throw new Error(`ghostopia: failed to load ${url.href} (${res.status})`);
  return res.json();
}

/** #rrggbb / #rgb -> 0xRRGGBB number (drops alpha). */
function hexToNum(hex: string): number {
  let h = hex.replace("#", "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  return parseInt(h.slice(0, 6), 16);
}

// Map the real-WORK graveyard regions to their section palette (for the overlay tint).
//
// The "home" concept is GONE and the crypt is unlabeled scenery, so
// neither the crypt NOR the ghost-graves region maps to a section palette any more (the crypt
// tint is decoupled from the removed "home"). Named zones (and their tints) are ONLY the real
// GhostCrawl-work regions; graves scatter as placeable spawn points with no designated home.
// Region id -> palette section name. The shipped map's regions ARE the real section ids
// (195), so research/extraction/verify/error self-resolve to their palettes directly; this
// maps the remaining zones (resting, canvas, and the four flagship departments) onto a
// palette so every zone — including the departments — gets a coloured ground wash + label.
const REGION_TO_SECTION: Record<string, string> = {
  resting: "home",
  canvas: "verify",
  "horror-books": "extraction",
  "mystery-books": "extraction",
  "spooky-masks": "research",
  "spooky-costumes": "research",
  // legacy aliases (kept so an older persisted/edited map still tints correctly)
  "central-graveyard": "research",
  "computer-graveyard": "extraction",
  "error-graveyard": "error",
  "data-graveyard": "verify",
};

export interface WorldAssets {
  atlas: Atlas;
  book: AnimationBook;
  mapData: WorldMapData;
  /** resolved palette book (base ramp + section palettes) — drives ghost recolor. */
  paletteBook: PaletteBook;
  /** the placeable-prop catalog — the renderer draws every placed prop from it. */
  catalog: PropCatalog;
  /** section-name AND region-id -> base tint colour (0xRRGGBB). */
  sectionTints: SectionTintMap;
  /** default ghost tint (the base body ramp). */
  defaultTint: number;
  /** night presentation theme (ground/vignette/backdrop tints) from palettes.json `world`. */
  theme: WorldTheme;
}

/** Build the night {@link WorldTheme} from the palettes.json `world` block (defaults fill gaps). */
function themeFromPalettes(raw: unknown): WorldTheme {
  const w = (raw as { world?: Record<string, unknown> }).world ?? {};
  const hx = (key: keyof WorldTheme, fallback: number): number => {
    const v = w[key];
    return typeof v === "string" ? hexToNum(v) : fallback;
  };
  const numOr = (key: keyof WorldTheme, fallback: number): number => {
    const v = w[key];
    return typeof v === "number" ? v : fallback;
  };
  return {
    background: hx("background", DEFAULT_WORLD_THEME.background),
    groundTint: hx("groundTint", DEFAULT_WORLD_THEME.groundTint),
    vignette: hx("vignette", DEFAULT_WORLD_THEME.vignette),
    vignetteAlpha: numOr("vignetteAlpha", DEFAULT_WORLD_THEME.vignetteAlpha),
    washAlpha: numOr("washAlpha", DEFAULT_WORLD_THEME.washAlpha),
    moon: hx("moon", DEFAULT_WORLD_THEME.moon),
    moonGlow: hx("moonGlow", DEFAULT_WORLD_THEME.moonGlow),
    star: hx("star", DEFAULT_WORLD_THEME.star),
  };
}

/** Load + assemble everything the render loop needs from real data + art. */
export async function loadWorldAssets(): Promise<WorldAssets> {
  const [gridDocs, manifestRaw, palettesRaw, animationsRaw, propCatalogRaw, mapRaw] =
    await Promise.all([
      Promise.all(gridUrls.map(fetchJson)),
      fetchJson(manifestUrl),
      fetchJson(palettesUrl),
      fetchJson(animationsUrl),
      fetchJson(propCatalogUrl),
      fetchJson(mapUrl),
    ]);

  // expand the compact JSON grids into canonical SpriteGrids
  const grids: Record<string, SpriteGrid> = {};
  for (const doc of gridDocs as Array<{ grids: Record<string, unknown> }>) {
    for (const [name, compact] of Object.entries(doc.grids)) {
      grids[name] = expandGrid(compact as never);
    }
  }

  const manifest = loadManifest(manifestRaw, Object.keys(grids));
  const palettes = loadPalettes(palettesRaw);
  const book = loadAnimations(animationsRaw, manifest); // throws if any clip frame is missing
  // the placeable-prop catalog — throws if any prop's grid/region/clip ref is missing.
  const catalog = loadPropCatalog(propCatalogRaw, manifest, book);
  const atlas = buildAtlas(manifest, grids);
  const mapData = loadMapData(mapRaw);

  // section tints: keyed by section name (ghost tint) + region id (overlay tint)
  const rawSections = (palettesRaw as { sections?: Record<string, { body?: string }> }).sections ?? {};
  const sectionTints: SectionTintMap = {};
  for (const [name, ramp] of Object.entries(rawSections)) {
    if (ramp?.body) sectionTints[name] = hexToNum(ramp.body);
  }
  for (const [regionId, section] of Object.entries(REGION_TO_SECTION)) {
    if (sectionTints[section] !== undefined) sectionTints[regionId] = sectionTints[section];
  }

  const base = (palettesRaw as { base?: { body?: string } }).base?.body ?? "#e8ecff";
  const defaultTint = hexToNum(base);
  const theme = themeFromPalettes(palettesRaw);

  return { atlas, book, mapData, paletteBook: palettes, catalog, sectionTints, defaultTint, theme };
}
