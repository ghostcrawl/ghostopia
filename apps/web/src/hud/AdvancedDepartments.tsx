// ghostopia web — the Advanced departments opt-in toggle.
//
// The advanced departments search REAL stores using the operator's own key, so they stay OFF
// until the operator switches one on. This panel reads the advanced departments from the
// server-relayed catalog (`advanced` flag) and emits `onToggle(id, enabled)` — the App wires it
// to the authed `workforce.advanced` verb, which starts/stops that department in the live world.
//
// Thin-frontend: this file imports NO GhostCrawl SDK and sends NO key — only the department id
// + the on/off intent. The enabled state is owned by the server (relayed back) and passed in.

import type { JSX } from "react";
import { useStore } from "zustand";

import { catalogStore } from "./catalogStore";

export interface AdvancedDepartmentsProps {
  /** The ids of the advanced departments currently switched on (server-owned intent). */
  enabled: string[];
  /** Emit the on/off intent for one advanced department. */
  onToggle: (id: string, enabled: boolean) => void;
}

/**
 * The opt-in list of advanced real-retail departments. Renders nothing when the catalog has
 * none, so the safe keyless default experience is unchanged until an advanced department ships.
 */
export function AdvancedDepartments({ enabled, onToggle }: AdvancedDepartmentsProps): JSX.Element | null {
  const sections = useStore(catalogStore, (s) => s.sections);
  const advanced = sections.filter((s) => s.advanced);
  if (advanced.length === 0) return null;

  const enabledSet = new Set(enabled);
  return (
    <section className="advanced-depts" aria-label="advanced departments">
      <header className="advanced-depts__head">advanced departments — opt in</header>
      <p className="editor__hint advanced-depts__note">
        These departments search real stores using your own key. They stay off until you switch
        one on.
      </p>
      <ul className="advanced-depts__list">
        {advanced.map((sec) => {
          const on = enabledSet.has(sec.id);
          return (
            <li className="advanced-depts__row" key={sec.id}>
              <span className="advanced-depts__label">{sec.label}</span>
              {sec.category && <span className="advanced-depts__cat">{sec.category}</span>}
              <button
                type="button"
                className={`advanced-depts__toggle${on ? " advanced-depts__toggle--on" : ""}`}
                aria-pressed={on}
                aria-label={`toggle ${sec.label}`}
                onClick={() => onToggle(sec.id, !on)}
              >
                {on ? "on" : "off"}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
