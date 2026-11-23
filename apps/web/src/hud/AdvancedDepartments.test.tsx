// @vitest-environment jsdom
// ghostopia web — AdvancedDepartments opt-in toggle .
//
// The opt-in surface for the ADVANCED real-retail departments: each is OFF by default and the
// operator switches it on explicitly (it searches real stores with the your own key). The
// component reads the advanced departments from `catalogStore` (server-relayed `advanced` flag)
// and emits an `onToggle(id, enabled)` the App wires to the `workforce.advanced` verb. Thin-
// frontend — no SDK, no key.

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdvancedDepartments } from "./AdvancedDepartments";
import { catalogStore, type CatalogSection } from "./catalogStore";

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
  enabled: string[];
  onToggle: (id: string, enabled: boolean) => void;
}): Promise<void> {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root!.render(<AdvancedDepartments enabled={props.enabled} onToggle={props.onToggle} />);
  });
}

function seed(sections: CatalogSection[]): void {
  catalogStore.getState().setSections(sections);
}

const ADV: CatalogSection = {
  id: "haunted-market",
  label: "Haunted Market (advanced)",
  role: "search_and_detail",
  capacity: 3,
  accepts: ["extract"],
  kind: "department",
  advanced: true,
  query: "wireless earbuds",
};
const NORMAL: CatalogSection = {
  id: "best-price",
  label: "Bargain Crypt",
  role: "navigate_and_extract",
  capacity: 3,
  accepts: ["extract"],
  kind: "department",
  advanced: false,
};

describe("AdvancedDepartments", () => {
  it("lists only advanced departments, off by default", async () => {
    seed([ADV, NORMAL]);
    await mount({ enabled: [], onToggle: vi.fn() });
    expect(container!.textContent).toContain("Haunted Market");
    // a non-advanced department is NOT in this opt-in list.
    expect(container!.textContent).not.toContain("Bargain Crypt");
    const btn = container!.querySelector<HTMLButtonElement>("button[aria-label='toggle Haunted Market (advanced)']");
    expect(btn).not.toBeNull();
    expect(btn!.getAttribute("aria-pressed")).toBe("false");
  });

  it("fires onToggle(id, true) when switched on", async () => {
    seed([ADV]);
    const onToggle = vi.fn();
    await mount({ enabled: [], onToggle });
    const btn = container!.querySelector<HTMLButtonElement>("button[aria-pressed]")!;
    await act(async () => btn.click());
    expect(onToggle).toHaveBeenCalledWith("haunted-market", true);
  });

  it("reflects the enabled state and toggles back off", async () => {
    seed([ADV]);
    const onToggle = vi.fn();
    await mount({ enabled: ["haunted-market"], onToggle });
    const btn = container!.querySelector<HTMLButtonElement>("button[aria-pressed]")!;
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    await act(async () => btn.click());
    expect(onToggle).toHaveBeenCalledWith("haunted-market", false);
  });

  it("renders nothing when there are no advanced departments", async () => {
    seed([NORMAL]);
    await mount({ enabled: [], onToggle: vi.fn() });
    expect(container!.querySelector("[aria-label='advanced departments']")).toBeNull();
  });
});
