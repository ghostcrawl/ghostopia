// ghostopia web — the Graveyard Builder store (draft + tools + history + selection).
//
// A vanilla Zustand store (readable OUTSIDE React by the PixiJS editor overlay via getState(),
// subscribed by the React toolbar via useStore). It holds the DRAFT map (cloned from the live
// world on enter), the active tool + selection + held-prop, and a bounded undo/redo History.
// EVERY edit routes through a pure `tools` op and is pushed to history. NOTHING here mutates
// the live world — only a validated `map.save` (wired in App/liveClient) swaps it. No SDK/key.

import { createStore } from "zustand/vanilla";
import type { PropCatalog } from "@ghostopia/ghost-art";
import type { EditorOverlayView } from "@ghostopia/ghost-renderer";

import { History } from "./history.js";
import {
  deleteProp,
  footprintValid,
  moveGrave,
  moveProp,
  moveWorkstation,
  paintPlot,
  placeProp,
  propIndexAt,
  recolorProp,
  rotateProp,
  toggleProp,
  type DraftMap,
  type Footprints,
  type Tile,
} from "./tools.js";
import { draftToWire, type WireMap } from "./mapio.js";

/** The editor tools (toolbar). */
export type EditorTool =
  | "place"
  | "select"
  | "rotate"
  | "recolor"
  | "toggle"
  | "erase"
  | "eyedropper"
  | "paint-plot";

/** A current selection in the draft (for move/rotate/recolor/toggle/delete). */
export type Selection =
  | { kind: "prop"; index: number }
  | { kind: "workstation"; id: string }
  | { kind: "grave"; id: string }
  | { kind: "area"; id: string }
  | null;

/** The recolor palette the editor cycles a prop's tint through (original graveyard hues). */
export const RECOLOR_TINTS: number[] = [
  0xffcf6b, // warm lantern amber
  0x8ff0ff, // spectral cyan
  0xb388ff, // wraith violet
  0x6be08a, // will-o'-wisp green
  0xff6b8a, // ember rose
];

export interface EditorState {
  active: boolean;
  draft: DraftMap | null;
  footprints: Footprints;
  catalog: PropCatalog | null;
  tool: EditorTool;
  /** the catalog id held for the place tool (the palette pick). */
  heldCatalogId: string | null;
  heldOrientation: string;
  heldState: string | null;
  /** the section a paint-plot click reassigns the clicked plot to. */
  paintSection: string | null;
  selection: Selection;
  /** the tile the cursor is hovering (for the overlay preview); null = off-canvas. */
  hoverTile: Tile | null;
  /** a status/validity line for the toolbar. */
  status: string;
  /** the last server save result (ok text or a reject reason). */
  saveResult: string | null;
  /** a monotonic revision bumped on every draft change (lets the overlay detect edits). */
  revision: number;

  enter: (draft: DraftMap, footprints: Footprints, catalog: PropCatalog) => void;
  exit: () => void;
  setTool: (tool: EditorTool) => void;
  pickCatalog: (catalogId: string) => void;
  setPaintSection: (section: string) => void;
  setHoverTile: (tile: Tile | null) => void;
  select: (sel: Selection) => void;
  /** The main canvas interaction: apply the active tool at a tile. */
  interactTile: (tile: Tile, button?: "left" | "right") => void;
  /** Act on the current selection (rotate/recolor/toggle/delete buttons). */
  rotateSelected: () => void;
  recolorSelected: () => void;
  toggleSelected: () => void;
  deleteSelected: () => void;
  undo: () => void;
  redo: () => void;
  canUndo: () => boolean;
  canRedo: () => boolean;
  loadDraft: (draft: DraftMap) => void;
  /** The `map.save` payload for the current draft (null when not editing). */
  toWire: () => WireMap | null;
}

// The history lives outside the reactive state (a mutable controller); the store mirrors its
// present into `draft` so React + the overlay re-read it.
let history: History<DraftMap> | null = null;

function orientationsOf(catalog: PropCatalog | null, catalogId: string): string[] {
  const def = catalog?.props[catalogId];
  return def ? Object.keys(def.orientations) : ["s"];
}

function statesOf(catalog: PropCatalog | null, catalogId: string): string[] {
  const def = catalog?.props[catalogId];
  return def?.states ? Object.keys(def.states) : [];
}

export const editorStore = createStore<EditorState>((set, get) => ({
  active: false,
  draft: null,
  footprints: {},
  catalog: null,
  tool: "place",
  heldCatalogId: null,
  heldOrientation: "s",
  heldState: null,
  paintSection: null,
  selection: null,
  hoverTile: null,
  status: "",
  saveResult: null,
  revision: 0,

  enter: (draft, footprints, catalog) => {
    history = new History<DraftMap>(draft, 60);
    // NOTE: `saveResult` is intentionally NOT reset here — a `world.snapshot` after a save
    // re-enters the draft to mirror the authoritative map, and that must not wipe the "saved ✓"
    // status the operator just triggered. It is cleared on `exit` (a fresh session).
    set({
      active: true,
      draft,
      footprints,
      catalog,
      selection: null,
      hoverTile: null,
      status: `${draft.placedProps.length} props · ${draft.areas.length} plots`,
      revision: get().revision + 1,
    });
  },

  exit: () => {
    history = null;
    set({ active: false, draft: null, selection: null, hoverTile: null, saveResult: null, revision: get().revision + 1 });
  },

  setTool: (tool) => set({ tool, selection: tool === "select" ? get().selection : null }),
  pickCatalog: (catalogId) => {
    const orients = orientationsOf(get().catalog, catalogId);
    const states = statesOf(get().catalog, catalogId);
    set({
      tool: "place",
      heldCatalogId: catalogId,
      heldOrientation: orients[0] ?? "s",
      heldState: states[0] ?? null,
    });
  },
  setPaintSection: (section) => set({ paintSection: section, tool: "paint-plot" }),
  setHoverTile: (tile) => set({ hoverTile: tile }),
  select: (sel) => set({ selection: sel }),

  interactTile: (tile, button = "left") => {
    const s = get();
    const draft = s.draft;
    if (!draft || !history) return;

    // right-click always erases whatever prop is under the tile (a quick delete).
    if (button === "right") {
      const idx = propIndexAt(draft, tile, s.footprints);
      if (idx >= 0) get().loadDraft(deleteProp(draft, idx));
      return;
    }

    switch (s.tool) {
      case "place": {
        if (!s.heldCatalogId) return;
        const next = placeProp(draft, s.heldCatalogId, tile, s.footprints, {
          orientation: s.heldOrientation,
          state: s.heldState,
        });
        get().loadDraft(next);
        break;
      }
      case "erase": {
        const idx = propIndexAt(draft, tile, s.footprints);
        if (idx >= 0) get().loadDraft(deleteProp(draft, idx));
        break;
      }
      case "eyedropper": {
        const idx = propIndexAt(draft, tile, s.footprints);
        if (idx >= 0) {
          const p = draft.placedProps[idx];
          get().pickCatalog(p.catalogId);
          set({ heldOrientation: p.orientation, heldState: p.state });
        }
        break;
      }
      case "rotate": {
        const idx = propIndexAt(draft, tile, s.footprints);
        if (idx >= 0) {
          const p = draft.placedProps[idx];
          get().loadDraft(rotateProp(draft, idx, orientationsOf(s.catalog, p.catalogId)));
        }
        break;
      }
      case "toggle": {
        const idx = propIndexAt(draft, tile, s.footprints);
        if (idx >= 0) {
          const p = draft.placedProps[idx];
          get().loadDraft(toggleProp(draft, idx, statesOf(s.catalog, p.catalogId)));
        }
        break;
      }
      case "recolor": {
        const idx = propIndexAt(draft, tile, s.footprints);
        if (idx >= 0) get().loadDraft(recolorProp(draft, idx, RECOLOR_TINTS));
        break;
      }
      case "paint-plot": {
        if (!s.paintSection) return;
        // reassign every plot covering the tile to the chosen section.
        let next = draft;
        for (const area of draft.areas) {
          if (tile.x >= area.x && tile.x < area.x + area.w && tile.y >= area.y && tile.y < area.y + area.h) {
            next = paintPlot(next, area.id, s.paintSection);
          }
        }
        if (next !== draft) get().loadDraft(next);
        break;
      }
      case "select": {
        // pick the topmost prop / a destination under the tile, or move the current selection.
        const sel = s.selection;
        if (sel && sel.kind === "prop") {
          const moved = moveProp(draft, sel.index, tile, s.footprints);
          if (moved !== draft) {
            get().loadDraft(moved);
            return;
          }
        } else if (sel && sel.kind === "workstation") {
          get().loadDraft(moveWorkstation(draft, sel.id, tile));
          return;
        } else if (sel && sel.kind === "grave") {
          get().loadDraft(moveGrave(draft, sel.id, tile));
          return;
        }
        // no active selection (or move blocked) → select what's under the tile.
        const ws = draft.workstations.find((w) => w.x === tile.x && w.y === tile.y);
        const gv = draft.graves.find((g) => g.x === tile.x && g.y === tile.y);
        const idx = propIndexAt(draft, tile, s.footprints);
        if (ws) set({ selection: { kind: "workstation", id: ws.id } });
        else if (gv) set({ selection: { kind: "grave", id: gv.id } });
        else if (idx >= 0) set({ selection: { kind: "prop", index: idx } });
        else set({ selection: null });
        break;
      }
      default:
        break;
    }
  },

  rotateSelected: () => {
    const s = get();
    if (!s.draft || s.selection?.kind !== "prop") return;
    const p = s.draft.placedProps[s.selection.index];
    if (p) get().loadDraft(rotateProp(s.draft, s.selection.index, orientationsOf(s.catalog, p.catalogId)));
  },
  recolorSelected: () => {
    const s = get();
    if (!s.draft || s.selection?.kind !== "prop") return;
    get().loadDraft(recolorProp(s.draft, s.selection.index, RECOLOR_TINTS));
  },
  toggleSelected: () => {
    const s = get();
    if (!s.draft || s.selection?.kind !== "prop") return;
    const p = s.draft.placedProps[s.selection.index];
    if (p) get().loadDraft(toggleProp(s.draft, s.selection.index, statesOf(s.catalog, p.catalogId)));
  },
  deleteSelected: () => {
    const s = get();
    if (!s.draft || s.selection?.kind !== "prop") return;
    get().loadDraft(deleteProp(s.draft, s.selection.index));
    set({ selection: null });
  },

  loadDraft: (draft) => {
    if (!history) history = new History<DraftMap>(draft, 60);
    else history.push(draft);
    set({
      draft: history.current,
      status: `${draft.placedProps.length} props · ${draft.areas.length} plots`,
      revision: get().revision + 1,
    });
  },

  undo: () => {
    if (!history) return;
    const d = history.undo();
    set({ draft: d, selection: null, revision: get().revision + 1, status: `${d.placedProps.length} props · ${d.areas.length} plots` });
  },
  redo: () => {
    if (!history) return;
    const d = history.redo();
    set({ draft: d, selection: null, revision: get().revision + 1, status: `${d.placedProps.length} props · ${d.areas.length} plots` });
  },
  canUndo: () => (history ? history.canUndo : false),
  canRedo: () => (history ? history.canRedo : false),

  toWire: () => {
    const d = get().draft;
    return d ? draftToWire(d) : null;
  },
}));

/** Convenience getter for the PixiJS overlay (identical to `.getState()`). */
export function getEditorState(): EditorState {
  return editorStore.getState();
}

/** The valid/invalid preview for the held prop at the hover tile (green/red overlay). */
export function heldPreviewValid(s: EditorState): boolean {
  if (!s.draft || !s.heldCatalogId || !s.hoverTile) return false;
  return footprintValid(s.draft, s.heldCatalogId, s.hoverTile, s.footprints);
}

/**
 * Build the per-frame overlay view the render loop draws from the current editor
 * state. Pure over the store snapshot + the section tint map: draft props → footprints, plots,
 * grave/workstation markers, the held-prop preview (green/red), and the selection highlight.
 */
export function buildEditorView(sectionTints: Record<string, number>): EditorOverlayView {
  const s = editorStore.getState();
  const d = s.draft;
  if (!s.active || !d) {
    return {
      active: false,
      width: 0,
      height: 0,
      tileSize: 16,
      revision: s.revision,
      props: [],
      areas: [],
      dests: [],
      preview: null,
      selection: null,
      sectionTints,
    };
  }
  const fp = (id: string): { w: number; h: number } => s.footprints[id] ?? { w: 1, h: 1 };

  let preview: EditorOverlayView["preview"] = null;
  if (s.tool === "place" && s.heldCatalogId && s.hoverTile) {
    preview = {
      tile: s.hoverTile,
      footprint: fp(s.heldCatalogId),
      valid: footprintValid(d, s.heldCatalogId, s.hoverTile, s.footprints),
    };
  }

  let selection: EditorOverlayView["selection"] = null;
  const sel = s.selection;
  if (sel?.kind === "prop" && d.placedProps[sel.index]) {
    const p = d.placedProps[sel.index];
    const f = fp(p.catalogId);
    selection = { x: p.tile.x, y: p.tile.y, w: f.w, h: f.h };
  } else if (sel?.kind === "workstation") {
    const w = d.workstations.find((x) => x.id === sel.id);
    if (w) selection = { x: w.x, y: w.y, w: 1, h: 1 };
  } else if (sel?.kind === "grave") {
    const g = d.graves.find((x) => x.id === sel.id);
    if (g) selection = { x: g.x, y: g.y, w: 1, h: 1 };
  } else if (sel?.kind === "area") {
    const a = d.areas.find((x) => x.id === sel.id);
    if (a) selection = { x: a.x, y: a.y, w: a.w, h: a.h };
  }

  return {
    active: true,
    width: d.width,
    height: d.height,
    tileSize: d.tileSize,
    revision: s.revision,
    props: d.placedProps.map((p) => ({ catalogId: p.catalogId, tile: p.tile, footprint: fp(p.catalogId), tint: p.tint })),
    areas: d.areas.map((a) => ({ id: a.id, section: a.section, x: a.x, y: a.y, w: a.w, h: a.h })),
    dests: [
      ...d.graves.map((g) => ({ id: g.id, kind: "grave" as const, x: g.x, y: g.y })),
      ...d.workstations.map((w) => ({ id: w.id, kind: "workstation" as const, x: w.x, y: w.y })),
    ],
    preview,
    selection,
    sectionTints,
  };
}
