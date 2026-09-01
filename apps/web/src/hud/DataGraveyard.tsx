// ghostopia web — the Data Graveyard (STAGE 7 milestone, the results section of the world).
//
// Completed missions (grouped) + a data-preview table of the most recent REAL extracted
// records — everything sourced from the server's `result.mission_progress` envelopes, which
// the server computes from the persisted SQLite result store. As missions complete they land
// here, in the data-graveyard section. This file imports NO GhostCrawl SDK and NO key — it
// renders only what the server relayed from real extraction.

import { useState } from "react";
import type { JSX, KeyboardEvent } from "react";
import { useStore } from "zustand";

import { resultsStore, dedupKey, type PreviewRow } from "./resultsStore";
import { catalogStore } from "./catalogStore";
import { ExportButtons } from "./ExportButtons";

/**
 * A 20px product thumbnail (S3) from `record.image`, or a quiet placeholder square for a
 * missing/broken URL. Set via `<img src>` only (never innerHTML) so a hostile URL cannot
 * execute — the shared "no image" token matches the dept-card + inspector thumbs.
 */
function RecThumb({ src }: { src: string }): JSX.Element {
  const [broken, setBroken] = useState(false);
  if (!src || broken) {
    return <div className="graveyard__rec-thumb export-thumb--missing" aria-hidden="true" />;
  }
  return (
    <img
      className="graveyard__rec-thumb"
      src={src}
      alt=""
      loading="lazy"
      onError={() => setBroken(true)}
    />
  );
}

/**
 * The preview rows belonging to one completed mission (click-through: reveal THAT
 * mission's found results). Filters by `missionId`; if no preview row carries the id (a
 * legacy envelope without `mission_id`), falls back to the full preview so the reveal is
 * never empty.
 */
export function previewForMission(preview: PreviewRow[], missionId: string): PreviewRow[] {
  const scoped = preview.filter((r) => r.missionId === missionId);
  return scoped.length > 0 ? scoped : preview;
}

/**
 * The preview rows belonging to one department (each department surfaces its OWN
 * found records). Filters by `section` — exact, no fallback: a department-grouped surface
 * must show only the records that department's ghosts actually brought back.
 */
export function previewForSection(preview: PreviewRow[], sectionId: string): PreviewRow[] {
  return preview.filter((r) => r.section === sectionId);
}

/** A record field lookup that treats the record as a flat object, returning "" when absent. */
function recordField(record: unknown, key: string): string {
  if (record !== null && typeof record === "object") {
    const v = (record as Record<string, unknown>)[key];
    if (v !== undefined && v !== null) return String(v);
  }
  return "";
}

function previewCells(record: unknown): string {
  if (record === null || record === undefined) return "";
  if (typeof record === "object") {
    return Object.entries(record as Record<string, unknown>)
      .slice(0, 4)
      .map(([k, v]) => `${k}: ${String(v)}`)
      .join(" · ");
  }
  return String(record);
}

/** The Data Graveyard panel: completed missions + a live data preview from real records. */
export function DataGraveyard(): JSX.Element {
  const completed = useStore(resultsStore, (s) => s.completedMissions);
  const preview = useStore(resultsStore, (s) => s.preview);
  const bestOffers = useStore(resultsStore, (s) => s.bestOffers);
  const sections = useStore(catalogStore, (s) => s.sections);

  // The set of scraped urls that ARE the winning (min-price) offer for their product (R5) — a
  // priced row whose url matches gets the "best" badge. Keyed on source_url (else link) so the
  // marker follows the server's best-price selection, never re-derived on the client.
  const bestUrls = new Set(
    bestOffers.map((o) => o.sourceUrl ?? o.link ?? "").filter((u) => u !== ""),
  );
  // Which completed mission is expanded to reveal its found results (click-through).
  const [openId, setOpenId] = useState<string | null>(null);
  // Group finds by completed mission (default) or by department (section). Additive.
  const [view, setView] = useState<"mission" | "department">("mission");

  const toggle = (id: string): void => setOpenId((cur) => (cur === id ? null : id));

  // The department label from the capacity catalog (on-theme, never a raw internal id). Fall
  // back to the id only when the catalog hasn't relayed a label for it yet.
  const labelFor = (sectionId: string): string =>
    sections.find((s) => s.id === sectionId)?.label ?? sectionId;

  // The departments that actually have found records in the current preview.
  const departments = Array.from(
    new Set(preview.map((r) => r.section).filter((s): s is string => !!s)),
  );

  return (
    <section className="graveyard" aria-label="data graveyard">
      <header className="graveyard__head">
        <span className="graveyard__title">data graveyard</span>
        <span className="graveyard__count">{completed.length} completed</span>
      </header>

      <div className="graveyard__view-toggles" role="group" aria-label="group findings by">
        <button
          type="button"
          className="graveyard__view-toggle"
          data-view="mission"
          aria-pressed={view === "mission"}
          onClick={() => setView("mission")}
        >
          by mission
        </button>
        <button
          type="button"
          className="graveyard__view-toggle"
          data-view="department"
          aria-pressed={view === "department"}
          onClick={() => setView("department")}
        >
          by department
        </button>
      </div>

      {view === "department" && (
        <div className="graveyard__departments">
          {departments.length === 0 && (
            <div className="graveyard__empty">No findings yet — records land here as ghosts finish…</div>
          )}
          {departments.map((sectionId) => {
            const rows = previewForSection(preview, sectionId);
            return (
              <div className="graveyard__dept-group" key={sectionId}>
                <div className="graveyard__dept-head">
                  <span className="graveyard__dept-title">{labelFor(sectionId)}</span>
                  <span className="graveyard__dept-count">{rows.length} found</span>
                  <ExportButtons rows={rows} dept={sectionId} label={labelFor(sectionId)} />
                </div>
                <ul className="graveyard__dept-preview graveyard__preview-list">
                  {rows.map((row) => {
                    const title = recordField(row.record, "title");
                    const price = recordField(row.record, "price");
                    const priced = title || price;
                    const isBest = !!row.url && bestUrls.has(row.url);
                    return (
                      <li
                        className={`graveyard__priced-row${isBest ? " graveyard__priced-row--best" : ""}`}
                        key={dedupKey(row)}
                      >
                        <RecThumb src={recordField(row.record, "image")} />
                        {priced ? (
                          <>
                            <span className="graveyard__rec-title">{title || hostOf(row.url ?? "")}</span>
                            {price && <span className="graveyard__rec-price">{price}</span>}
                          </>
                        ) : (
                          <span className="graveyard__preview-fields">{previewCells(row.record)}</span>
                        )}
                        {isBest && (
                          <span className="graveyard__best-badge" title="lowest price found">best</span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </div>
      )}

      {view === "mission" && (
      <div className="graveyard__missions">
        {completed.length === 0 && (
          <div className="graveyard__empty">No findings yet — records land here as ghosts finish…</div>
        )}
        {completed.map((m) => {
          const open = openId === m.id;
          const rows = open ? previewForMission(preview, m.id) : [];
          return (
            <div className="graveyard__mission-group" key={m.id}>
              {/* A real keyboard-accessible affordance (mirrors the mode-toggle
                  aria-pressed pattern) — click / Enter / Space reveals THIS mission's rows. */}
              <div
                className={`graveyard__mission${open ? " graveyard__mission--open" : ""}`}
                role="button"
                tabIndex={0}
                aria-expanded={open}
                aria-label={`show findings for ${m.title}`}
                onClick={() => toggle(m.id)}
                onKeyDown={(e: KeyboardEvent<HTMLDivElement>) => {
                  if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
                    e.preventDefault();
                    toggle(m.id);
                  }
                }}
              >
                <div className="graveyard__mission-title" title={m.id}>{m.title}</div>
                <div className="graveyard__mission-stats">
                  <span className="graveyard__rec">{m.progress.records} records</span>
                  <span className="graveyard__ok">{m.progress.completed} ok</span>
                  {m.progress.failed > 0 && <span className="graveyard__fail">{m.progress.failed} failed</span>}
                </div>
              </div>
              {open && (
                <ul className="graveyard__mission-preview graveyard__preview-list">
                  {rows.length === 0 && (
                    <li className="graveyard__empty">No findings yet…</li>
                  )}
                  {rows.map((row) => (
                    <li className="graveyard__preview-row" key={dedupKey(row)}>
                      {row.url && (
                        <span className="graveyard__preview-url" title={row.url}>{hostOf(row.url)}</span>
                      )}
                      <span className="graveyard__preview-fields">{previewCells(row.record)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
      )}

      <div className="graveyard__preview">
        <div className="graveyard__preview-head">data preview ({preview.length})</div>
        <ul className="graveyard__preview-list">
          {preview.length === 0 && <li className="graveyard__empty">No findings yet…</li>}
          {preview.map((row) => (
            <li className="graveyard__preview-row" key={dedupKey(row)}>
              {row.url && (
                <span className="graveyard__preview-url" title={row.url}>{hostOf(row.url)}</span>
              )}
              <span className="graveyard__preview-fields">{previewCells(row.record)}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
