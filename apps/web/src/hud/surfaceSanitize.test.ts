// @vitest-environment jsdom
// ghostopia web — the frontend renders only curated, customer-facing copy.
//
// These tests render the customer-facing surfaces with a raw status value injected and assert
// that the client-side `safeSurfaceText` guard holds — the rendered DOM shows only the sanitized
// fallback.

import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import type { Ghost } from "@ghostopia/ghost-renderer";

import { TabBody } from "../inspector/tabs";
import type { InspectorSession } from "../inspector/inspectorStore";
import { isSurfaceSafe } from "../surfaceSafe";

// React needs this flag set to run `act` inside a test environment.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// The DOM must contain NONE of the banned lexicon. We assert through `isSurfaceSafe` — the SAME
// shared client boundary (`surfaceSafe.ts`) the components render behind — rather than a duplicated
// regex here, so the assertion can never drift from the real lexicon (and this test file carries no
// codename literal for the source-language gate to trip on).
function assertSanitized(text: string, where: string): void {
  expect(isSurfaceSafe(text), `a banned term reached the ${where} DOM: ${text}`).toBe(true);
}

// Raw status values a buggy/future server path might try to surface — each is
// in-contract for the client sanitization guard, which must collapse each to the safe fallback.
const RAW_INTERNAL_CODES = ["datadome", "cloudflare challenge", "residential proxy"];

let container: HTMLElement | null = null;
let root: ReturnType<typeof createRoot> | null = null;

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  container?.remove();
  container = null;
  root = null;
});

async function renderTab(tab: string, ghost: Ghost, session: InspectorSession): Promise<HTMLElement> {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root!.render(createElement(TabBody, { tab: tab as never, ghost, session }));
  });
  return container;
}

function ghostWith(persona: string | null): Ghost {
  return { id: "g1", name: "Caretaker 0", x: 0, y: 0, state: "IDLE", persona } as unknown as Ghost;
}

function sessionWith(overrides: Partial<InspectorSession>): InspectorSession {
  return {
    ghostId: "g1",
    currentUrl: null,
    title: null,
    latestFrameRef: null,
    events: [],
    records: [],
    errors: [],
    ...overrides,
  } as InspectorSession;
}

describe(" customer-surface sanitization — no banned term reaches the customer DOM", () => {
  it("scrubs RAW vendor-named error codes on the Errors tab (renders only a safe phrase)", async () => {
    const session = sessionWith({
      errors: RAW_INTERNAL_CODES.map((code, i) => ({ code, retryable: i % 2 === 0, ts: i })),
    });
    const el = await renderTab("Errors", ghostWith(null), session);
    const text = el.textContent ?? "";
    // the client boundary held — not one raw vendor token rendered.
    assertSanitized(text, "Errors");
    // and it did not silently render nothing: the sanitized fallback IS shown (proof of scrub).
    expect(text).toContain("Held at the gate…");
  });

  it("omits a vendor-named persona on the Overview tab (never renders the raw value)", async () => {
    const el = await renderTab(
      "Overview",
      ghostWith("residential proxy · datadome"),
      sessionWith({ currentUrl: "https://example.com/p", title: "Pricing" }),
    );
    const text = el.textContent ?? "";
    assertSanitized(text, "Overview");
    // the surrounding clean overview fields still render (the row is OMITTED, not the whole tab).
    expect(text).toContain("Pricing");
  });

  it("keeps a surface-safe persona when it is clean", async () => {
    const el = await renderTab("Overview", ghostWith("iPhone · Safari · en-US"), sessionWith({}));
    const text = el.textContent ?? "";
    expect(text).toContain("iPhone");
    assertSanitized(text, "Overview");
  });

  it("the sanitized surfaces (error code + persona) never leak across the customer tabs", async () => {
    // a vendor token on BOTH the sanitized surfaces at once — neither may echo it to the DOM.
    const session = sessionWith({
      errors: [{ code: "kasada", retryable: false, ts: 1 }],
      currentUrl: "https://example.com/x",
    });
    for (const tab of ["Overview", "Errors"]) {
      const el = await renderTab(tab, ghostWith("stealth mode · akamai"), session);
      const text = el.textContent ?? "";
      assertSanitized(text, tab);
      if (root) await act(async () => root!.unmount());
      container?.remove();
      root = null;
      container = null;
    }
  });
});
