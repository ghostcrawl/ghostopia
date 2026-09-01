// ghostopia web — the live browser inspector panel (STAGE 4 MILESTONE).
//
// Opens when the operator clicks a working ghost. On open it sends `ghost.select {ghost_id}`
// so the Python server starts `recordings.visual().watch()` for ONLY this ghost and relays
// `browser.frame` envelopes; on close it sends a deselect so the server stops the stream
// (one stream at a time). The Browser tab draws the REAL relayed frames; the
// other tabs bind to the ghost's real relayed session state. The client never calls
// GhostCrawl and holds no key.

import { useState } from "react";
import type { JSX } from "react";
import { useStore } from "zustand";

import { useWorldStore, type Ghost } from "@ghostopia/ghost-renderer";

import { inspectorStore, type InspectorSession } from "./inspectorStore";
import { TabBody, browserTabLabel, visibleTabs, type TabName } from "./tabs";

/** Callbacks the host (App) supplies so the panel drives the ONE live WS connection. */
export interface InspectorPanelProps {
  /** send `ghost.select {ghost_id}` (null deselects) over the live client's WS. */
  onSelect: (ghostId: string | null) => void;
}

const EMPTY_SESSION = (id: string): InspectorSession => ({
  ghostId: id,
  currentUrl: null,
  title: null,
  latestFrameRef: null,
  events: [],
  records: [],
  errors: [],
});

/**
 * The inspector panel. Renders nothing until a ghost is opened (`inspectorStore.openGhostId`).
 * Sending `ghost.select` is the host's job via {@link InspectorPanelProps.onSelect}, kept out
 * of the store so the store stays a pure state container.
 */
export function InspectorPanel({ onSelect }: InspectorPanelProps): JSX.Element | null {
  const openGhostId = useStore(inspectorStore, (s) => s.openGhostId);
  const ghost = useStore(useWorldStore, (s) =>
    openGhostId ? (s.ghosts[openGhostId] as Ghost | undefined) : undefined,
  );
  const session = useStore(
    inspectorStore,
    (s) => (openGhostId ? s.sessions[openGhostId] : undefined),
  );
  const [tab, setTab] = useState<TabName>("Browser");

  if (!openGhostId || !ghost) return null;
  const sess = session ?? EMPTY_SESSION(openGhostId);

  // The tab keys visible for THIS ghost (api-only drops the redundant static "Activity" tab —
  // its Browser slot is relabeled "Activity" and already shows the activity feed). If the
  // persisted selection is now hidden (e.g. the operator had "Activity" open on a browser-nav
  // ghost then selected an api-only one), fall back to the always-present "Browser" slot so the
  // nav never shows a stale/no active tab.
  const tabs = visibleTabs(ghost.workKind);
  const activeTab: TabName = tabs.includes(tab) ? tab : "Browser";

  const close = (): void => {
    inspectorStore.getState().closeInspector();
    onSelect(null); // server stops the stream (deselect)
  };

  return (
    <aside className="inspector" role="dialog" aria-label={`inspector for ${ghost.name}`}>
      <header className="inspector__header">
        <span className="inspector__name">{ghost.name}</span>
        <span className="inspector__state">{ghost.state}</span>
        <button type="button" className="inspector__close" aria-label="close" onClick={close}>
          ✕
        </button>
      </header>
      <nav className="inspector__tabs">
        {tabs.map((t) => (
          <button
            type="button"
            key={t}
            className={`inspector__tab${t === activeTab ? " inspector__tab--active" : ""}`}
            aria-pressed={t === activeTab}
            onClick={() => setTab(t)}
          >
            {/* The Browser tab reads "Activity" for an API-only ghost (its body renders
                the activity/data view, not a live frame). The static "Activity" tab is dropped
                for api-only ghosts (visibleTabs) so the label never appears twice. */}
            {t === "Browser" ? browserTabLabel(ghost.workKind) : t}
          </button>
        ))}
      </nav>
      <div className="inspector__body">
        <TabBody tab={activeTab} ghost={ghost} session={sess} />
      </div>
    </aside>
  );
}

/**
 * Open the inspector for a ghost: mark it open in the store, mirror the selection into the
 * world store (render highlight), and send `ghost.select` so the server begins streaming.
 */
export function openInspectorFor(
  ghostId: string,
  onSelect: (ghostId: string | null) => void,
): void {
  inspectorStore.getState().openInspector(ghostId);
  useWorldStore.getState().selectGhost(ghostId);
  onSelect(ghostId);
}
