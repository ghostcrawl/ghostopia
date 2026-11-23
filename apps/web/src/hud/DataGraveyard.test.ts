// @vitest-environment jsdom
// ghostopia web — Data Graveyard accessible results click-through (Task 2).
//
// The workforce must read as REAL capability: the found results already land in the Data
// Graveyard, and clicking a completed mission must be a real, keyboard-accessible affordance
// that reveals THAT mission's extracted preview rows (the full work), and the ghost → inspector
// "Data" tab path must surface the real `.inspector__records`. These tests drive the pure
// filter helper + static markup (a11y attributes) + a jsdom click (reveal behaviour), plus the
// Data-tab records path — no new modal, reusing the established surfaces.

import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import { DataGraveyard, previewForMission, previewForSection } from "./DataGraveyard";
import { resultsStore } from "./resultsStore";
import type { PreviewRow } from "./resultsStore";
import { catalogStore } from "./catalogStore";
import { DataTab } from "../inspector/tabs";
import type { InspectorSession } from "../inspector/inspectorStore";

afterEach(() => {
  resultsStore.getState().clear();
  catalogStore.getState().clear();
});

function seed(): void {
  resultsStore.setState({
    missions: {},
    sections: [],
    completedMissions: [
      { id: "m1", title: "Harvest listings", progress: { total: 2, completed: 2, failed: 0, records: 2 } },
      { id: "m2", title: "Map the crypt", progress: { total: 1, completed: 1, failed: 0, records: 1 } },
    ],
    preview: [
      { id: 1, taskId: "t1", missionId: "m1", url: "https://shop.example/a", record: { title: "A" } },
      { id: 2, taskId: "t2", missionId: "m2", url: "https://map.example/b", record: { title: "B" } },
    ],
  });
}

describe("previewForMission ( per-mission results filter)", () => {
  const rows: PreviewRow[] = [
    { id: 1, taskId: "t1", missionId: "m1", url: "u1", record: 1 },
    { id: 2, taskId: "t2", missionId: "m2", url: "u2", record: 2 },
    { id: 3, taskId: "t3", missionId: "m1", url: "u3", record: 3 },
  ];
  it("filters preview rows to the given mission", () => {
    expect(previewForMission(rows, "m1").map((r) => r.taskId)).toEqual(["t1", "t3"]);
    expect(previewForMission(rows, "m2").map((r) => r.taskId)).toEqual(["t2"]);
  });
  it("falls back to ALL rows when no row carries the mission id (legacy envelope)", () => {
    expect(previewForMission(rows, "unknown")).toHaveLength(3);
  });
});

describe("previewForSection ( per-department results filter)", () => {
  const rows: PreviewRow[] = [
    { id: 1, taskId: "t1", missionId: "m1", section: "spooky-masks", url: "u1", record: 1 },
    { id: 2, taskId: "t2", missionId: "m2", section: "horror-books", url: "u2", record: 2 },
    { id: 3, taskId: "t3", missionId: "m1", section: "spooky-masks", url: "u3", record: 3 },
  ];
  it("filters preview rows to the given department (section)", () => {
    expect(previewForSection(rows, "spooky-masks").map((r) => r.taskId)).toEqual(["t1", "t3"]);
    expect(previewForSection(rows, "horror-books").map((r) => r.taskId)).toEqual(["t2"]);
  });
  it("returns [] for a department with no matching rows (no fallback — grouping is exact)", () => {
    expect(previewForSection(rows, "unknown")).toHaveLength(0);
  });
});

function seedDepartments(): void {
  catalogStore.getState().setSections([
    { id: "spooky-masks", label: "Spooky Masks", role: "extraction", capacity: 4, accepts: [] },
    { id: "horror-books", label: "Horror Books", role: "extraction", capacity: 4, accepts: [] },
  ]);
  resultsStore.setState({
    missions: {},
    sections: [],
    completedMissions: [
      { id: "m1", title: "Harvest listings", progress: { total: 2, completed: 2, failed: 0, records: 2 } },
    ],
    preview: [
      { id: 1, taskId: "t1", missionId: "m1", section: "spooky-masks", url: "https://shop.example/a", record: { title: "Ghost Mask", price: "£9.99" } },
      { id: 2, taskId: "t2", missionId: "m1", section: "horror-books", url: "https://books.example/b", record: { title: "The Haunting", price: "£12.50" } },
    ],
  });
}

async function mountDepartments(): Promise<{ container: HTMLElement; unmount: () => Promise<void> }> {
  seedDepartments();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(createElement(DataGraveyard));
  });
  return {
    container,
    unmount: async () => {
      await act(async () => root.unmount());
      container.remove();
    },
  };
}

describe("Data Graveyard department grouping ( per-department priced list)", () => {
  it("switching to the department view renders one group per department with its records", async () => {
    const { container, unmount } = await mountDepartments();
    const toggle = container.querySelector('.graveyard__view-toggle[data-view="department"]') as HTMLElement;
    expect(toggle).not.toBeNull();
    await act(async () => toggle.click());

    const groups = container.querySelectorAll(".graveyard__dept-group");
    expect(groups.length).toBe(2);
    // header uses the catalog department label, not the raw internal id.
    const text = container.textContent ?? "";
    expect(text).toContain("Spooky Masks");
    expect(text).toContain("Horror Books");
    expect(text).not.toContain("spooky-masks");
    // priced list surfaces title + price from the real records.
    expect(text).toContain("Ghost Mask");
    expect(text).toContain("£9.99");
    expect(text).toContain("The Haunting");
    expect(text).toContain("£12.50");
    await unmount();
  });

  it("keeps the mission grouping available (additive)", async () => {
    const { container, unmount } = await mountDepartments();
    // default view is by-mission: the mission affordance is present.
    expect(container.querySelector('.graveyard__mission[role="button"]')).not.toBeNull();
    await unmount();
  });
});

function seedBestOffers(): void {
  catalogStore.getState().setSections([
    { id: "best-price", label: "Best Price", role: "extraction", capacity: 4, accepts: [] },
  ]);
  resultsStore.setState({
    missions: {},
    sections: [],
    completedMissions: [],
    bestOffers: [
      {
        productKey: "t:ghost manual",
        title: "Ghost Manual",
        priceRaw: "£12.00",
        priceNum: 12,
        currency: "GBP",
        link: "https://b.example/p",
        image: "https://b.example/p.jpg",
        sourceUrl: "https://b.example/p",
        section: "best-price",
      },
    ],
    preview: [
      { id: 1, taskId: "t1", missionId: "m1", section: "best-price", url: "https://a.example/p", record: { title: "Ghost Manual", price: "£20.00" } },
      { id: 2, taskId: "t2", missionId: "m1", section: "best-price", url: "https://b.example/p", record: { title: "Ghost Manual", price: "£12.00" } },
    ],
  });
}

describe("Data Graveyard best-offer badge (: the winning min-price row is marked)", () => {
  it("marks ONLY the preview row that is the winning best offer with a 'best' badge", async () => {
    seedBestOffers();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => root.render(createElement(DataGraveyard)));
    const toggle = container.querySelector('.graveyard__view-toggle[data-view="department"]') as HTMLElement;
    await act(async () => toggle.click());

    const badges = container.querySelectorAll(".graveyard__best-badge");
    expect(badges.length).toBe(1);
    // the badge sits on the £12.00 (b.example) row, not the £20.00 (a.example) row.
    const bestRow = badges[0].closest(".graveyard__priced-row");
    expect(bestRow?.textContent).toContain("£12.00");

    await act(async () => root.unmount());
    container.remove();
  });
});

async function mount(): Promise<{ container: HTMLElement; unmount: () => Promise<void> }> {
  seed();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(createElement(DataGraveyard));
  });
  return {
    container,
    unmount: async () => {
      await act(async () => root.unmount());
      container.remove();
    },
  };
}

describe("Data Graveyard mission-row affordance ( a11y)", () => {
  it("renders each mission row as a real keyboard-activatable button", async () => {
    const { container, unmount } = await mount();
    const rows = container.querySelectorAll('.graveyard__mission[role="button"]');
    expect(rows.length).toBe(2);
    for (const row of rows) {
      expect(row.getAttribute("tabindex")).toBe("0");
      expect(row.getAttribute("aria-expanded")).toBe("false");
    }
    await unmount();
  });

  it("clicking a mission reveals THAT mission's preview rows", async () => {
    const { container, unmount } = await mount();
    // before click: no expanded per-mission preview list
    expect(container.querySelector(".graveyard__mission-preview")).toBeNull();
    const firstMission = container.querySelector('.graveyard__mission[role="button"]') as HTMLElement;
    expect(firstMission).not.toBeNull();
    await act(async () => {
      firstMission.click();
    });
    const revealed = container.querySelector(".graveyard__mission-preview");
    expect(revealed).not.toBeNull();
    expect(firstMission.getAttribute("aria-expanded")).toBe("true");
    // it shows m1's row (shop.example), not m2's
    expect(revealed?.textContent).toContain("shop.example");
    expect(revealed?.textContent).not.toContain("map.example");
    await unmount();
  });

  it("is keyboard-activatable via Enter", async () => {
    const { container, unmount } = await mount();
    const firstMission = container.querySelector('.graveyard__mission[role="button"]') as HTMLElement;
    await act(async () => {
      firstMission.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
      );
    });
    expect(container.querySelector(".graveyard__mission-preview")).not.toBeNull();
    await unmount();
  });
});

describe("Data Graveyard data-preview is stable across re-applied progress (dedup + append)", () => {
  async function mountLive(): Promise<{ container: HTMLElement; unmount: () => Promise<void> }> {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(createElement(DataGraveyard));
    });
    return {
      container,
      unmount: async () => {
        await act(async () => root.unmount());
        container.remove();
      },
    };
  }

  function progress(preview: Array<Record<string, unknown>>): Record<string, unknown> {
    return { mission_id: "m1", progress: { total: 1, completed: 1, failed: 0, records: preview.length }, preview };
  }
  const A = { id: 1, task_id: "t1", mission_id: "m1", url: "https://shop.example/a", record: { title: "A" } };
  const B = { id: 2, task_id: "t2", mission_id: "m1", url: "https://shop.example/b", record: { title: "B" } };

  it("re-applying the same preview does not duplicate rendered rows; a new record appends last", async () => {
    const { container, unmount } = await mountLive();
    await act(async () => resultsStore.getState().applyProgress(progress([B, A])));
    let rows = container.querySelectorAll(".graveyard__preview .graveyard__preview-row");
    expect(rows.length).toBe(2);

    // same payload again → still 2 rows (no duplicates), same order (oldest-first).
    await act(async () => resultsStore.getState().applyProgress(progress([B, A])));
    rows = container.querySelectorAll(".graveyard__preview .graveyard__preview-row");
    expect(rows.length).toBe(2);

    // a later tick adds one new record → 3 rows, the newcomer last.
    const C = { id: 3, task_id: "t3", mission_id: "m1", url: "https://shop.example/c", record: { title: "C" } };
    await act(async () => resultsStore.getState().applyProgress(progress([C, B, A])));
    rows = container.querySelectorAll(".graveyard__preview .graveyard__preview-row");
    expect(rows.length).toBe(3);
    expect(rows[2].textContent).toContain("shop.example");
    await unmount();
  });
});

describe("ghost → inspector Data tab surfaces the real records (, no new modal)", () => {
  it("DataTab renders .inspector__records from the session records", () => {
    const session: InspectorSession = {
      ghostId: "g1",
      currentUrl: null,
      title: null,
      latestFrameRef: null,
      events: [],
      records: [{ title: "found" }],
      errors: [],
    };
    const html = renderToStaticMarkup(createElement(DataTab, { session }));
    expect(html).toContain("inspector__records");
    expect(html).toContain("found");
  });
});

describe("Data Graveyard W6 verdict + item-keyed best badge ()", () => {
  async function renderDept(): Promise<{ container: HTMLElement; done: () => Promise<void> }> {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => root.render(createElement(DataGraveyard)));
    const toggle = container.querySelector('.graveyard__view-toggle[data-view="department"]') as HTMLElement;
    await act(async () => toggle.click());
    return { container, done: async () => { await act(async () => root.unmount()); container.remove(); } };
  }
  const listing = "https://shop.example/headphones";
  const rows: PreviewRow[] = [
    { id: 1, taskId: "t1", missionId: "m1", section: "best-price", url: listing, record: { title: "Sony WH-1000XM5", price: "$279", link: `${listing}/xm5` } },
    { id: 2, taskId: "t1", missionId: "m1", section: "best-price", url: listing, record: { title: "Case for Sony WH-1000XM5", price: "$12", link: `${listing}/case` } },
  ];

  it("badges exactly the verdict's item — not every product sharing the listing url", async () => {
    catalogStore.getState().setSections([{ id: "best-price", label: "Best Price", role: "extraction", capacity: 4, accepts: [] }]);
    resultsStore.setState({
      missions: {}, sections: [], completedMissions: [], preview: rows,
      bestOffers: [],
      verdicts: [{ section: "best-price", query: "Sony WH-1000XM5", status: "matched", bestLink: `${listing}/xm5`, bestTitle: "Sony WH-1000XM5", bestPriceRaw: "$279", candidates: 2, matched: 1 }],
    });
    const { container, done } = await renderDept();
    const badges = container.querySelectorAll(".graveyard__best-badge");
    expect(badges.length).toBe(1);
    expect(badges[0].closest(".graveyard__priced-row")?.textContent).toContain("$279");
    expect(container.querySelector(".graveyard__no-match")).toBeNull();
    await done();
  });

  it("shows 'no exact match (N candidates)' and NO best badge when nothing matched", async () => {
    resultsStore.setState({
      missions: {}, sections: [], completedMissions: [], preview: rows,
      bestOffers: [{ productKey: "t:x", title: "Case for Sony WH-1000XM5", priceRaw: "$12", priceNum: 12, currency: "USD", link: `${listing}/case`, image: null, sourceUrl: listing, section: "best-price" }],
      verdicts: [{ section: "best-price", query: "Sony WH-1000XM6", status: "no_exact_match", bestLink: null, bestTitle: null, bestPriceRaw: null, candidates: 2, matched: 0 }],
    });
    const { container, done } = await renderDept();
    expect(container.querySelectorAll(".graveyard__best-badge").length).toBe(0);
    expect(container.querySelector(".graveyard__no-match")?.textContent).toBe("no exact match (2 candidates)");
    await done();
  });
});
