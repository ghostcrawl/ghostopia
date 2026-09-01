// ghostopia web — the focused-department store (196).
//
// Holds which department (section) the operator clicked ON THE MAP to read its findings. The
// business model: a department plot is a RESULT REPOSITORY — clicking it opens a floating card
// of the real records that department's ghosts brought back. This store is the tiny bridge
// between the canvas click (RenderLoop `onSelectSection`) and the React `DepartmentResults`
// card. It imports NO SDK and NO key — just a section id.

import { createStore } from "zustand/vanilla";

export interface SectionFocusState {
  /** the department (section) whose findings card is open, or null (closed). */
  focused: string | null;
  /** open a department's findings card (click on its map plot). */
  focus: (sectionId: string) => void;
  /** toggle a department: clicking the already-open one closes it (a natural on/off tap). */
  toggle: (sectionId: string) => void;
  /** close the findings card (click bare ground / the card's ✕ / Esc). */
  clear: () => void;
}

export const sectionFocusStore = createStore<SectionFocusState>((set) => ({
  focused: null,
  focus: (sectionId) => set({ focused: sectionId }),
  toggle: (sectionId) => set((s) => ({ focused: s.focused === sectionId ? null : sectionId })),
  clear: () => set({ focused: null }),
}));
