// @vitest-environment jsdom
// ghostopia web — DepartmentsPanel add/edit/remove (Task 2).
//
// The operator authors a themed scraping DEPARTMENT (label/theme + target_url-OR-query +
// category + extract_schema + placement) and the panel emits the authed-WS section.save /
// section.remove verbs carrying that SectionDef. A server reject (ok:false) surfaces
// its reason and KEEPS the form values. Thin-frontend — no SDK, no key (grep-verified below).

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DepartmentsPanel, buildSectionDef, parseSchema } from "./DepartmentsPanel";
import type { SectionSaveResult } from "./DepartmentsPanel";
import { catalogStore } from "./catalogStore";

let container: HTMLElement | null = null;
let root: ReturnType<typeof createRoot> | null = null;

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  container?.remove();
  container = null;
  root = null;
  catalogStore.getState().clear();
});

async function mount(props: {
  onSave: (s: Record<string, unknown>) => void;
  onRemove: (id: string) => void;
  saveResult?: SectionSaveResult | null;
}): Promise<HTMLElement> {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root!.render(<DepartmentsPanel {...props} />);
  });
  return container;
}

async function rerender(props: {
  onSave: (s: Record<string, unknown>) => void;
  onRemove: (id: string) => void;
  saveResult?: SectionSaveResult | null;
}): Promise<void> {
  await act(async () => {
    root!.render(<DepartmentsPanel {...props} />);
  });
}

async function typeInto(el: HTMLInputElement | HTMLTextAreaElement, value: string): Promise<void> {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")!.set!;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("parseSchema / buildSectionDef (pure assembly)", () => {
  it("parses a newline field list into an extract_schema map", () => {
    expect(parseSchema("title\nprice: number\n\n")).toEqual({ title: "string", price: "number" });
  });

  it("assembles only the ACTIVE target + omits empty optionals", () => {
    const def = buildSectionDef({
      editingId: null,
      label: "Horror Books",
      targetMode: "url",
      targetUrl: "https://books.toscrape.com/x",
      query: "",
      category: "horror",
      schemaText: "title\nprice",
      role: "extraction",
      capacity: 4,
      x: 1,
      y: 2,
      w: 6,
      h: 6,
    });
    expect(def.id).toBe("horror-books");
    expect(def.target_url).toBe("https://books.toscrape.com/x");
    expect(def).not.toHaveProperty("query");
    expect(def.category).toBe("horror");
    expect(def.extract_schema).toEqual({ title: "string", price: "string" });
    expect(def.bounds).toEqual({ x: 1, y: 2, w: 6, h: 6 });
  });
});

describe("DepartmentsPanel add ( )", () => {
  it("filling the add form and Save emits section.save with the assembled SectionDef", async () => {
    const onSave = vi.fn();
    const onRemove = vi.fn();
    const c = await mount({ onSave, onRemove });

    await typeInto(c.querySelector('[aria-label="department label"]') as HTMLInputElement, "Horror Books");
    await typeInto(
      c.querySelector('[aria-label="department target url"]') as HTMLInputElement,
      "https://books.toscrape.com/catalogue/category/books/horror_31/",
    );
    await typeInto(c.querySelector('[aria-label="department schema"]') as HTMLTextAreaElement, "title\nprice");

    const save = c.querySelector(".departments__save") as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    await act(async () => (c.querySelector(".departments__form") as HTMLFormElement).requestSubmit());

    expect(onSave).toHaveBeenCalledTimes(1);
    const def = onSave.mock.calls[0][0] as Record<string, unknown>;
    expect(def.label).toBe("Horror Books");
    expect(def.target_url).toContain("books.toscrape.com");
    expect(def.extract_schema).toEqual({ title: "string", price: "string" });
    expect(def.id).toBe("horror-books");
  });

  it("submit is disabled until a label AND a target are present", async () => {
    const c = await mount({ onSave: vi.fn(), onRemove: vi.fn() });
    const save = c.querySelector(".departments__save") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    await typeInto(c.querySelector('[aria-label="department label"]') as HTMLInputElement, "Just A Label");
    expect(save.disabled).toBe(true); // no target yet
  });
});

describe("DepartmentsPanel remove", () => {
  it("Remove on a department emits section.remove {id}", async () => {
    catalogStore.getState().setSections([
      { id: "horror-books", label: "Horror Books", role: "extraction", capacity: 4, accepts: [], targetUrl: "https://books.toscrape.com/x" },
    ]);
    const onRemove = vi.fn();
    const c = await mount({ onSave: vi.fn(), onRemove });
    const removeBtn = c.querySelector('[aria-label="remove Horror Books"]') as HTMLButtonElement;
    expect(removeBtn).not.toBeNull();
    await act(async () => removeBtn.click());
    expect(onRemove).toHaveBeenCalledWith("horror-books");
  });
});

describe("DepartmentsPanel server reject surfacing", () => {
  it("a section.saved {ok:false, reason} renders the reason and KEEPS the form values", async () => {
    const onSave = vi.fn();
    const onRemove = vi.fn();
    const c = await mount({ onSave, onRemove });
    await typeInto(c.querySelector('[aria-label="department label"]') as HTMLInputElement, "Horror Books");
    await typeInto(
      c.querySelector('[aria-label="department target url"]') as HTMLInputElement,
      "http://127.0.0.1/admin",
    );

    await rerender({ onSave, onRemove, saveResult: { ok: false, reason: "blocked target: loopback address" } });

    const reject = c.querySelector(".departments__reject");
    expect(reject?.textContent).toContain("blocked target");
    // form values retained (not cleared) so the operator can fix + resubmit
    expect((c.querySelector('[aria-label="department label"]') as HTMLInputElement).value).toBe("Horror Books");
    expect((c.querySelector('[aria-label="department target url"]') as HTMLInputElement).value).toBe("http://127.0.0.1/admin");
  });

  it("a section.saved {ok:true} clears the form back to new", async () => {
    const onSave = vi.fn();
    const onRemove = vi.fn();
    const c = await mount({ onSave, onRemove });
    await typeInto(c.querySelector('[aria-label="department label"]') as HTMLInputElement, "Horror Books");
    await rerender({ onSave, onRemove, saveResult: { ok: true, reason: null } });
    expect((c.querySelector('[aria-label="department label"]') as HTMLInputElement).value).toBe("");
  });
});
