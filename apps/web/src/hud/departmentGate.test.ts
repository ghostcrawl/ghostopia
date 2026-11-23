// ghostopia web — department-only click gate + hover-cursor affordance .
//
// Only a DEPARTMENT plot (server-tagged kind:"department") opens a findings card; a
// non-department section (resting) or bare ground is intentional silence (a no-op). The pointer
// shows :pointer over a department and :grab over non-department ground. Drives the pure gate
// helpers against a seeded catalog + the section-focus store — no canvas, no SDK.

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { catalogStore, type CatalogSection } from "./catalogStore";
import {
  cursorForSection,
  handleSectionClick,
  isDepartmentSection,
} from "./departmentGate";
import { sectionFocusStore } from "./sectionFocusStore";

const _department: CatalogSection = {
  id: "horror-books",
  label: "Horror Books",
  role: "navigate_and_extract",
  capacity: 3,
  accepts: ["extract"],
  kind: "department",
  targetUrl: "https://books.toscrape.com/catalogue/category/books/horror_31/index.html",
  hasSchema: true,
};

const _resting: CatalogSection = {
  id: "resting",
  label: "resting",
  role: "idle_wander",
  capacity: 8,
  accepts: [],
  kind: null,
};

beforeEach(() => {
  catalogStore.getState().setSections([_department, _resting]);
  sectionFocusStore.getState().clear();
});

afterEach(() => {
  catalogStore.getState().clear();
  sectionFocusStore.getState().clear();
});

describe("click gate — isDepartmentSection", () => {
  it("is true only for a section the server tagged kind:'department'", () => {
    expect(isDepartmentSection("horror-books")).toBe(true);
    expect(isDepartmentSection("resting")).toBe(false); // non-department ground
    expect(isDepartmentSection("unknown")).toBe(false); // not in the catalog
    expect(isDepartmentSection(null)).toBe(false); // bare ground
  });
});

describe("click gate — handleSectionClick", () => {
  it("Test A: a department click opens (toggles) its findings card", () => {
    handleSectionClick("horror-books");
    expect(sectionFocusStore.getState().focused).toBe("horror-books");
    // re-clicking the open department closes it (natural on/off tap).
    handleSectionClick("horror-books");
    expect(sectionFocusStore.getState().focused).toBe(null);
  });

  it("Test B: a non-department or null click is intentional silence (no-op)", () => {
    // open a department first so we can prove ground clicks do NOT clear it.
    handleSectionClick("horror-books");
    expect(sectionFocusStore.getState().focused).toBe("horror-books");

    handleSectionClick("resting"); // non-department section
    expect(sectionFocusStore.getState().focused).toBe("horror-books"); // unchanged — no clear
    handleSectionClick(null); // bare ground
    expect(sectionFocusStore.getState().focused).toBe("horror-books"); // still unchanged
  });
});

describe("hover affordance — cursorForSection", () => {
  it("Test C: pointer over a department, grab over non-department ground", () => {
    expect(cursorForSection("horror-books")).toBe("pointer");
    expect(cursorForSection("resting")).toBe("grab");
    expect(cursorForSection(null)).toBe("grab");
  });
});
