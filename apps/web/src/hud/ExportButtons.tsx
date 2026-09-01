// ghostopia web — the shared "export findings" control.
//
// One component, two call sites (the DepartmentResults findings card + the Data Graveyard
// per-department head). Renders "export csv" / "export json" buttons that build the file
// client-side via downloadFindings — NO GhostCrawl key, NO SDK, NO server round-trip. Both
// buttons are disabled when the department has zero records so the affordance never lies.

import type { JSX } from "react";

import { downloadFindings } from "./exportFindings";
import type { PreviewRow } from "./resultsStore";

export interface ExportButtonsProps {
  /** the department's records (already held client-side). */
  rows: PreviewRow[];
  /** the department slug used in the download filename (`${dept}-findings.csv`). */
  dept: string;
  /** the display department label, for the button aria-labels. */
  label: string;
}

/** The two-button CSV/JSON export control, disabled when there is nothing to export. */
export function ExportButtons({ rows, dept, label }: ExportButtonsProps): JSX.Element {
  const empty = rows.length === 0;
  return (
    <span className="export-btns">
      <button
        type="button"
        className="export-btn"
        disabled={empty}
        aria-label={`download ${label} findings as csv`}
        onClick={() => downloadFindings(rows, dept, "csv")}
      >
        export csv
      </button>
      <button
        type="button"
        className="export-btn"
        disabled={empty}
        aria-label={`download ${label} findings as json`}
        onClick={() => downloadFindings(rows, dept, "json")}
      >
        export json
      </button>
    </span>
  );
}
