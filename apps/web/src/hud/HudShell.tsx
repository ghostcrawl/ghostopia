// ghostopia web — the HUD shell: a single, non-overlapping, collapsible dock.
//
// Previously the Live-mode panels (mission / dashboard / sections / selected-ghost /
// diagnostics / data / roster) were each absolutely positioned and overlapped one another
// and the in-world section labels. HudShell docks them ALL into one scrollable column of
// collapsible groups (native <details>/<summary> — accessible + testable), so nothing
// stacks on top of anything else and the world (incl. its section labels) stays uncovered.
// On a phone the dock becomes a bottom-sheet that fits inside the safe area (see styles.css).
//
// It renders CHROME ONLY and imports NO GhostCrawl SDK and NO key — pure layout + React.

import { useState } from "react";
import type { JSX, ReactNode } from "react";

/** One docked panel: a titled, collapsible group wrapping an existing HUD panel node. */
export interface HudPanel {
  id: string;
  title: string;
  node: ReactNode;
  /** Open by default? (the tidy minimal set is open; the rest start collapsed). */
  defaultOpen?: boolean;
}

/**
 * The docked control shell. `topActions` renders as a prominent, always-visible action bar
 * (e.g. the "Run workforce" button) above the collapsible panel groups. The whole dock itself
 * collapses to a slim bar so the operator can reclaim the full world when they want to.
 */
export function HudShell({
  panels,
  topActions,
}: {
  panels: HudPanel[];
  topActions?: ReactNode;
}): JSX.Element {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside className="hud-shell" data-collapsed={collapsed} aria-label="control dock">
      <header className="hud-shell__bar">
        <span className="hud-shell__title">control</span>
        <button
          type="button"
          className="hud-shell__collapse"
          aria-label={collapsed ? "expand control dock" : "collapse control dock"}
          aria-expanded={!collapsed}
          onClick={() => setCollapsed((v) => !v)}
        >
          {collapsed ? "▸" : "▾"}
        </button>
      </header>
      {!collapsed && (
        <div className="hud-shell__body">
          {topActions && <div className="hud-shell__actions">{topActions}</div>}
          {panels.map((p) => (
            <details className="hud-shell__group" key={p.id} open={p.defaultOpen ?? true}>
              <summary className="hud-shell__group-title">{p.title}</summary>
              <div className="hud-shell__group-body">{p.node}</div>
            </details>
          ))}
        </div>
      )}
    </aside>
  );
}
