# ghostopia assets — original procedural pixel-art pipeline

These assets are the STAGE-0 art foundation for ghostopia's graveyard world. They are
**language-neutral data** (JSON pixel-grids + a manifest + palettes) consumed by the
frontend TS runtime `@ghostopia/ghost-art`. The renderer reads the same manifest
as data — so **adding art is a data drop, never a renderer code change**.

## Originality

**Every grid here is authored by hand from scratch.** No sprite, tileset, map, character,
palette, or `.aito`/asset file from any reference repository was traced, copied, or reused.
The *technique* (JSON pixel-grid + palette recolor + 1px outline + a data-driven manifest →
atlas) is an original re-implementation of a well-known procedural approach; the *pixels and
palette* are ghostopia's own. Each `*.grids.json` carries an `_note` asserting this.

## The pipeline (grid → manifest → atlas → render)

```
author a JSON pixel-grid   ─┐
  (palette KEYS, '' = clear) │   loadManifest(sprites.manifest.json)
add a manifest entry         ├─▶  + expandGrid(*.grids.json)  ─▶  buildAtlas(...)
  (name → grid → regions)    │        └ resolveGrid + outline       └ PixiJS-consumable
declare palettes/tints      ─┘   loadPalettes(palettes.json)           atlas + frame table
                                    └ recolor / applyStatusTint
```

1. **Author a grid.** A sprite is a grid of **palette KEYS** (single chars). `' '` (the
   `transparent` char) is a clear pixel — never a silent black. Grids live in `*.grids.json`
   under a `grids` map in the compact `{ legend, transparent, rows }` authoring form
   (`expandGrid` turns it into the canonical `string[][]` model; `resolveGrid` rasterizes it).
2. **Catalog it in `sprites.manifest.json`.** Map a sprite `name → { grid, cellWidth,
   cellHeight, regions }`. A `SpriteRegion {col,row,cols,rows}` addresses a frame in CELL
   units. The manifest is the **single source** the atlas builder reads.
3. **Palette-recolor per section/status.** `palettes.json` holds ONE base ghost ramp, a
   per-section palette (mirroring the base keys), and per-status tints. At runtime `recolor`
   swaps the base ramp for a section ramp (same ghost → cool-blue research vs amber
   extraction) and `applyStatusTint` multiplies a status tint (error = dark/desaturated,
   success = bright). **One authored ghost family → N looks by data.**
4. **Pack + render.** `buildAtlas` resolves every grid (with a 1px outline pass), packs them
   into one RGBA atlas, and emits a `"<sprite>:<region>"` frame table. The renderer uploads it
   as a texture with `imageSmoothingEnabled = false` / nearest scaling for crisp pixels.

## Dynamic layer — the ghost TURNS + MOVES (animations.json + facing.ts)

The ghost is not a still. It **turns 8 ways** and every state has **motion**.

- **8-way turning ("the 360").** Only FIVE facings are hand-authored — `s`, `se`, `e`, `ne`,
  `n`. The west set (`sw`, `w`, `nw`) is **never authored**: it is the east art (`se`/`e`/`ne`)
  drawn **mirrored** (`ctx.scale(-1, 1)`). `facingFromVector(dx, dy)` maps a movement vector
  (screen space, `+dy` = down = south) to `{ facing, mirror }` — the renderer turns the ghost
  to its A* heading deterministically.
- **Per-state motion (DATA, not a wall clock).** Every facing carries a **4-frame idle float**
  (bob + wisp sway + a blink) and a **4-frame move cycle**. The front facing keeps the
  `work` / `success` / `error` expression frames. Timing lives in `animations.json` as per-frame
  `ms` — the art package hard-codes nothing to a clock.
- **`animations.json` is the SINGLE clip contract the renderer plays.** `clip name → ordered
  [{ sprite, region, ms }] + { loop, mirror? }`. `loadAnimations(json, manifest)` **validates
  every frame's `(sprite, region)` against the manifest** — a missing ref **throws**, so a typo
  can never ship a blank frame. Clips: `idle.<facing>` / `move.<facing>` (all 8, west = mirror),
  `work` / `success` / `error`, the living overlays `ov.*`, and the ambient world flickers
  `world.terminal` / `world.candle`.
- **Region naming.** Ghost regions are `<facing>_<anim><frame>` (`s_idle0`, `se_move3`,
  `n_idle1`) + the front `work0..2` / `success0..1` / `error0..1`. Overlays and the crypt
  terminal / grave candle carry their flicker frames.

The ghost sheet + living overlays are emitted by the ORIGINAL authoring scripts
`packages/ghost-art/scripts/author_ghost.mjs` and `author_overlays.mjs` — the **rest
silhouettes are hand-authored keyframes**; the in-between motion (bob / sway / blink) is a
deterministic transform. The renderer contract lives in `docs/ART_RUNTIME_CONTRACT.md`.

## Build it

```bash
cd ghostopia
npm run art:atlas -w @ghostopia/ghost-art
```

Writes (into the gitignored `packages/ghost-art/dist/`):

- `atlas.png` — the packed texture atlas.
- `preview.png` — the ONE ghost recolored across every section palette × status tints at 4×
  nearest-neighbour, plus a strip of the graveyard tileset / terminals / landmarks / overlays.
  **Open this for the STAGE-0 vibe-check.**
- `atlas.frames.json` — `{ width, height, frames }` the renderer consumes.

## Asset set

| File | Grids | What |
|---|---|---|
| `ghost/ghost.grids.json` | `ghost` (one 12×14 sheet) | ONE cute-spooky ghost, now DIRECTIONAL + ANIMATED: 5 authored facings (`s se e ne n`) × (idle0..3 + move0..3) + front `work`/`success`/`error` frames. Rows = facings, cols = frames. West set is the east art mirrored. |
| `world/tiles.grids.json` | `grass` `path` `dirt` `grave` `terminal` | Ground autotile (16×16), the ghost home grave (16×16), and the crypt terminal / workstation (16×24, idle + active + active1 — a screen scanline flicker when a ghost works). |
| `world/landmarks.grids.json` | `crypt` `mausoleum` `fog` `candle` | Section identity anchors (24×24), a tileable additive fog overlay (16×16), + a two-frame grave candle (8×8, `candle0/1` flame flicker). |
| `overlays/overlays.grids.json` | `overlays` (5-icon sheet, 2 frames each) | Living status overlays (13px): captcha pulse, cooldown sand-tick, retry quarter-turn, success pop, zzz drift. |
| `animations.json` | — | The clip table the renderer plays: `clip → [{sprite,region,ms}] + {loop,mirror?}`. Validated against the manifest by `loadAnimations`. |
| `sprites.manifest.json` | — | The data-driven catalogue (name → grid → region frame table). |
| `palettes.json` | — | ONE base ramp + per-section palettes + per-status tints. |

## Adding a sprite (no renderer change)

1. Add a grid to a `*.grids.json` `grids` map (or a new file wired into `build_atlas.mjs`'s
   `gridFiles`), authored in the `{ legend, transparent, rows }` form.
2. Add a `sprites.manifest.json` entry (`name → grid → regions`).
3. Rebuild the atlas. The renderer picks it up because it reads the manifest as **data** —
   the same principle as the behavior registry (no code edit to add content).

## Palette convention

The base ghost ramp is a cohesive cute-spooky set — desaturated indigo/teal night base,
off-white body with a cyan rim, dark eyes, warm blush. Every section palette **hue-shifts off
this ONE base** so the world stays unified; keep new sections keyed to the same base ramp keys
(`body` / `bodyShade` / `rim`).
