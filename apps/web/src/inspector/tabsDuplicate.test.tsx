// ghostopia web — regression for the DUPLICATE "Activity" tab defect (, live 2026-08-30).
//
// An api-only ghost's "Browser" tab slot is RELABELED to "Activity" (browserTabLabel) and its
// body already surfaces the activity/records feed (with an event-log fallback). A separate
// static "Activity" tab therefore rendered a SECOND button reading "Activity" — two identical
// labels. `visibleTabs` drops the redundant static "Activity" key for api-only ghosts, so the
// label appears exactly once. Browser-navigation ghosts keep both (live frame vs event log are
// distinct surfaces).

import { describe, expect, it } from "vitest";

import { browserTabLabel, visibleTabs, type TabName } from "./tabs";

/** Render the labels each visible tab would display, mirroring InspectorPanel's nav. */
function renderedLabels(workKind: "api-only" | "browser-nav"): string[] {
  return visibleTabs(workKind).map((t: TabName) =>
    t === "Browser" ? browserTabLabel(workKind) : t,
  );
}

describe("D2 — duplicate Activity tab", () => {
  it("api-only ghost renders the 'Activity' label EXACTLY once", () => {
    const labels = renderedLabels("api-only");
    const activityCount = labels.filter((l) => l === "Activity").length;
    expect(activityCount).toBe(1);
    // the redundant static "Activity" key is dropped; Browser→Activity remains
    expect(visibleTabs("api-only")).not.toContain("Activity");
    expect(labels).toEqual(["Overview", "Activity", "Data", "Errors"]);
  });

  it("browser-nav ghost keeps all five tabs — Browser and Activity are distinct", () => {
    const labels = renderedLabels("browser-nav");
    expect(labels).toEqual(["Overview", "Browser", "Activity", "Data", "Errors"]);
    expect(labels.filter((l) => l === "Activity").length).toBe(1);
  });

  it("an unknown/absent work-kind defaults to the full browser-nav tab set", () => {
    expect(visibleTabs(null)).toEqual(["Overview", "Browser", "Activity", "Data", "Errors"]);
    expect(visibleTabs(undefined)).toEqual([
      "Overview",
      "Browser",
      "Activity",
      "Data",
      "Errors",
    ]);
  });

  it("no ghost work-kind ever yields two buttons with the same label", () => {
    for (const wk of ["api-only", "browser-nav"] as const) {
      const labels = renderedLabels(wk);
      expect(new Set(labels).size).toBe(labels.length);
    }
  });
});
