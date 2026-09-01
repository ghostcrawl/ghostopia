// ghostopia ghost-renderer — the Graveyard Builder canvas overlay.
//
// A PixiJS overlay the render loop draws ON TOP of the world when the operator is in Editor
// mode: a tile GRID, the held prop's ghost-PREVIEW (green=valid / red=invalid via the
// footprint/collision check done in the editor store), the current draft's placed-prop
// FOOTPRINTS (so edits are visible immediately, before a save re-bakes the sprites), the
// section PLOTS (paint overlay), the graves/workstations markers, and the SELECTION highlight.
//
// The view is a plain data shape the app computes from the editor draft (no coupling to the
// apps/web editor types), so this stays a pure renderer concern. `tileFromWorld` is pure +
// unit-tested. Imports only PixiJS — NO SDK, NO Python, NO key.

import { Container, Graphics, Text } from "pixi.js";

/** A footprint box in tiles. */
export interface OverlayFootprint {
  w: number;
  h: number;
}

/** A draft placed-prop the overlay draws as a translucent footprint. */
export interface OverlayProp {
  catalogId: string;
  tile: { x: number; y: number };
  footprint: OverlayFootprint;
  tint: number | null;
}

/** A draft section PLOT (area) the overlay tints/labels. */
export interface OverlayArea {
  id: string;
  section: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A draft destination (grave / workstation) marker. */
export interface OverlayDest {
  id: string;
  kind: "grave" | "workstation";
  x: number;
  y: number;
}

/** The held prop's placement preview (valid=green / invalid=red). */
export interface OverlayPreview {
  tile: { x: number; y: number };
  footprint: OverlayFootprint;
  valid: boolean;
}

/** A selection highlight target (a footprint box in tiles). */
export interface OverlaySelection {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Everything the overlay needs for a frame (computed by the app from the editor draft). */
export interface EditorOverlayView {
  active: boolean;
  width: number;
  height: number;
  tileSize: number;
  /** bumps whenever the static content (props/areas/dests) changes → rebuild the static layer. */
  revision: number;
  props: OverlayProp[];
  areas: OverlayArea[];
  dests: OverlayDest[];
  preview: OverlayPreview | null;
  selection: OverlaySelection | null;
  /** section name -> tint colour (0xRRGGBB) for plot fills. */
  sectionTints: Record<string, number>;
}

/** PURE: world-pixel point → the tile it falls in (floor divide by tile size). */
export function tileFromWorld(
  worldX: number,
  worldY: number,
  tileSize: number,
): { x: number; y: number } {
  return { x: Math.floor(worldX / tileSize), y: Math.floor(worldY / tileSize) };
}

/**
 * The editor overlay: a Container of a static grid + a static content layer (props/plots/
 * destinations rebuilt on `revision` change) + a dynamic layer (preview + selection redrawn
 * every frame). `update(view)` is called each tick; it hides itself when `view.active` is false.
 */
export class EditorOverlay {
  readonly container: Container;
  private readonly grid: Graphics;
  private readonly staticLayer: Container;
  private readonly staticG: Graphics;
  private readonly dyn: Graphics;
  private lastRevision = -1;
  private lastGridKey = "";

  constructor() {
    this.container = new Container();
    this.container.label = "editorOverlay";
    this.container.eventMode = "none";
    this.container.visible = false;
    this.grid = new Graphics();
    this.staticLayer = new Container();
    this.staticG = new Graphics();
    this.staticLayer.addChild(this.staticG);
    this.dyn = new Graphics();
    this.container.addChild(this.grid);
    this.container.addChild(this.staticLayer);
    this.container.addChild(this.dyn);
  }

  update(view: EditorOverlayView): void {
    this.container.visible = view.active;
    if (!view.active) return;
    const ts = view.tileSize;

    // ---- 1. grid (rebuild only when the map dimensions change) ----
    const gridKey = `${view.width}x${view.height}x${ts}`;
    if (gridKey !== this.lastGridKey) {
      this.lastGridKey = gridKey;
      this.grid.clear();
      for (let x = 0; x <= view.width; x++) {
        this.grid.moveTo(x * ts, 0).lineTo(x * ts, view.height * ts);
      }
      for (let y = 0; y <= view.height; y++) {
        this.grid.moveTo(0, y * ts).lineTo(view.width * ts, y * ts);
      }
      this.grid.stroke({ color: 0x8fb0ff, alpha: 0.16, width: 1 });
    }

    // ---- 2. static content: plots, prop footprints, destination markers ----
    if (view.revision !== this.lastRevision) {
      this.lastRevision = view.revision;
      this.staticG.clear();
      // clear old plot labels (children beyond the staticG at index 0).
      while (this.staticLayer.children.length > 1) {
        const c = this.staticLayer.children[this.staticLayer.children.length - 1];
        this.staticLayer.removeChild(c);
        c.destroy();
      }
      // plots (areas): a tinted fill + a dashed-ish border + a section label.
      for (const a of view.areas) {
        const color = view.sectionTints[a.section] ?? 0x8a80c0;
        this.staticG
          .rect(a.x * ts, a.y * ts, a.w * ts, a.h * ts)
          .fill({ color, alpha: 0.08 })
          .stroke({ color, alpha: 0.4, width: 1 });
        const label = new Text({
          text: `${a.section}`,
          style: { fontFamily: "ui-monospace, monospace", fontSize: 7, fontWeight: "700", fill: color },
        });
        label.resolution = 3;
        label.alpha = 0.85;
        label.position.set(a.x * ts + 3, a.y * ts + 2);
        this.staticLayer.addChild(label);
      }
      // prop footprints (translucent so the operator sees the draft layout under construction).
      for (const p of view.props) {
        const color = p.tint ?? 0xbcd0ff;
        this.staticG
          .rect(p.tile.x * ts + 1, p.tile.y * ts + 1, p.footprint.w * ts - 2, p.footprint.h * ts - 2)
          .fill({ color, alpha: 0.32 })
          .stroke({ color, alpha: 0.7, width: 1 });
      }
      // destination markers (grave = amber diamond, workstation = cyan square).
      for (const d of view.dests) {
        const cx = d.x * ts + ts / 2;
        const cy = d.y * ts + ts / 2;
        if (d.kind === "grave") {
          this.staticG
            .poly([cx, cy - ts * 0.4, cx + ts * 0.4, cy, cx, cy + ts * 0.4, cx - ts * 0.4, cy])
            .fill({ color: 0xffb347, alpha: 0.5 })
            .stroke({ color: 0xffb347, alpha: 0.9, width: 1 });
        } else {
          this.staticG
            .rect(cx - ts * 0.35, cy - ts * 0.35, ts * 0.7, ts * 0.7)
            .fill({ color: 0x8ff0ff, alpha: 0.45 })
            .stroke({ color: 0x8ff0ff, alpha: 0.9, width: 1 });
        }
      }
    }

    // ---- 3. dynamic layer: the held preview + the selection highlight (every frame) ----
    this.dyn.clear();
    if (view.preview) {
      const { tile, footprint, valid } = view.preview;
      const color = valid ? 0x6be08a : 0xe0574a;
      this.dyn
        .rect(tile.x * ts, tile.y * ts, footprint.w * ts, footprint.h * ts)
        .fill({ color, alpha: 0.28 })
        .stroke({ color, alpha: 0.95, width: 2 });
    }
    if (view.selection) {
      const s = view.selection;
      this.dyn
        .rect(s.x * ts - 1, s.y * ts - 1, s.w * ts + 2, s.h * ts + 2)
        .stroke({ color: 0xffffff, alpha: 0.95, width: 2 });
    }
  }

  destroy(): void {
    this.container.destroy({ children: true });
  }
}
