// ghostopia web — the capability catalog store (STAGE 7 management surface).
//
// The behaviors + sections the server relays over `catalog.behaviors` / `catalog.sections`
// (in response to a `catalog.request` the client sends on connect). The Sections panel + the
// Ghost inspector read their dropdown options from HERE — so adding a behavior/section on the
// server needs NO UI edit. This file imports NO GhostCrawl SDK and NO key.

import { createStore } from "zustand/vanilla";

/** A registered behavior the management surface can assign (NAMES + label only). */
export interface CatalogBehavior {
  name: string;
  label: string;
  kind: string;
}

/** A section the management surface can move a ghost into / target. */
export interface CatalogSection {
  id: string;
  label: string;
  role: string;
  capacity: number;
  accepts: string[];
  // The explicit server section-kind tag: "department" marks a real result repository.
  // The map click-gate trusts THIS tag (never inferred from `targetUrl` presence).
  kind?: string | null;
  // The department's what-to-scrape identity (NAMES/target only). `hasSchema` is a
  // presence flag — the extract_schema body stays server-side (thin-frontend, no key).
  targetUrl?: string | null;
  query?: string | null;
  category?: string | null;
  hasSchema?: boolean;
  // An opt-in ADVANCED real-retail department — off by default (it searches real
  // stores with the operator's own key). The AdvancedDepartments panel offers the toggle.
  advanced?: boolean;
}

export interface CatalogState {
  behaviors: CatalogBehavior[];
  sections: CatalogSection[];
  setBehaviors: (behaviors: CatalogBehavior[]) => void;
  setSections: (sections: CatalogSection[]) => void;
  clear: () => void;
}

export const catalogStore = createStore<CatalogState>((set) => ({
  behaviors: [],
  sections: [],
  setBehaviors: (behaviors) => set({ behaviors }),
  setSections: (sections) => set({ sections }),
  clear: () => set({ behaviors: [], sections: [] }),
}));
