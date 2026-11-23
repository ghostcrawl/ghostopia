// @vitest-environment jsdom
// ghostopia web — MissionForm query/category mission mode (Task 1).
//
// The operator can launch a search-driven (query/category) mission, not only a url list. These
// tests drive the real component in jsdom: switching to query mode enables submit on a non-empty
// query and calls onSubmit with {query, no urls}; url mode with an empty textarea keeps submit
// disabled (regression). Thin-frontend — the form sends NAMES/targets only, never a key.

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MissionForm } from "./MissionForm";
import { catalogStore, type CatalogSection } from "./catalogStore";
import type { MissionFanout } from "../liveClient";

let container: HTMLElement | null = null;
let root: ReturnType<typeof createRoot> | null = null;

const DEPARTMENTS: CatalogSection[] = [
  { id: "horror-books", label: "horror books", role: "extraction", capacity: 6, accepts: ["extract"], kind: "department" },
  { id: "mystery-books", label: "mystery books", role: "extraction", capacity: 6, accepts: ["extract"], kind: "department" },
  { id: "spooky-masks", label: "spooky masks", role: "extraction", capacity: 6, accepts: ["extract"], kind: "department" },
  { id: "spooky-costumes", label: "spooky costumes", role: "extraction", capacity: 6, accepts: ["extract"], kind: "department" },
  { id: "resting", label: "resting", role: "resting", capacity: 99, accepts: [], kind: null },
];

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  container?.remove();
  container = null;
  root = null;
  catalogStore.getState().clear();
});

async function mount(onSubmit: (m: MissionFanout) => void): Promise<HTMLElement> {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root!.render(<MissionForm onSubmit={onSubmit} />);
  });
  return container;
}

/** Set an <input>/<textarea> value the way React tracks it, then dispatch `input`. */
async function typeInto(el: HTMLInputElement | HTMLTextAreaElement, value: string): Promise<void> {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")!.set!;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

/** Click the inactive mode tab to switch into url-list (advanced) mode. */
async function switchToUrlMode(c: HTMLElement): Promise<void> {
  const urlModeBtn = c.querySelector(
    '[aria-label="mission mode"] button[aria-pressed="false"]',
  ) as HTMLButtonElement;
  expect(urlModeBtn.textContent).toContain("url list");
  await act(async () => urlModeBtn.click());
}

describe("MissionForm query/category mode ( )", () => {
  it("defaults to PRODUCT SEARCH: typing a product enables submit and calls onSubmit with {query} and no urls", async () => {
    const onSubmit = vi.fn();
    const c = await mount(onSubmit);

    // product search is the DEFAULT mode — its tab is pressed and the product field is shown.
    const searchTab = c.querySelector('[aria-label="mission mode"] button[aria-pressed="true"]') as HTMLButtonElement;
    expect(searchTab.textContent).toContain("product search");

    const queryInput = c.querySelector(
      '[aria-label="product to find the cheapest offer for"]',
    ) as HTMLInputElement;
    expect(queryInput).not.toBeNull();
    const submit = c.querySelector(".mission-form__submit") as HTMLButtonElement;
    // Flagship example is preloaded — submit is ready on first paint.
    expect(queryInput.value).toBe("Sony WH-1000XM5");
    expect(submit.disabled).toBe(false);

    await typeInto(queryInput, "spooky masks");
    expect(submit.disabled).toBe(false);

    await act(async () => (c.querySelector("form") as HTMLFormElement).requestSubmit());

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const mission = onSubmit.mock.calls[0][0] as MissionFanout;
    expect(mission.query).toBe("spooky masks");
    expect(mission.urls).toEqual([]);
  });

  it("clearing the product field disables submit again", async () => {
    const onSubmit = vi.fn();
    const c = await mount(onSubmit);
    const queryInput = c.querySelector(
      '[aria-label="product to find the cheapest offer for"]',
    ) as HTMLInputElement;
    const submit = c.querySelector(".mission-form__submit") as HTMLButtonElement;
    await typeInto(queryInput, "");
    expect(submit.disabled).toBe(true);
  });

  it("in url mode with an empty textarea, submit stays disabled (regression)", async () => {
    const onSubmit = vi.fn();
    const c = await mount(onSubmit);
    await switchToUrlMode(c);
    const submit = c.querySelector(".mission-form__submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    // no product field in url mode
    expect(c.querySelector('[aria-label="product to find the cheapest offer for"]')).toBeNull();
    expect(c.querySelector('[aria-label="mission urls"]')).not.toBeNull();
  });

  it("url mode still submits a url list (additive — the url path is intact)", async () => {
    const onSubmit = vi.fn();
    const c = await mount(onSubmit);
    await switchToUrlMode(c);
    const urls = c.querySelector('[aria-label="mission urls"]') as HTMLTextAreaElement;
    await typeInto(urls, "https://books.toscrape.com/catalogue/category/books/horror_31/");
    const submit = c.querySelector(".mission-form__submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(false);
    await act(async () => (c.querySelector("form") as HTMLFormElement).requestSubmit());
    const mission = onSubmit.mock.calls[0][0] as MissionFanout;
    expect(mission.urls).toHaveLength(1);
    expect(mission.query).toBeUndefined();
  });

  async function openAdvanced(c: HTMLElement): Promise<void> {
    const toggle = c.querySelector(".mission-form__advanced-toggle") as HTMLButtonElement;
    expect(toggle).not.toBeNull();
    await act(async () => toggle.click());
  }

  it("the entry-section dropdown lists the real departments and defaults to the first (no dead stages)", async () => {
    catalogStore.getState().setSections(DEPARTMENTS);
    const onSubmit = vi.fn();
    const c = await mount(onSubmit);
    await openAdvanced(c);

    const select = c.querySelector('[aria-label="entry section"]') as HTMLSelectElement;
    const ids = Array.from(select.options).map((o) => o.value);
    // Only real, ghost-accepting departments — resting (accepts empty, not a department) is out,
    // and no removed pipeline stage survives.
    expect(ids).toEqual(["horror-books", "mystery-books", "spooky-masks", "spooky-costumes"]);
    for (const dead of ["extraction", "research", "verify", "canvas", "error"]) {
      expect(ids).not.toContain(dead);
    }
    // defaults to the first real department
    expect(select.value).toBe("horror-books");
  });

  it("without a catalog, falls back to the four launch departments (no removed stages)", async () => {
    const onSubmit = vi.fn();
    const c = await mount(onSubmit);
    await openAdvanced(c);
    const select = c.querySelector('[aria-label="entry section"]') as HTMLSelectElement;
    const ids = Array.from(select.options).map((o) => o.value);
    expect(ids).toEqual(["horror-books", "mystery-books", "spooky-masks", "spooky-costumes"]);
    expect(select.value).toBe("horror-books");
  });

  it("a submitted mission carries the selected department as its entrySection", async () => {
    catalogStore.getState().setSections(DEPARTMENTS);
    const onSubmit = vi.fn();
    const c = await mount(onSubmit);
    await switchToUrlMode(c);
    const urls = c.querySelector('[aria-label="mission urls"]') as HTMLTextAreaElement;
    await typeInto(urls, "https://books.toscrape.com/catalogue/category/books/mystery_3/");
    await act(async () => (c.querySelector("form") as HTMLFormElement).requestSubmit());
    const mission = onSubmit.mock.calls[0][0] as MissionFanout;
    expect(["horror-books", "mystery-books", "spooky-masks", "spooky-costumes"]).toContain(mission.entrySection);
  });
});
