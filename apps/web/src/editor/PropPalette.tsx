// ghostopia web — the Graveyard Builder prop palette (the catalog, pickable).
//
// A React panel listing every catalog prop grouped by category (light / landmark / decor / …),
// each a pickable swatch. Picking one arms the "place" tool with that catalog id. Labels come
// from the SERVER-sourced catalog data (no hard-coded prop copy) — the thin-frontend boundary
// holds (no SDK/key here). The swatch colour is derived from the catalog id so the palette
// reads without needing the atlas rendered into it.

import type { JSX } from "react";
import { useStore } from "zustand";

import { editorStore } from "./editorStore.js";

/** Deterministic swatch hue from a catalog id (stable, readable — pure presentation). */
function swatchColor(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  const hue = h % 360;
  return `hsl(${hue} 55% 42%)`;
}

/** The prop palette: catalog props grouped by category, each a pickable placement swatch. */
export function PropPalette(): JSX.Element | null {
  const catalog = useStore(editorStore, (s) => s.catalog);
  const held = useStore(editorStore, (s) => s.heldCatalogId);
  const tool = useStore(editorStore, (s) => s.tool);
  if (!catalog) return null;

  // group prop ids by category (stable order within a category).
  const groups: Record<string, string[]> = {};
  for (const [id, def] of Object.entries(catalog.props)) {
    (groups[def.category] ??= []).push(id);
  }
  const categories = Object.keys(groups).sort();

  return (
    <div className="palette">
      <div className="palette__head">props</div>
      <div className="palette__groups">
        {categories.map((cat) => (
          <div className="palette__group" key={cat}>
            <div className="palette__cat">{cat}</div>
            <div className="palette__swatches">
              {groups[cat].map((id) => {
                const on = held === id && tool === "place";
                return (
                  <button
                    type="button"
                    key={id}
                    className={`palette__swatch${on ? " palette__swatch--on" : ""}`}
                    title={id}
                    aria-pressed={on}
                    onClick={() => editorStore.getState().pickCatalog(id)}
                  >
                    <span className="palette__chip" style={{ background: swatchColor(id) }} />
                    <span className="palette__label">{id}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
