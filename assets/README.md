# ghostopia assets

The world and its ghosts are drawn from data, not baked images. Every sprite is a JSON
pixel-grid using palette keys; the renderer reads the same manifest, so adding art is a data
change rather than a code change.

## What each file is

| File | Purpose |
| --- | --- |
| `sprites.manifest.json` | Catalogue mapping a sprite name to its grid and named frame regions. The atlas builder and renderer both read it. |
| `palettes.json` | The base ghost colour ramp, per-section palettes, and per-status tints. One authored ghost can wear many colour schemes. |
| `animations.json` | Clip table: `clip name → [{ sprite, region, ms }]` with loop and mirror flags. Frames are validated against the manifest when loaded. |
| `ghost/ghost.grids.json` | The ghost sheet — five authored facings with idle and motion frames, plus work/success/error expressions. West facings are mirrored east art. |
| `world/*.grids.json` | Ground tiles, graves, crypt terminals, landmarks, candles and ambient overlays. |
| `overlays/overlays.grids.json` | Small status overlays (pulse, cooldown, retry, success, sleep). |
| `props.catalog.json` | Placeable prop definitions: footprint, orientations and optional on/off states. |
| `screenshots/` | Images used in the project README. |

Grids use single-character palette keys. A space means a transparent pixel. Each grid file
carries a short `_note` describing its contents.

## Rebuild the atlas

```bash
npm run art:atlas -w @ghostopia/ghost-art
```

This writes the packed texture atlas, a recoloured preview image, and the frame table into
the gitignored `packages/ghost-art/dist/` directory. The preview shows every section palette
and status tint at once — open it after changing art to check the result.

## Add a sprite

1. Add a grid to the relevant `*.grids.json` file using the `{ legend, transparent, rows }`
   form.
2. Add a `name → { grid, cellWidth, cellHeight, regions }` entry to `sprites.manifest.json`.
3. Rebuild the atlas. The renderer picks up the new frame from the manifest automatically.

Keep new palettes keyed to the same base ramp (`body`, `bodyShade`, `rim`) so the world stays
visually consistent.
