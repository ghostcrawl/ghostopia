// ghostopia web — the results store keyed-merge acceptance (stable dedup + append-in-place).
//
// The operator directive: scraped results must UPDATE/APPEND in place — deduped, stable order,
// no jumping — so a growing list stays reviewable. These tests pin the store behaviour that
// makes that true: re-applying the SAME preview twice yields NO duplicate rows and keeps the
// SAME order, and a brand-new record appends at the BOTTOM without moving any existing row.

import { afterEach, describe, expect, it } from "vitest";

import { resultsStore, dedupKey, type PreviewRow } from "./resultsStore";

afterEach(() => resultsStore.getState().clear());

/** A server `result.mission_progress` payload with just the preview array we care about. */
function progress(preview: Array<Record<string, unknown>>): Record<string, unknown> {
  return { mission_id: "m1", progress: { total: 1, completed: 1, failed: 0, records: preview.length }, preview };
}

const R1 = { id: 1, task_id: "t1", mission_id: "m1", section: "horror-books", url: "https://books.example/a", record: { title: "A", price: "£10" } };
const R2 = { id: 2, task_id: "t2", mission_id: "m1", section: "horror-books", url: "https://books.example/b", record: { title: "B", price: "£20" } };
const R3 = { id: 3, task_id: "t3", mission_id: "m1", section: "spooky-masks", url: "https://masks.example/c", record: { title: "C", price: "£30" } };

function keys(rows: PreviewRow[]): string[] {
  return rows.map(dedupKey);
}

describe("resultsStore keyed merge (dedup + append-in-place, no reshuffle)", () => {
  it("re-applying the SAME preview twice yields NO duplicate rows and stable order", () => {
    const s = resultsStore.getState();
    // server sends newest-first (then); the store re-sorts to oldest-first.
    s.applyProgress(progress([R2, R1]));
    const first = resultsStore.getState().preview;
    expect(first.map((r) => r.id)).toEqual([1, 2]); // oldest-first, placed order

    // exact same payload again — must NOT duplicate, must NOT reorder.
    s.applyProgress(progress([R2, R1]));
    const second = resultsStore.getState().preview;
    expect(second).toHaveLength(2);
    expect(second.map((r) => r.id)).toEqual([1, 2]);
    expect(keys(second)).toEqual(keys(first));
  });

  it("a NEW record appends at the bottom without moving existing rows", () => {
    const s = resultsStore.getState();
    s.applyProgress(progress([R1, R2]));
    const before = resultsStore.getState().preview;
    expect(before.map((r) => r.id)).toEqual([1, 2]);

    // a later tick carries the two existing rows PLUS a new one — order stays, new one is last.
    s.applyProgress(progress([R3, R2, R1]));
    const after = resultsStore.getState().preview;
    expect(after).toHaveLength(3);
    expect(after.map((r) => r.id)).toEqual([1, 2, 3]);
    // the first two rows are the SAME identities in the SAME positions (no jump).
    expect(keys(after).slice(0, 2)).toEqual(keys(before));
  });

  it("updates a matched row's fields in place while pinning its first-seen id/position", () => {
    const s = resultsStore.getState();
    s.applyProgress(progress([R1]));
    // the SAME extracted record re-arrives with a fresh DB id and an added field — same
    // dedupKey (section|url|record identical), so it must stay one row at its original slot.
    const R1b = { ...R1, id: 99, task_id: "t1-retry" };
    s.applyProgress(progress([R2, R1b]));
    const rows = resultsStore.getState().preview;
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.id)).toEqual([1, 2]); //  kept its first-seen id 1, not 99
    expect(rows[0].taskId).toBe("t1-retry"); // fields updated in place
  });

  it("carries the row id through toPreview (stable client key)", () => {
    resultsStore.getState().applyProgress(progress([R1]));
    expect(resultsStore.getState().preview[0].id).toBe(1);
  });
});

describe("resultsStore best offers ( winning min-price offer per product)", () => {
  it("applies a best_offers list from the progress payload", () => {
    const s = resultsStore.getState();
    s.applyProgress({
      mission_id: "m1",
      progress: { total: 1, completed: 1, failed: 0, records: 2 },
      preview: [],
      best_offers: [
        {
          product_key: "t:ghost manual",
          title: "Ghost Manual",
          price_raw: "£12.00",
          price_num: 12,
          currency: "GBP",
          link: "https://b.example/p",
          image: "https://b.example/p.jpg",
          source_url: "https://b.example/p",
          section: "best-price",
        },
      ],
    });
    const best = resultsStore.getState().bestOffers;
    expect(best).toHaveLength(1);
    expect(best[0].priceNum).toBe(12);
    expect(best[0].link).toBe("https://b.example/p");
    expect(best[0].sourceUrl).toBe("https://b.example/p");
    expect(best[0].title).toBe("Ghost Manual");
  });

  it("leaves prior best offers untouched when a tick omits best_offers", () => {
    const s = resultsStore.getState();
    s.applyProgress({ mission_id: "m1", progress: {}, best_offers: [
      { product_key: "k", title: "X", price_raw: "$1", price_num: 1, currency: "USD", link: "u", image: "", source_url: "u", section: "s" },
    ] });
    s.applyProgress({ mission_id: "m1", progress: {}, preview: [] });
    expect(resultsStore.getState().bestOffers).toHaveLength(1);
  });

  it("clear() empties best offers", () => {
    resultsStore.getState().applyProgress({ mission_id: "m1", progress: {}, best_offers: [
      { product_key: "k", title: "X", price_raw: "$1", price_num: 1, currency: "USD", link: "u", image: "", source_url: "u", section: "s" },
    ] });
    resultsStore.getState().clear();
    expect(resultsStore.getState().bestOffers).toEqual([]);
  });
});
