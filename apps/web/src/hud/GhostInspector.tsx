// ghostopia web — the Ghost inspector (STAGE 7 management surface).
//
// For the SELECTED ghost: name / section / current task, plus the runtime controls — a
// BEHAVIOR dropdown populated from the server-relayed `behaviors.list()` (`catalogStore`), a
// SECTION dropdown, pause / resume, and retarget — each emitting the management
// command over the authed WS (NAMES only, applied server-authoritatively). Adding a
// behavior/section needs NO edit here; the dropdowns read the server list. This file imports
// NO GhostCrawl SDK and NO key.

import type { JSX } from "react";
import { useStore } from "zustand";

import { useWorldStore } from "@ghostopia/ghost-renderer";

import type { ManageCommand } from "../liveClient";
import { inspectorStore } from "../inspector/inspectorStore";
import { isSurfaceSafe } from "../surfaceSafe";
import { catalogStore } from "./catalogStore";
import { ghostStateLabel } from "./ghostStateLabel";
import { rosterStore } from "./rosterStore";

/**
 * The Ghost inspector management card. Renders for the open ghost (the one whose live-frame
 * inspector is selected). `onManage` emits the management commands; `onSelect` is not
 * used here (selection lives in the roster/inspector) — this card is purely control.
 */
export function GhostInspector({
  onManage,
}: {
  onManage: (cmd: ManageCommand) => void;
}): JSX.Element {
  const openId = useStore(inspectorStore, (s) => s.openGhostId);
  const ghosts = useStore(rosterStore, (s) => s.ghosts);
  const behaviors = useStore(catalogStore, (s) => s.behaviors);
  const sections = useStore(catalogStore, (s) => s.sections);
  // The SESSION-scoped sanitized persona (server-built whitelist sentence)
  // lives on the world-store ghost. Show it as a customer-safe chip — gated through the TS
  // surface language boundary (omit-not-leak), never a raw UA / engine codename / vendor term.
  const persona = useStore(useWorldStore, (s) =>
    openId ? (s.ghosts[openId]?.persona ?? null) : null,
  );

  if (!openId) return <></>;
  const g = ghosts[openId];
  if (!g) return <></>;
  const safePersona =
    typeof persona === "string" && persona.trim().length > 0 && isSurfaceSafe(persona)
      ? persona
      : null;

  return (
    <section className="ghost-inspector" aria-label={`manage ${g.name}`}>
      <header className="ghost-inspector__head">
        <span className="ghost-inspector__name">{g.name}</span>
        <span className="ghost-inspector__state">{ghostStateLabel(g.state)}</span>
      </header>
      <div className="ghost-inspector__meta">
        <span className="ghost-inspector__section">{g.section || "—"}</span>
        {g.task && <span className="ghost-inspector__task" title={g.task}>{g.task}</span>}
      </div>
      {safePersona && (
        <div className="ghost-inspector__persona" title="session persona">{safePersona}</div>
      )}

      <label className="ghost-inspector__field">
        behavior
        <select
          className="ghost-inspector__select"
          aria-label="behavior"
          value={g.behavior || ""}
          onChange={(e) => {
            const behavior = e.target.value;
            if (behavior) onManage({ command: "assign_behavior", ghostId: g.ghostId, behavior });
          }}
        >
          {!g.behavior && <option value="">choose behavior…</option>}
          {behaviors.map((b) => (
            <option key={b.name} value={b.name}>{b.label}</option>
          ))}
        </select>
      </label>

      <label className="ghost-inspector__field">
        section
        <select
          className="ghost-inspector__select"
          aria-label="section"
          value={g.section || ""}
          onChange={(e) => {
            // Choosing a new section REASSIGNS the ghost — an authoritative move that
            // re-seats its roster AND walks it into the new plot (server re-paths via A*).
            const section = e.target.value;
            if (section) onManage({ command: "reassign", ghostId: g.ghostId, section });
          }}
        >
          {!g.section && <option value="">choose section…</option>}
          {sections.map((s) => (
            <option key={s.id} value={s.id}>{s.id}</option>
          ))}
        </select>
      </label>

      {/* Operator commands: direct the ghost — send to a workstation in its section,
          recall it home, or pause/resume/retarget. Each is an authoritative server verb. */}
      <div className="ghost-inspector__actions">
        <button
          type="button"
          className="ghost-inspector__btn"
          aria-label="send to workstation"
          onClick={() =>
            onManage({
              command: "send_to_workstation",
              ghostId: g.ghostId,
              section: g.section || undefined,
            })
          }
        >
          → station
        </button>
        <button
          type="button"
          className="ghost-inspector__btn"
          aria-label="recall home"
          onClick={() => onManage({ command: "recall", ghostId: g.ghostId })}
        >
          ⌂ recall
        </button>
      </div>
      <div className="ghost-inspector__actions">
        <button
          type="button"
          className="ghost-inspector__btn"
          onClick={() => onManage({ command: "pause", ghostId: g.ghostId })}
        >
          pause
        </button>
        <button
          type="button"
          className="ghost-inspector__btn"
          onClick={() => onManage({ command: "resume", ghostId: g.ghostId })}
        >
          resume
        </button>
        <button
          type="button"
          className="ghost-inspector__btn"
          onClick={() =>
            onManage({ command: "retarget", ghostId: g.ghostId, section: g.section || undefined })
          }
        >
          retarget
        </button>
      </div>
    </section>
  );
}
