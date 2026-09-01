// ghostopia web — the department-only map click gate + hover-cursor affordance.
//
// Only a DEPARTMENT plot is a result repository: clicking one opens its findings card
// (toggle). Clicking a non-department section (the resting graveyard) or bare ground is
// intentional silence — a no-op, no card, no flash, no toast. The department tag
// is the server-authoritative `kind:"department"` on the catalog section; it is NEVER inferred
// from `target_url` presence client-side. Imports NO SDK and NO key — just the
// section id + the client catalog.

import { catalogStore } from "./catalogStore";
import { sectionFocusStore } from "./sectionFocusStore";

/** True when `id` names a section the server tagged `kind:"department"` (a result repository). */
export function isDepartmentSection(id: string | null): boolean {
  if (!id) return false;
  const section = catalogStore.getState().sections.find((s) => s.id === id);
  return section?.kind === "department";
}

/** Handle a map result-click: open a department's findings card (toggle); everything else is
 *  intentional silence (a non-department section or bare ground is a no-op — S1). */
export function handleSectionClick(id: string | null): void {
  if (isDepartmentSection(id)) {
    sectionFocusStore.getState().toggle(id as string);
  }
  // else: absolute silence — no card, no clear-then-error, no toast.
}

/** The stage cursor over a world section: `pointer` over a department plot (it is clickable
 *  for findings), `grab` over non-department ground (pan affordance) — S1 hover affordance. */
export function cursorForSection(id: string | null): "pointer" | "grab" {
  return isDepartmentSection(id) ? "pointer" : "grab";
}
