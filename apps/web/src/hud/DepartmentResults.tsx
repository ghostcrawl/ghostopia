// ghostopia web — the floating DEPARTMENT FINDINGS card (196).
//
// The business model made obvious: a department plot on the map is a RESULT REPOSITORY. Click
// a department and this card floats up with the REAL records that department's ghosts brought
// back — title / price / rating / availability — sourced from the same server `result.mission_
// progress` preview the Data Graveyard renders (per-section). It opens from the canvas
// click (RenderLoop `onSelectSection` → `sectionFocusStore`) and closes on ✕ / Esc / bare
// ground. It imports NO GhostCrawl SDK and NO key — only server-relayed records.

import { useEffect, useState } from "react";
import type { JSX } from "react";
import { useStore } from "zustand";

import { catalogStore } from "./catalogStore";
import { previewForSection } from "./DataGraveyard";
import { ExportButtons } from "./ExportButtons";
import { resultsStore, dedupKey } from "./resultsStore";
import { sectionFocusStore } from "./sectionFocusStore";

/**
 * A 28px product thumbnail (S3) from `record.image`, or a quiet placeholder square for a
 * missing/broken URL (never a browser broken-image icon). The image is set via `<img src>`
 * (never innerHTML), so a hostile URL cannot execute — worst case it 404s → onError.
 */
function RowThumb({ src }: { src: string }): JSX.Element {
  const [broken, setBroken] = useState(false);
  if (!src || broken) {
    return <div className="dept-card__row-thumb export-thumb--missing" aria-hidden="true" />;
  }
  return (
    <img
      className="dept-card__row-thumb"
      src={src}
      alt=""
      loading="lazy"
      onError={() => setBroken(true)}
    />
  );
}

/** A record field lookup treating the record as a flat object; "" when absent. */
function field(record: unknown, key: string): string {
  if (record !== null && typeof record === "object") {
    const v = (record as Record<string, unknown>)[key];
    if (v !== undefined && v !== null) return String(v);
  }
  return "";
}

/** The non-priced fallback: the first few record fields as "k: v · k: v". */
function fields(record: unknown): string {
  if (record === null || record === undefined) return "";
  if (typeof record === "object") {
    return Object.entries(record as Record<string, unknown>)
      .slice(0, 4)
      .map(([k, v]) => `${k}: ${String(v)}`)
      .join(" · ");
  }
  return String(record);
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** The floating findings card for the department the operator clicked on the map. */
export function DepartmentResults(): JSX.Element {
  const focused = useStore(sectionFocusStore, (s) => s.focused);
  const preview = useStore(resultsStore, (s) => s.preview);
  const sections = useStore(catalogStore, (s) => s.sections);

  // Esc closes the card (mirrors the world deselect key), one-shot while it is open.
  useEffect(() => {
    if (!focused) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") sectionFocusStore.getState().clear();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focused]);

  if (!focused) return <></>;

  const label = sections.find((s) => s.id === focused)?.label ?? focused;
  const rows = previewForSection(preview, focused);

  return (
    <aside className="dept-card" aria-label={`${label} findings`} role="dialog">
      <header className="dept-card__head">
        <div className="dept-card__titles">
          <span className="dept-card__eyebrow">department findings</span>
          <span className="dept-card__title">{label}</span>
        </div>
        <button
          type="button"
          className="dept-card__close"
          aria-label="close findings"
          onClick={() => sectionFocusStore.getState().clear()}
        >
          ✕
        </button>
      </header>
      <div className="dept-card__count">
        <span>{rows.length} brought back</span>
        <ExportButtons rows={rows} dept={focused} label={label} />
      </div>
      <ul className="dept-card__list">
        {rows.length === 0 && (
          <li className="dept-card__empty">
            No findings yet — its ghosts are still gathering. Records land here as they work…
          </li>
        )}
        {rows.map((row) => {
          const title = field(row.record, "title");
          const price = field(row.record, "price");
          const rating = field(row.record, "rating");
          const availability = field(row.record, "availability");
          const priced = title || price;
          return (
            <li className="dept-card__row" key={dedupKey(row)}>
              <RowThumb src={field(row.record, "image")} />
              {priced ? (
                <>
                  <span className="dept-card__row-title">{title || hostOf(row.url ?? "")}</span>
                  <span className="dept-card__row-meta">
                    {price && <span className="dept-card__row-price">{price}</span>}
                    {rating && <span className="dept-card__row-rating">{rating}</span>}
                    {availability && (
                      <span className="dept-card__row-avail">{availability}</span>
                    )}
                  </span>
                </>
              ) : (
                <span className="dept-card__row-fields">{fields(row.record)}</span>
              )}
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
