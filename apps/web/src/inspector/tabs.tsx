// ghostopia web — the live-inspector tab bodies (STAGE 4).
//
// Overview / Browser / Activity / Data / Errors — each bound to the REAL session state the
// Python server relays over the authed WS into `inspectorStore`. The Browser tab renders the
// latest server-relayed `browser.frame` ref straight into an `<img>` (the server already
// normalized it: absolute / data URI / prefixed-relative all render as a plain src). This file
// imports NO GhostCrawl SDK and NO key — it draws only what the server relayed.

import { useState } from "react";
import type { JSX } from "react";

import type { Ghost, GhostWorkKind } from "@ghostopia/ghost-renderer";

import { isSurfaceSafe, safeSurfaceText } from "../surfaceSafe";
import type { InspectorSession } from "./inspectorStore";

/** The five inspector tabs. */
export const TABS = ["Overview", "Browser", "Activity", "Data", "Errors"] as const;
export type TabName = (typeof TABS)[number];

/**
 * The displayed label for the "Browser" tab slot. An API-only ghost has no live
 * browser view, so its tab reads "Activity" (the body renders the activity/records view,
 * not a black frame); a browser-navigation ghost (or an unknown/absent work-kind, which
 * defaults to the graceful browser-nav path) keeps "Browser".
 */
export function browserTabLabel(workKind: GhostWorkKind | null | undefined): string {
  return workKind === "api-only" ? "Activity" : "Browser";
}

/**
 * The tab keys visible for a given ghost (defect: DUPLICATE "Activity" tab). For an API-only
 * ghost the "Browser" slot is RELABELED to "Activity" (browserTabLabel) and its body — the
 * ActivityView records feed — already falls back to the recent activity log, so it fully
 * supersedes the static "Activity" tab (the plain event log). Rendering both produced two
 * buttons reading "Activity". We therefore drop the redundant static "Activity" key for
 * API-only ghosts (no information is lost — the relabeled Browser tab surfaces the same log
 * when no records have landed). A browser-navigation ghost keeps all five: its "Browser"
 * tab (live frame) and "Activity" tab (event log) are genuinely different surfaces.
 */
export function visibleTabs(workKind: GhostWorkKind | null | undefined): readonly TabName[] {
  if (workKind === "api-only") {
    return TABS.filter((t) => t !== "Activity");
  }
  return TABS;
}

/**
 * Resolve a server-relayed frame ref to an `<img>` src. The server normalizes refs before
 * fan-out (absolute + `data:` pass through; a relative ref is prefixed with the target frame
 * base URL), so the client renders the ref directly — it only guards an empty ref.
 */
export function frameSrc(ref: string | null): string | null {
  if (!ref) return null;
  return ref; // already normalized server-side (`_normalize_frame_ref`)
}

export function OverviewTab({
  ghost,
  session,
}: {
  ghost: Ghost;
  session: InspectorSession;
}): JSX.Element {
  // The sanitized, SESSION-scoped persona sentence (server-built from the device/OS/
  // browser-class/locale whitelist). The server already sanitizes it; the TS surface
  // gate is the client's last line — a non-empty AND surface-safe persona renders as one more
  // "Session" row in the existing overview grid, otherwise the row is OMITTED entirely (never
  // a raw UA / engine codename / vendor term, never a fallback string).
  const persona =
    typeof ghost.persona === "string" &&
    ghost.persona.trim().length > 0 &&
    isSurfaceSafe(ghost.persona)
      ? ghost.persona
      : null;
  return (
    <dl className="inspector__overview">
      <dt>state</dt>
      <dd>{ghost.state}</dd>
      {persona && (
        <>
          <dt>Session</dt>
          <dd className="inspector__persona">{persona}</dd>
        </>
      )}
      <dt>current URL</dt>
      <dd className="inspector__url">{session.currentUrl ?? "—"}</dd>
      <dt>title</dt>
      <dd>{session.title ?? "—"}</dd>
      <dt>records</dt>
      <dd>{session.records.length}</dd>
    </dl>
  );
}

/** A record-field lookup treating the record as a flat object; "" when absent (mirrors DepartmentResults.field). */
function recordField(record: unknown, key: string): string {
  if (record !== null && typeof record === "object") {
    const v = (record as Record<string, unknown>)[key];
    if (v !== undefined && v !== null) return String(v);
  }
  return "";
}

/**
 * A product thumbnail for one streamed record (S2): a 32px `<img>` from `record.image`, or —
 * for a missing/broken URL — a quiet placeholder square (never a browser broken-image icon).
 * The `onError` swap keeps the "never a raw ugly fallback" tone the rest of the app holds.
 */
function RecordThumb({ src }: { src: string }): JSX.Element {
  const [broken, setBroken] = useState(false);
  if (!src || broken) {
    return (
      <div className="inspector__record-thumb inspector__record-thumb--missing" aria-hidden="true" />
    );
  }
  return (
    <img
      className="inspector__record-thumb"
      src={src}
      alt=""
      loading="lazy"
      onError={() => setBroken(true)}
    />
  );
}

/**
 * State A: the live activity/data feed for an api-only (scrape/extract)
 * ghost. There is NO browser frame — instead of the misleading black `.inspector__frame` box we
 * surface the REAL records the ghost is filing right now as structured priced rows (32px
 * thumbnail + title + right-aligned price, mirroring the DepartmentResults priced row) with a
 * "currently on: {url}" line and a "{n} brought back so far" haul summary. New records prepend
 * (newest first) with a brief fade-in so filing reads as "live," not a static dump. When no
 * records have landed yet we fall back to the recent activity log, then a curated on-theme
 * empty message. State B (the live browser frame) is a different surface entirely (BrowserTab).
 */
function ActivityView({ session }: { session: InspectorSession }): JSX.Element {
  if (session.records.length > 0) {
    const currentUrl = session.currentUrl;
    // newest first (matches the reverse-chronological convention of the activity log).
    const rows = session.records.map((r, i) => ({ r, i })).reverse();
    return (
      <div className="inspector__records-feed">
        <div className="dept-card__count inspector__haul">
          {session.records.length} brought back so far
        </div>
        <ol className="inspector__records">
          {rows.map(({ r, i }) => {
            const title = recordField(r, "title");
            const price = recordField(r, "price");
            const image = recordField(r, "image");
            return (
              <li className="inspector__record" key={i}>
                <div className="inspector__record-main">
                  <RecordThumb src={image} />
                  <span className="inspector__record-title">{title || "—"}</span>
                  {price && (
                    <span className="dept-card__row-price inspector__record-price">{price}</span>
                  )}
                </div>
                {currentUrl && (
                  <span className="inspector__record-current">currently on: {currentUrl}</span>
                )}
              </li>
            );
          })}
        </ol>
      </div>
    );
  }
  if (session.events.length > 0) {
    return (
      <ul className="inspector__log">
        {session.events
          .slice()
          .reverse()
          .map((e, i) => (
            <li className="inspector__log-row" key={`${e.ts}-${i}`}>
              <span className="inspector__log-ts">{e.ts.toFixed(1)}</span>
              <span className="inspector__log-label">{e.label}</span>
            </li>
          ))}
      </ul>
    );
  }
  return (
    <div className="inspector__placeholder">
      Its ghosts are still gathering — records will stream in here…
    </div>
  );
}

export function BrowserTab({
  ghost,
  session,
}: {
  ghost: Ghost;
  session: InspectorSession;
}): JSX.Element {
  // Branch on the ghost's work-kind. An API-only ghost never opens a live browser
  // stream server-side (FrameFanout), so rendering the black frame box would falsely
  // imply a browser view — show the activity/data view instead.
  if (ghost.workKind === "api-only") {
    return (
      <div className="inspector__browser inspector__browser--activity">
        <ActivityView session={session} />
      </div>
    );
  }
  // Browser-navigation ghost: this ghost is LIVE-BROWSABLE — mark it with a watchable affordance
  // that visually distinguishes it from the stateless (api-only) workforce.
  const src = frameSrc(session.latestFrameRef);
  // The server's honest reason the live view can't stream yet (session opening, or the live-view
  // capability is off) — surface-gated so a leaked/vendor reason is dropped to the on-theme
  // placeholder rather than shown (omit-not-leak). Shown only when there is no frame.
  const reason =
    typeof session.viewReason === "string" &&
    session.viewReason.trim().length > 0 &&
    isSurfaceSafe(session.viewReason)
      ? session.viewReason
      : null;
  return (
    <div className="inspector__browser">
      <span className="inspector__watchable" aria-hidden="true">
        Live browsable
      </span>
      {src ? (
        <img className="inspector__frame" src={src} alt="live browser frame" />
      ) : (
        <div className="inspector__placeholder">{reason ?? "No live view yet…"}</div>
      )}
    </div>
  );
}

function ActivityTab({ session }: { session: InspectorSession }): JSX.Element {
  if (session.events.length === 0) {
    return <div className="inspector__placeholder">Nothing stirring yet…</div>;
  }
  return (
    <ul className="inspector__log">
      {session.events
        .slice()
        .reverse()
        .map((e, i) => (
          <li className="inspector__log-row" key={`${e.ts}-${i}`}>
            <span className="inspector__log-ts">{e.ts.toFixed(1)}</span>
            <span className="inspector__log-label">{e.label}</span>
          </li>
        ))}
    </ul>
  );
}

export function DataTab({ session }: { session: InspectorSession }): JSX.Element {
  if (session.records.length === 0) {
    return <div className="inspector__placeholder">No findings yet…</div>;
  }
  return (
    <ol className="inspector__records">
      {session.records.map((r, i) => (
        <li className="inspector__record" key={i}>
          <code>{JSON.stringify(r)}</code>
        </li>
      ))}
    </ol>
  );
}

function ErrorsTab({ session }: { session: InspectorSession }): JSX.Element {
  if (session.errors.length === 0) {
    return <div className="inspector__placeholder">All quiet — no missteps…</div>;
  }
  return (
    <ul className="inspector__errors">
      {session.errors
        .slice()
        .reverse()
        .map((e, i) => (
          <li className="inspector__error-row" key={`${e.ts}-${i}`}>
            <span className="inspector__error-code">
              {safeSurfaceText(e.code, "Held at the gate…")}
            </span>
            <span className="inspector__error-retryable">
              {e.retryable ? "retryable" : "terminal"}
            </span>
          </li>
        ))}
    </ul>
  );
}

/** Render the active tab body bound to the ghost's real relayed session state. */
export function TabBody({
  tab,
  ghost,
  session,
}: {
  tab: TabName;
  ghost: Ghost;
  session: InspectorSession;
}): JSX.Element {
  switch (tab) {
    case "Overview":
      return <OverviewTab ghost={ghost} session={session} />;
    case "Browser":
      return <BrowserTab ghost={ghost} session={session} />;
    case "Activity":
      return <ActivityTab session={session} />;
    case "Data":
      return <DataTab session={session} />;
    case "Errors":
      return <ErrorsTab session={session} />;
    default:
      return <div className="inspector__placeholder" />;
  }
}
