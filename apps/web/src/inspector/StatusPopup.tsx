// ghostopia web — the hover status popup (STAGE 4 partial).
//
// Hovering a working ghost shows its CURRENT real activity: state + current URL, read from the
// ghost's relayed session state (`inspectorStore` — populated by the server's `browser.status`
// for the selected ghost, plus the coarse state the world store already carries). Non-selected
// ghosts show lightweight status only (no frame stream). This file imports NO
// GhostCrawl SDK and NO key.

import type { JSX } from "react";
import { useStore } from "zustand";

import { useWorldStore, type Ghost } from "@ghostopia/ghost-renderer";

import { inspectorStore } from "./inspectorStore";

/**
 * The hover popup. Renders nothing unless a ghost is hovered (`inspectorStore.hoverGhostId`).
 * Shows the ghost's name + coarse state (always available from the world store) and, when the
 * server has relayed it for the selected ghost, the real current URL.
 */
export function StatusPopup(): JSX.Element | null {
  const hoverId = useStore(inspectorStore, (s) => s.hoverGhostId);
  const ghost = useStore(useWorldStore, (s) =>
    hoverId ? (s.ghosts[hoverId] as Ghost | undefined) : undefined,
  );
  const currentUrl = useStore(
    inspectorStore,
    (s) => (hoverId ? (s.sessions[hoverId]?.currentUrl ?? null) : null),
  );

  if (!hoverId || !ghost) return null;

  return (
    <div className="status-popup" role="status">
      <div className="status-popup__name">{ghost.name}</div>
      <div className="status-popup__state">{ghost.state}</div>
      {currentUrl && <div className="status-popup__url">{currentUrl}</div>}
    </div>
  );
}
