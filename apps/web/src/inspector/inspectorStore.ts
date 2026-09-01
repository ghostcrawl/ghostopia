// ghostopia web — the live-inspector store (STAGE 4).
//
// A vanilla Zustand store holding the REAL per-ghost session detail the server relays over
// the authed WS: the latest `browser.frame` ref, `browser.status` (current URL/title), a
// recent event log, extracted records, and mapped errors. The thin inspector components
// subscribe to it; `liveClient` feeds it from server envelopes. This file imports NO
// GhostCrawl SDK and NO key — it only holds what the server relays.
//
// Server-enforced: only the SELECTED ghost ever streams frames/status, so
// `latestFrameRef`/`currentUrl` are populated for the open ghost only. Events/errors accrue
// per ghost from the same envelope stream that drives the world.

import { createStore } from "zustand/vanilla";

/** A recent activity-log entry (Activity tab). */
export interface ActivityEntry {
  ts: number;
  label: string;
}

/** A mapped error surfaced from a `browser.error` / `task.retry` envelope (Errors tab). */
export interface ErrorEntry {
  ts: number;
  code: string;
  retryable: boolean;
}

/** The REAL session detail for one ghost, accumulated from server-relayed envelopes. */
export interface InspectorSession {
  ghostId: string;
  currentUrl: string | null;
  title: string | null;
  latestFrameRef: string | null;
  /**
   * The server's honest reason the live browser frame can't stream right now:
   * the session hasn't opened yet, or the workspace's live-view capability is off. The Browser
   * tab shows this instead of an eternal "No live view yet…" placeholder; `null` once the live
   * view is up (a frame arrives). Optional/additive — absent on older relays.
   */
  viewReason?: string | null;
  events: ActivityEntry[];
  records: unknown[];
  errors: ErrorEntry[];
}

const MAX_EVENTS = 50;

function emptySession(ghostId: string): InspectorSession {
  return {
    ghostId,
    currentUrl: null,
    title: null,
    latestFrameRef: null,
    viewReason: null,
    events: [],
    records: [],
    errors: [],
  };
}

/** The inspector store state + its mutators. */
export interface InspectorState {
  /** the ghost whose inspector PANEL is open (null = closed). */
  openGhostId: string | null;
  /** the ghost currently HOVERED (drives the status popup). */
  hoverGhostId: string | null;
  /** id -> the ghost's accumulated real session detail. */
  sessions: Record<string, InspectorSession>;

  /** Open the inspector for a ghost (the caller also sends `ghost.select`). */
  openInspector: (ghostId: string) => void;
  /** Close the inspector (the caller also sends a deselect). */
  closeInspector: () => void;
  /** Set (or clear) the hovered ghost for the status popup. */
  setHover: (ghostId: string | null) => void;

  /** Store the latest server-relayed frame ref for a ghost (Browser tab `<img>`). */
  applyFrame: (ghostId: string, ref: string) => void;
  /** Store (or clear) the honest live-view unavailability reason (R7 Browser tab). */
  applyView: (ghostId: string, reason: string | null) => void;
  /** Store the ghost's real current URL + title (Overview/status popup). */
  applyStatus: (ghostId: string, currentUrl: string | null, title: string | null) => void;
  /** Append an activity-log entry (Activity tab). */
  pushEvent: (ghostId: string, label: string, ts: number) => void;
  /** Append extracted records so far (Data tab). */
  pushRecords: (ghostId: string, records: unknown[]) => void;
  /** Append a mapped error (Errors tab). */
  pushError: (ghostId: string, code: string, retryable: boolean, ts: number) => void;
}

function withSession(
  sessions: Record<string, InspectorSession>,
  ghostId: string,
  patch: (s: InspectorSession) => InspectorSession,
): Record<string, InspectorSession> {
  const prev = sessions[ghostId] ?? emptySession(ghostId);
  return { ...sessions, [ghostId]: patch(prev) };
}

/** The vanilla inspector store. React chrome subscribes via `useStore(inspectorStore, sel)`. */
export const inspectorStore = createStore<InspectorState>((set) => ({
  openGhostId: null,
  hoverGhostId: null,
  sessions: {},

  openInspector: (ghostId) => set({ openGhostId: ghostId }),
  closeInspector: () => set({ openGhostId: null }),
  setHover: (ghostId) => set({ hoverGhostId: ghostId }),

  applyFrame: (ghostId, ref) =>
    set((s) => ({
      // a real frame arriving means the live view IS up — clear any stale reason.
      sessions: withSession(s.sessions, ghostId, (sess) => ({
        ...sess,
        latestFrameRef: ref,
        viewReason: null,
      })),
    })),

  applyView: (ghostId, reason) =>
    set((s) => ({
      sessions: withSession(s.sessions, ghostId, (sess) => ({ ...sess, viewReason: reason })),
    })),

  applyStatus: (ghostId, currentUrl, title) =>
    set((s) => ({
      sessions: withSession(s.sessions, ghostId, (sess) => ({
        ...sess,
        currentUrl: currentUrl ?? sess.currentUrl,
        title: title ?? sess.title,
      })),
    })),

  pushEvent: (ghostId, label, ts) =>
    set((s) => ({
      sessions: withSession(s.sessions, ghostId, (sess) => ({
        ...sess,
        events: [...sess.events, { ts, label }].slice(-MAX_EVENTS),
      })),
    })),

  pushRecords: (ghostId, records) =>
    set((s) => ({
      sessions: withSession(s.sessions, ghostId, (sess) => {
        // Append only records whose JSON is not already present — a re-relayed batch cannot
        // duplicate rows. Newest-first append order is preserved (no re-sort).
        const seen = new Set(sess.records.map((r) => JSON.stringify(r)));
        const fresh = records.filter((r) => {
          const key = JSON.stringify(r);
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
        return fresh.length === 0 ? sess : { ...sess, records: [...sess.records, ...fresh] };
      }),
    })),

  pushError: (ghostId, code, retryable, ts) =>
    set((s) => ({
      sessions: withSession(s.sessions, ghostId, (sess) => ({
        ...sess,
        errors: [...sess.errors, { ts, code, retryable }].slice(-MAX_EVENTS),
      })),
    })),
}));

/** Read the session detail for a ghost (or an empty session when none yet). */
export function sessionFor(ghostId: string | null): InspectorSession | null {
  if (!ghostId) return null;
  return inspectorStore.getState().sessions[ghostId] ?? emptySession(ghostId);
}
