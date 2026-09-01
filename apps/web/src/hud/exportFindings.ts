// ghostopia web — the "feed your store" export util.
//
// Each department's REAL records download as CSV or JSON, built ENTIRELY client-side from the
// PreviewRow[] the client already holds (thin-frontend: NO GhostCrawl key, NO SDK import, NO
// fetch / server round-trip). The Blob-download plumbing mirrors editor/EditorMode.tsx doExport
// exactly (new Blob → URL.createObjectURL → a.download → a.click() → revokeObjectURL). This is
// the literal dropshipper payoff: real store-ready data (title/price/rating/availability/link/
// image) a shop owner can import.

import type { PreviewRow } from "./resultsStore";

/** The fixed export column order. Also the CSV header. */
export const FINDINGS_COLUMNS = ["title", "price", "rating", "availability", "link", "image"] as const;

/** A record field lookup treating the record as a flat object; "" when absent. */
function field(record: unknown, key: string): string {
  if (record !== null && typeof record === "object") {
    const v = (record as Record<string, unknown>)[key];
    if (v !== undefined && v !== null) return String(v);
  }
  return "";
}

/**
 * CSV cell: neutralize spreadsheet formula injection (CWE-1236) on untrusted scraped data,
 * THEN quote + double any internal quote. Scraped store titles/prices come from arbitrary
 * third-party pages; a cell whose first character is a formula trigger (`= + - @` or a
 * leading tab/CR) is prefixed with a leading apostrophe so Excel/Sheets treats it as text,
 * never evaluates it as a formula. Quote-escaping alone (the old comment) only
 * defends the field delimiter, NOT formula evaluation.
 */
function csvCell(value: string): string {
  const guarded = /^[=+\-@\t\r]/.test(value) ? `'${value}` : value;
  return `"${guarded.replace(/"/g, '""')}"`;
}

/**
 * The export value for one row+column. Reads the record field, but the `link` column falls
 * back to the row's own scraped `url` (the one field always present) so the link column is
 * never blank — the R5 fix: `record.link` was frequently empty → an always-blank CSV column.
 */
function cellValue(r: PreviewRow, col: string): string {
  const v = field(r.record, col);
  if (v) return v;
  if (col === "link" && r.url) return r.url;
  return "";
}

/** Build the CSV text over the fixed columns; header + one quote-escaped line per record. */
export function buildFindingsCsv(rows: PreviewRow[]): string {
  const header = FINDINGS_COLUMNS.join(",");
  const lines = rows.map((r) => FINDINGS_COLUMNS.map((col) => csvCell(cellValue(r, col))).join(","));
  return [header, ...lines].join("\n");
}

/** Build the JSON text: the raw records the ghosts brought back, pretty-printed (2-space). */
export function buildFindingsJson(rows: PreviewRow[]): string {
  return JSON.stringify(rows.map((r) => r.record), null, 2);
}

/**
 * Download the department's findings as `${dept}-findings.${fmt}`. Client-side Blob only —
 * mirrors EditorMode.doExport (no key, no SDK, no fetch). `fmt` doubles as the file extension.
 */
export function downloadFindings(rows: PreviewRow[], dept: string, fmt: "csv" | "json"): void {
  const text = fmt === "csv" ? buildFindingsCsv(rows) : buildFindingsJson(rows);
  const type = fmt === "csv" ? "text/csv" : "application/json";
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${dept}-findings.${fmt}`;
  a.click();
  URL.revokeObjectURL(url);
}
