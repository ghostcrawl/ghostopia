// ghostopia web — the two-state real activity/data inspector .
//
// State A — api-only (scrape/extract) work: the Activity view is a live feed of the REAL
// records the ghost is filing right now — a 32px thumbnail + title + right-aligned price +
// a "currently on: {url}" line + a "{n} brought back so far" haul summary — NOT raw JSON.
// State B — nav/render work (work_kind !== "api-only"): the live browser frame, UNCHANGED.
// The split is driven by the REAL provider's work_kind; there is no keyless/mock path. These
// tests render the pure tab bodies to static markup and assert the two states never share
// content (an api-only session never renders `.inspector__frame`, and vice-versa).

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Ghost } from "@ghostopia/ghost-renderer";

import { BrowserTab } from "./tabs";
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

describe("inspector State A — api-only activity feed renders real priced records ()", () => {
  it("case A: renders a structured priced record row (thumb + title + price + currently-on + haul), NOT raw JSON", () => {
    const html = renderToStaticMarkup(
      createElement(BrowserTab, {
        ghost: ghost({ workKind: "api-only" }),
        session: session({
          currentUrl: "https://books.toscrape.com/catalogue/page-2.html",
          records: [{ title: "The Haunting of Hill House", price: "£39.25", image: "https://img/x.jpg" }],
        }),
      }),
    );
    // structured priced row (mirrors the DepartmentResults priced row), never the black frame
    expect(html).not.toContain("inspector__frame");
    expect(html).toContain("The Haunting of Hill House");
    expect(html).toContain("£39.25");
    expect(html).toContain("dept-card__row-price"); // reuses the warm-accent price token verbatim
    expect(html).toContain("inspector__record-thumb"); // 32px thumbnail element
    // the current page the ghost is working
    expect(html).toContain("currently on:");
    expect(html).toContain("https://books.toscrape.com/catalogue/page-2.html");
    // haul-so-far summary
    expect(html).toContain("1 brought back so far");
    // NOT the raw `JSON.stringify(r)` dump
    expect(html).not.toContain('{"title"');
  });

  it("case A2: a record with no image renders the quiet placeholder thumb, never a broken-image icon", () => {
    const html = renderToStaticMarkup(
      createElement(BrowserTab, {
        ghost: ghost({ workKind: "api-only" }),
        session: session({ records: [{ title: "No Cover", price: "£1.00" }] }),
      }),
    );
    expect(html).toContain("inspector__record-thumb--missing");
    expect(html).toContain("No Cover");
  });

  it("case B: an empty api-only session renders the corrected empty copy (no stale 'no live view' text)", () => {
    const html = renderToStaticMarkup(
      createElement(BrowserTab, {
        ghost: ghost({ workKind: "api-only" }),
        session: session({}),
      }),
    );
    expect(html).not.toContain("inspector__frame");
    expect(html).toContain("records will stream in here");
    expect(html).not.toContain("no live view for this kind of work");
  });
});

describe("inspector State B — nav/render work keeps the live browser frame, never the records feed ()", () => {
  it("case C: a browser-nav session renders the .inspector__frame (State B), proving A/B never share content", () => {
    const html = renderToStaticMarkup(
      createElement(BrowserTab, {
        ghost: ghost({ workKind: "browser-nav" }),
        session: session({ latestFrameRef: "data:image/png;base64,AAAA" }),
      }),
    );
    expect(html).toContain("inspector__frame");
    expect(html).toContain('src="data:image/png;base64,AAAA"');
    // State B is the live frame — never the records feed/haul summary.
    expect(html).not.toContain("brought back so far");
    expect(html).not.toContain("inspector__record-thumb");
  });
});
