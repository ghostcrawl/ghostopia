// ghostopia web — the results / mission-progress store (STAGE 7, the Data Graveyard source).
//
// Fed by the server's `mission.created` (title/total) + `result.mission_progress` envelopes
// (progress rollup + a data preview + per-section throughput + completed missions), all
// computed from the REAL persisted SQLite result store. The Data Graveyard + the global
// dashboard render straight from here — records come from real extraction, not a canned
// counter (REAL-NOT-MOCK). This file imports NO GhostCrawl SDK and NO key.

import { createStore } from "zustand/vanilla";

/** A mission's progress rollup (from the DB: task counts + summed real records). */
export interface MissionProgress {
  total: number;
  completed: number;
  failed: number;
  records: number;
}

/** One extracted record for the data-preview table. */
export interface PreviewRow {
  /** The result row's monotonic DB primary key — the stable merge/sort key (append-in-place). */
  id: number;
  taskId: string | null;
  missionId: string | null;
  /** The department (section) the record was scraped in — for per-department grouping. */
  section?: string;
  url: string | null;
  record: unknown;
}

/**
 * The stable identity of a preview row by CONTENT (section + url + record), independent of the
 * DB id — the dedup + React key. Two payloads describing the same extracted record collapse to
 * one row; a genuinely new record gets its own key and appends.
 */
export function dedupKey(r: PreviewRow): string {
  return `${r.section ?? ""}|${r.url ?? ""}|${JSON.stringify(r.record)}`;
}

/** Per-section throughput (task counts + records) for the dashboard. */
export interface SectionThroughput {
  section: string;
  tasks: number;
  completed: number;
  failed: number;
  records: number;
}

/**
 * The winning (minimum-price) offer for one product — the server's best-price selection (R5).
 * Surfaced so the Data Graveyard can mark the winning row and the export can prefer it.
 */
export interface BestOffer {
  productKey: string;
  title: string;
  priceRaw: string;
  priceNum: number | null;
  currency: string | null;
  link: string | null;
  image: string | null;
  sourceUrl: string | null;
  section: string | null;
}

/** A completed mission (all tasks finished) landing in the Data Graveyard. */
export interface CompletedMission {
  id: string;
  title: string;
  progress: MissionProgress;
}

/** A live (in-flight) mission the dashboard lists under "current missions". */
export interface LiveMission {
  id: string;
  title: string;
  total: number;
  progress: MissionProgress | null;
}

export interface ResultsState {
  /** mission_id -> its live rollup (title/total from mission.created, progress from updates). */
  missions: Record<string, LiveMission>;
  /** the latest data preview (most recent extracted records), newest first. */
  preview: PreviewRow[];
  /** per-section throughput, records desc. */
  sections: SectionThroughput[];
  /** completed missions (Data Graveyard), newest first. */
  completedMissions: CompletedMission[];
  /** the winning min-price offer per product (best-price selection, R5), cheapest first. */
  bestOffers: BestOffer[];

  /** Apply a `mission.created` (seed the live mission row). */
  createMission: (id: string, title: string, total: number) => void;
  /** Apply a `result.mission_progress` (rollup + preview + sections + completed). */
  applyProgress: (payload: unknown) => void;
  /** Clear all results (Live toggle-off). */
  clear: () => void;
}

function toProgress(v: unknown): MissionProgress | null {
  if (typeof v !== "object" || v === null) return null;
  const o = v as Record<string, unknown>;
  return {
    total: numOf(o.total),
    completed: numOf(o.completed),
    failed: numOf(o.failed),
    records: numOf(o.records),
  };
}

function numOf(v: unknown): number {
  return typeof v === "number" ? v : 0;
}

function strOrNull(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function toPreview(v: unknown): PreviewRow[] {
  if (!Array.isArray(v)) return [];
  return v.map((r) => {
    const o = (r ?? {}) as Record<string, unknown>;
    return {
      id: numOf(o.id),
      taskId: strOrNull(o.task_id),
      missionId: strOrNull(o.mission_id),
      section: typeof o.section === "string" ? o.section : undefined,
      url: strOrNull(o.url),
      record: o.record,
    };
  });
}

/**
 * Merge an incoming preview batch into the accumulated rows KEYED by content (`dedupKey`),
 * updating matched rows in place and appending genuinely-new records — never replacing the
 * whole array. A row keeps the FIRST-seen `id` (lowest), so once placed it never moves; the
 * result is sorted OLDEST-FIRST so the reviewable list grows downward with zero reshuffle.
 */
function mergePreview(prev: PreviewRow[], incoming: PreviewRow[]): PreviewRow[] {
  const map = new Map<string, PreviewRow>();
  for (const row of prev) map.set(dedupKey(row), row);
  for (const row of incoming) {
    const key = dedupKey(row);
    const existing = map.get(key);
    if (existing) {
      // update fields in place but pin the earliest id (stable position).
      map.set(key, { ...existing, ...row, id: Math.min(existing.id, row.id) });
    } else {
      map.set(key, row);
    }
  }
  return Array.from(map.values()).sort((a, b) => a.id - b.id);
}

function toSections(v: unknown): SectionThroughput[] {
  if (!Array.isArray(v)) return [];
  return v.map((s) => {
    const o = (s ?? {}) as Record<string, unknown>;
    return {
      section: typeof o.section === "string" ? o.section : "",
      tasks: numOf(o.tasks),
      completed: numOf(o.completed),
      failed: numOf(o.failed),
      records: numOf(o.records),
    };
  });
}

function numOrNull(v: unknown): number | null {
  return typeof v === "number" ? v : null;
}

function toBestOffers(v: unknown): BestOffer[] {
  if (!Array.isArray(v)) return [];
  return v.map((o) => {
    const r = (o ?? {}) as Record<string, unknown>;
    return {
      productKey: typeof r.product_key === "string" ? r.product_key : "",
      title: typeof r.title === "string" ? r.title : "",
      priceRaw: typeof r.price_raw === "string" ? r.price_raw : "",
      priceNum: numOrNull(r.price_num),
      currency: strOrNull(r.currency),
      link: strOrNull(r.link),
      image: strOrNull(r.image),
      sourceUrl: strOrNull(r.source_url),
      section: strOrNull(r.section),
    };
  });
}

function toCompleted(v: unknown): CompletedMission[] {
  if (!Array.isArray(v)) return [];
  return v.map((m) => {
    const o = (m ?? {}) as Record<string, unknown>;
    return {
      id: typeof o.id === "string" ? o.id : "",
      title: typeof o.title === "string" ? o.title : "",
      progress: toProgress(o.progress) ?? { total: 0, completed: 0, failed: 0, records: 0 },
    };
  });
}

export const resultsStore = createStore<ResultsState>((set) => ({
  missions: {},
  preview: [],
  sections: [],
  completedMissions: [],
  bestOffers: [],

  createMission: (id, title, total) =>
    set((s) => ({
      missions: { ...s.missions, [id]: { id, title, total, progress: s.missions[id]?.progress ?? null } },
    })),

  applyProgress: (payload) =>
    set((s) => {
      const p = (payload ?? {}) as Record<string, unknown>;
      const mid = strOrNull(p.mission_id);
      const progress = toProgress(p.progress);
      const missions = { ...s.missions };
      if (mid) {
        const prev = missions[mid] ?? { id: mid, title: mid, total: progress?.total ?? 0, progress: null };
        missions[mid] = { ...prev, progress: progress ?? prev.progress };
      }
      return {
        missions,
        preview: Array.isArray(p.preview) ? mergePreview(s.preview, toPreview(p.preview)) : s.preview,
        sections: Array.isArray(p.sections) ? toSections(p.sections) : s.sections,
        completedMissions: Array.isArray(p.completed_missions)
          ? toCompleted(p.completed_missions)
          : s.completedMissions,
        bestOffers: Array.isArray(p.best_offers) ? toBestOffers(p.best_offers) : s.bestOffers,
      };
    }),

  clear: () => set({ missions: {}, preview: [], sections: [], completedMissions: [], bestOffers: [] }),
}));

/** Aggregate totals across all sections (records / completed / failed) for the dashboard. */
export function aggregateThroughput(sections: SectionThroughput[]): {
  records: number;
  completed: number;
  failed: number;
} {
  return sections.reduce(
    (acc, s) => ({
      records: acc.records + s.records,
      completed: acc.completed + s.completed,
      failed: acc.failed + s.failed,
    }),
    { records: 0, completed: 0, failed: 0 },
  );
}
