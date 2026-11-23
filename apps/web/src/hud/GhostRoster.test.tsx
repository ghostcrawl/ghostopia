// @vitest-environment jsdom
// ghostopia web — GhostRoster retarget-target test (196 FIX 4).
//
// The roster's "retarget" button previously hardcoded the REMOVED section id "verify"
// (dropped in 194/195). It must now point at a REAL current department read from the relayed
// catalog (`kind === "department"`). Thin-frontend — no SDK, no key.

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ManageCommand } from "../liveClient";
import { catalogStore, type CatalogSection } from "./catalogStore";
import { GhostRoster } from "./GhostRoster";
import { rosterStore } from "./rosterStore";

let container: HTMLElement | null = null;
let root: ReturnType<typeof createRoot> | null = null;

function dept(id: string, label: string): CatalogSection {
  return { id, label, role: "extraction", capacity: 4, accepts: [], kind: "department" };
}

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  container?.remove();
  container = null;
  root = null;
  rosterStore.getState().clear();
  catalogStore.getState().clear();
});

async function mount(onManage: (c: ManageCommand) => void): Promise<HTMLElement> {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root!.render(<GhostRoster onSelect={() => {}} onManage={onManage} />);
  });
  return container;
}

describe("GhostRoster retarget target (196 FIX 4)", () => {
  it("retargets to a REAL department id from the catalog, never the removed 'verify'", async () => {
    rosterStore.getState().seed("g1", "Scout", "horror-books", "navigate_and_extract");
    catalogStore.getState().setSections([
      dept("horror-books", "Horror Books"),
      dept("spooky-masks", "Spooky Masks"),
    ]);
    const onManage = vi.fn();
    const el = await mount(onManage);

    const retargetBtn = Array.from(el.querySelectorAll("button")).find((b) =>
      b.textContent?.startsWith("retarget→"),
    );
    expect(retargetBtn).toBeTruthy();
    // targets a DIFFERENT real department than the ghost's own section, never "verify".
    expect(retargetBtn!.textContent).toContain("Spooky Masks");
    expect(retargetBtn!.textContent).not.toContain("verify");

    await act(async () => {
      retargetBtn!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(onManage).toHaveBeenCalledWith({
      command: "retarget",
      ghostId: "g1",
      section: "spooky-masks",
    });
  });

  it("omits the retarget button entirely when the catalog carries no department", async () => {
    rosterStore.getState().seed("g1", "Scout", "horror-books", "navigate_and_extract");
    catalogStore.getState().setSections([]); // no departments known yet
    const el = await mount(vi.fn());
    const retargetBtn = Array.from(el.querySelectorAll("button")).find((b) =>
      b.textContent?.startsWith("retarget→"),
    );
    expect(retargetBtn).toBeUndefined();
  });
});
