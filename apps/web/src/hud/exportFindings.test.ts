// @vitest-environment jsdom
// ghostopia web — the "feed your store" export (Task 1).
//
// Each department's real records must export as CSV AND JSON, built ENTIRELY client-side from
// the PreviewRow[] already held (thin-frontend: no key, no SDK, no server round-trip). These
// tests drive the pure CSV/JSON builders (column order + quote-escaping + missing-field cell +
// records shape), the Blob-download plumbing (filename + createObjectURL/revoke, mirroring
// EditorMode.doExport), and the shared ExportButtons component (disabled when there are zero
// rows). No browser needed — jsdom for the download + static markup for the buttons.

import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildFindingsCsv, buildFindingsJson, downloadFindings, FINDINGS_COLUMNS } from "./exportFindings";
import { ExportButtons } from "./ExportButtons";
import type { PreviewRow } from "./resultsStore";

const ROWS: PreviewRow[] = [
  {
    id: 1,
    taskId: "t1",
    missionId: "m1",
    section: "horror-books",
    url: "https://books.example/a",
    record: {
      title: 'The "Haunting"',
      price: "£12.50",
      rating: "4",
      availability: "In stock",
      link: "https://books.example/a",
      image: "https://books.example/a.jpg",
    },
  },
  {
    id: 2,
    taskId: "t2",
    missionId: "m1",
    section: "horror-books",
    url: "https://books.example/b",
    // deliberately missing rating/availability/image → empty quoted cells.
    record: { title: "Grave Tales", price: "£9.99", link: "https://books.example/b" },
  },
];

describe("buildFindingsCsv (fixed columns + quote-escaping)", () => {
  it("emits the exact header row", () => {
    const csv = buildFindingsCsv(ROWS);
    expect(csv.split("\n")[0]).toBe("title,price,rating,availability,link,image");
    expect(FINDINGS_COLUMNS).toEqual(["title", "price", "rating", "availability", "link", "image"]);
  });

  it("wraps every cell in quotes and doubles internal quotes", () => {
    const lines = buildFindingsCsv(ROWS).split("\n");
    // row 1: title contains a " → doubled + wrapped.
    expect(lines[1]).toBe(
      '"The ""Haunting""","£12.50","4","In stock","https://books.example/a","https://books.example/a.jpg"',
    );
  });

  it("renders a missing field as an empty quoted cell", () => {
    const lines = buildFindingsCsv(ROWS).split("\n");
    // row 2 has no rating/availability/image.
    expect(lines[2]).toBe('"Grave Tales","£9.99","","","https://books.example/b",""');
  });

  it("neutralizes spreadsheet formula injection on untrusted scraped cells (CWE-1236, CR-01)", () => {
    // scraped store titles are untrusted; a leading =/+/-/@ (or tab/CR) is a spreadsheet
    // formula trigger. The cell must be prefixed with a leading apostrophe BEFORE quoting so
    // Excel/Sheets treats it as text, never a formula.
    const rows: PreviewRow[] = [
      {
        id: 7,
        taskId: "t7",
        missionId: "m1",
        section: "haunted-market",
        url: "https://evil.example/p",
        record: {
          title: '=HYPERLINK("http://evil","pwn")',
          price: "-2+3",
          rating: "@SUM(A1:A9)",
          availability: "+cmd|'/c calc'!A1",
          link: "https://evil.example/p",
          image: "\tnasty",
        },
      },
    ];
    const line = buildFindingsCsv(rows).split("\n")[1];
    expect(line).toBe(
      '"\'=HYPERLINK(""http://evil"",""pwn"")","\'-2+3","\'@SUM(A1:A9)","\'+cmd|\'/c calc\'!A1",' +
        '"https://evil.example/p","\'\tnasty"',
    );
  });

  it("falls back the link column to the row url when record.link is absent ()", () => {
    // the record carries no link, but the row was scraped from a real page url — the export's
    // link column must be non-empty (was always blank:).
    const rows: PreviewRow[] = [
      {
        id: 9,
        taskId: "t9",
        missionId: "m1",
        section: "best-price",
        url: "https://store.example/deal",
        record: { title: "A Deal", price: "$4.99", image: "https://store.example/deal.jpg" },
      },
    ];
    const line = buildFindingsCsv(rows).split("\n")[1];
    expect(line).toBe('"A Deal","$4.99","","","https://store.example/deal","https://store.example/deal.jpg"');
  });
});

describe("buildFindingsJson (records shape)", () => {
  it("is JSON.stringify of rows.map(r => r.record), pretty 2-space", () => {
    const json = buildFindingsJson(ROWS);
    expect(json).toBe(JSON.stringify(ROWS.map((r) => r.record), null, 2));
    expect(JSON.parse(json)).toHaveLength(2);
    expect(JSON.parse(json)[0].title).toBe('The "Haunting"');
  });
});

describe("downloadFindings (thin-frontend Blob download)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("names the file {dept}-findings.{ext} and uses createObjectURL/revoke", () => {
    // jsdom's Blob has no .text; capture the constructor payload + type instead.
    const blobs: Array<{ text: string; type: string }> = [];
    const RealBlob = globalThis.Blob;
    vi.stubGlobal("Blob", function (this: unknown, parts: BlobPart[], opts?: BlobPropertyBag) {
      blobs.push({ text: String(parts[0]), type: opts?.type ?? "" });
      return new RealBlob(parts, opts);
    } as unknown as typeof Blob);
    const createSpy = vi
      .spyOn(URL, "createObjectURL")
      .mockImplementation(() => "blob:stub");
    const revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    let captured: HTMLAnchorElement | null = null;
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = realCreate(tag);
      if (tag === "a") captured = el as HTMLAnchorElement;
      return el;
    });

    downloadFindings(ROWS, "horror-books", "csv");
    expect(captured?.download).toBe("horror-books-findings.csv");
    expect(createSpy).toHaveBeenCalledOnce();
    expect(revokeSpy).toHaveBeenCalledOnce();
    expect(clickSpy).toHaveBeenCalledOnce();
    expect(blobs[0].type).toBe("text/csv");
    expect(blobs[0].text).toBe(buildFindingsCsv(ROWS));

    downloadFindings(ROWS, "horror-books", "json");
    expect(captured?.download).toBe("horror-books-findings.json");
    expect(blobs[1].type).toBe("application/json");
    expect(blobs[1].text).toBe(buildFindingsJson(ROWS));
  });
});

describe("ExportButtons (shared two-button component, S4)", () => {
  it("renders enabled export csv / export json buttons when there are rows", () => {
    const html = renderToStaticMarkup(
      createElement(ExportButtons, { rows: ROWS, dept: "horror-books", label: "Horror Books" }),
    );
    expect(html).toContain("export csv");
    expect(html).toContain("export json");
    expect(html).toContain('aria-label="download Horror Books findings as csv"');
    expect(html).toContain('aria-label="download Horror Books findings as json"');
    expect(html).not.toContain("disabled");
  });

  it("disables both buttons when there are zero rows", () => {
    const html = renderToStaticMarkup(
      createElement(ExportButtons, { rows: [], dept: "horror-books", label: "Horror Books" }),
    );
    // both buttons carry the disabled attribute.
    expect(html.match(/disabled/g)?.length).toBe(2);
  });
});
