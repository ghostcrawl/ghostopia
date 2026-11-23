// ghostopia web — Ghost-type-variant inspector + sanitized persona chip (Task 1).
//
// The inspector Browser tab must branch on the ghost's work-kind (server contract,
//  client field): a browser-navigation ghost renders the live `.inspector__frame`
// (with a last-screenshot + on-theme placeholder fallback); an API-only ghost renders an
// activity/records view — NEVER the misleading black frame box. The overview grows a
// sanitized "Session" persona row that is OMITTED (never a raw/fallback leak) when the
// persona fails the TS surface-language gate. These tests render the pure tab bodies to
// static markup (no store wiring) and assert the type-variant + omit-not-leak contract.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Ghost } from "@ghostopia/ghost-renderer";

import { BrowserTab, OverviewTab, browserTabLabel } from "./tabs";
import type { InspectorSession } from "./inspectorStore";

function ghost(patch: Partial<Ghost>): Ghost {
  return { id: "g1", name: "Mold", state: "AT_WORKSTATION", ...patch } as Ghost;
}

function session(patch: Partial<InspectorSession>): InspectorSession {
  return {
    ghostId: "g1",
    currentUrl: null,
    title: null,
    latestFrameRef: null,
    events: [],
    records: [],
    errors: [],
    ...patch,
  };
}

describe("GhostInspector type-variant Browser tab ()", () => {
  it("browser-nav ghost with a live frame renders the photographic .inspector__frame", () => {
    const html = renderToStaticMarkup(
      createElement(BrowserTab, {
        ghost: ghost({ workKind: "browser-nav" }),
        session: session({ latestFrameRef: "data:image/png;base64,AAAA" }),
      }),
    );
    expect(html).toContain("inspector__frame");
    expect(html).toContain('src="data:image/png;base64,AAAA"');
  });

  it("browser-nav ghost with NO live frame shows the on-theme 'No live view yet…' placeholder", () => {
    const html = renderToStaticMarkup(
      createElement(BrowserTab, {
        ghost: ghost({ workKind: "browser-nav" }),
        session: session({ latestFrameRef: null }),
      }),
    );
    expect(html).not.toContain("inspector__frame");
    expect(html).toContain("inspector__placeholder");
    expect(html).toContain("No live view yet");
  });

  it("api-only ghost renders an activity/records view, NEVER the black .inspector__frame box", () => {
    const withRecords = renderToStaticMarkup(
      createElement(BrowserTab, {
        ghost: ghost({ workKind: "api-only" }),
        session: session({ records: [{ title: "a finding" }] }),
      }),
    );
    expect(withRecords).not.toContain("inspector__frame");
    expect(withRecords).toContain("inspector__records");

    const noRecords = renderToStaticMarkup(
      createElement(BrowserTab, {
        ghost: ghost({ workKind: "api-only" }),
        session: session({}),
      }),
    );
    expect(noRecords).not.toContain("inspector__frame");
    // on-theme empty copy : a live view DOES exist for api-only work now — it is
    // the records feed, just not a browser frame — so the stale "no live view" wording is gone.
    expect(noRecords).toContain("records will stream in here");
    expect(noRecords).not.toContain("no live view");
  });

  it("browser-nav ghost with NO frame but an honest viewReason shows the REASON, not the bare placeholder (R7)", () => {
    const html = renderToStaticMarkup(
      createElement(BrowserTab, {
        ghost: ghost({ workKind: "browser-nav" }),
        session: session({
          latestFrameRef: null,
          viewReason: "Waking a browser for this ghost — the live view opens in a moment…",
        }),
      }),
    );
    expect(html).not.toContain("inspector__frame");
    expect(html).toContain("Waking a browser for this ghost");
    // the eternal "No live view yet…" placeholder is REPLACED by the honest reason.
    expect(html).not.toContain("No live view yet");
  });

  it("a leaky viewReason is dropped (never rendered) — omit-not-leak (R7)", () => {
    const html = renderToStaticMarkup(
      createElement(BrowserTab, {
        ghost: ghost({ workKind: "browser-nav" }),
        session: session({
          latestFrameRef: null,
          viewReason: "cloudflare challenge on the residential proxy",
        }),
      }),
    );
    expect(html).not.toContain("cloudflare");
    expect(html).not.toContain("proxy");
    // falls back to the on-theme placeholder rather than leaking the banned reason.
    expect(html).toContain("No live view yet");
  });

  it("a browser-nav (live-browsable) ghost carries a watchable affordance (R7)", () => {
    const html = renderToStaticMarkup(
      createElement(BrowserTab, {
        ghost: ghost({ workKind: "browser-nav" }),
        session: session({ latestFrameRef: "data:image/png;base64,AAAA" }),
      }),
    );
    expect(html).toContain("inspector__watchable");
    // an api-only ghost is NOT live-browsable → no watchable affordance.
    const apiHtml = renderToStaticMarkup(
      createElement(BrowserTab, {
        ghost: ghost({ workKind: "api-only" }),
        session: session({ records: [{ title: "x" }] }),
      }),
    );
    expect(apiHtml).not.toContain("inspector__watchable");
  });

  it("labels the Browser tab 'Activity' for an api-only ghost, 'Browser' otherwise", () => {
    expect(browserTabLabel("api-only")).toBe("Activity");
    expect(browserTabLabel("browser-nav")).toBe("Browser");
    expect(browserTabLabel(null)).toBe("Browser");
    expect(browserTabLabel(undefined)).toBe("Browser");
  });
});

describe("GhostInspector sanitized persona chip ()", () => {
  it("renders a surface-safe persona as a 'Session' overview row", () => {
    const html = renderToStaticMarkup(
      createElement(OverviewTab, {
        ghost: ghost({ persona: "browsing as a Chrome-class desktop from a US locale" }),
        session: session({}),
      }),
    );
    expect(html).toContain("Session");
    expect(html).toContain("browsing as a Chrome-class desktop from a US locale");
  });

  it("OMITS the persona row when the persona fails the surface-language gate (no leak)", () => {
    const leaky = "running behind a residential proxy";
    const html = renderToStaticMarkup(
      createElement(OverviewTab, {
        ghost: ghost({ persona: leaky }),
        session: session({}),
      }),
    );
    expect(html).not.toContain(leaky);
    expect(html).not.toContain("residential");
    expect(html).not.toContain("proxy");
    expect(html).not.toContain("Session");
  });

  it("OMITS the persona row entirely when no persona is present", () => {
    const html = renderToStaticMarkup(
      createElement(OverviewTab, { ghost: ghost({}), session: session({}) }),
    );
    expect(html).not.toContain("Session");
  });
});
