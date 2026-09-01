// ghostopia web — the Sections panel (STAGE 7 management surface).
//
// Per-section roster / capacity / role / current target, plus a ROLE dropdown that tunes the
// whole section at runtime by emitting an `assign_behavior` management command for
// every ghost currently on the section's roster — server-authoritative, NAMES only, no key.
// The section list + role options are SERVER-RELAYED (`catalogStore`), so adding a
// section/behavior needs NO UI edit. This file imports NO GhostCrawl SDK and NO key.

import type { JSX } from "react";
import { useStore } from "zustand";

import type { ManageCommand } from "../liveClient";
import { catalogStore } from "./catalogStore";
import { rosterStore, type RosterEntry } from "./rosterStore";

function rosterFor(ghosts: Record<string, RosterEntry>, sectionId: string): RosterEntry[] {
  return Object.values(ghosts).filter((g) => g.section === sectionId);
}

function capacityPct(count: number, capacity: number): string {
  if (capacity <= 0) return "0%";
  return `${Math.round(Math.min(1, count / capacity) * 100)}%`;
}

/**
 * The Sections panel. `onManage` emits the management commands. Changing a section's
 * role dropdown re-roles every ghost on that section's roster (a runtime section-tuning built
 * on the per-ghost `assign_behavior` command).
 */
export function SectionsPanel({
  onManage,
  onSpawn,
  onDespawn,
}: {
  onManage: (cmd: ManageCommand) => void;
  /** Add one ghost into a section (per-section '+' control). */
  onSpawn?: (section: string) => void;
  /** Remove one ghost from a section (per-section '-' control). */
  onDespawn?: (ghostId: string) => void;
}): JSX.Element {
  const sections = useStore(catalogStore, (s) => s.sections);
  const behaviors = useStore(catalogStore, (s) => s.behaviors);
  const ghosts = useStore(rosterStore, (s) => s.ghosts);

  if (sections.length === 0) return <></>;

  return (
    <section className="sections" aria-label="sections">
      <header className="sections__head">sections</header>
      <ul className="sections__list">
        {sections.map((sec) => {
          const roster = rosterFor(ghosts, sec.id);
          const working = roster.filter((g) => g.state !== "IDLE" && g.state !== "RETURNING_HOME");
          const targets = roster.map((g) => g.currentUrl).filter((u): u is string => Boolean(u));
          return (
            <li className="sections__row" key={sec.id}>
              <div className="sections__row-top">
                <span className="sections__name">{sec.id}</span>
                <span className="sections__role">{sec.role}</span>
              </div>
              <div className="sections__cap">
                <div className="sections__cap-bar">
                  <div
                    className="sections__cap-fill"
                    style={{ width: capacityPct(working.length, sec.capacity) }}
                  />
                </div>
                <span className="sections__cap-label">
                  {working.length}/{sec.capacity} · {roster.length} on roster
                </span>
              </div>
              {targets.length > 0 && (
                <div className="sections__target" title={targets[0]}>→ {hostOf(targets[0])}</div>
              )}
              {/* EASY add/remove: a +/- stepper that spawns a ghost into THIS section
                  or removes the last one on its roster — authoritative server verbs. */}
              <div className="sections__stepper" role="group" aria-label={`ghosts in ${sec.id}`}>
                <button
                  type="button"
                  className="sections__step sections__step--rm"
                  aria-label={`remove ghost from ${sec.id}`}
                  disabled={roster.length === 0 || !onDespawn}
                  onClick={() => {
                    const last = roster[roster.length - 1];
                    if (last && onDespawn) onDespawn(last.ghostId);
                  }}
                >
                  −
                </button>
                <span className="sections__step-count" aria-label={`${roster.length} ghosts`}>
                  {roster.length}
                </span>
                <button
                  type="button"
                  className="sections__step sections__step--add"
                  aria-label={`add ghost to ${sec.id}`}
                  disabled={!onSpawn}
                  onClick={() => onSpawn?.(sec.id)}
                >
                  +
                </button>
              </div>
              <label className="sections__role-pick">
                role
                <select
                  className="sections__select"
                  aria-label={`role for ${sec.id}`}
                  value=""
                  onChange={(e) => {
                    const behavior = e.target.value;
                    if (!behavior) return;
                    for (const g of roster) {
                      onManage({ command: "assign_behavior", ghostId: g.ghostId, behavior });
                    }
                  }}
                  disabled={roster.length === 0}
                >
                  <option value="">re-role roster…</option>
                  {behaviors.map((b) => (
                    <option key={b.name} value={b.name}>{b.label}</option>
                  ))}
                </select>
              </label>
            </li>
          );
        })}
      </ul>
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
