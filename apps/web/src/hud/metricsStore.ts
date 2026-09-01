// ghostopia web — the live metrics store (STAGE 7 dashboard).
//
// Real-stream counters derived from the SAME server envelopes that drive the world: each
// browser.navigate → a page crawled, browser.session_opened → a session, task.retry → a
// retry, and a captcha/rate-limit browser.error → its counter. pages/min + avg page time are
// computed from the real navigation timestamps. NO metric is invented — a counter with no
// backing envelope simply stays 0 (REAL-NOT-MOCK). This file imports NO SDK and NO key.

import { createStore } from "zustand/vanilla";

export interface MetricsState {
  pagesCrawled: number;
  sessionsOpened: number;
  retries: number;
  captchaEvents: number;
  rateLimits: number;
  /** navigation timestamps (seconds) for pages/min + avg page time. */
  navTimes: number[];

  /** Count a real `browser.navigate` (a page was crawled). */
  countNavigate: (ts: number) => void;
  /** Count a real `browser.session_opened`. */
  countSession: () => void;
  /** Count a real `task.retry`. */
  countRetry: () => void;
  /** Classify + count a real `browser.error` (captcha / rate-limit / other). */
  countError: (code: string, visual: string | null) => void;
  clear: () => void;
}

const MAX_NAV = 200;

export const metricsStore = createStore<MetricsState>((set) => ({
  pagesCrawled: 0,
  sessionsOpened: 0,
  retries: 0,
  captchaEvents: 0,
  rateLimits: 0,
  navTimes: [],

  countNavigate: (ts) =>
    set((s) => ({ pagesCrawled: s.pagesCrawled + 1, navTimes: [...s.navTimes, ts].slice(-MAX_NAV) })),
  countSession: () => set((s) => ({ sessionsOpened: s.sessionsOpened + 1 })),
  countRetry: () => set((s) => ({ retries: s.retries + 1 })),
  countError: (code, visual) =>
    set((s) => {
      const hay = `${code} ${visual ?? ""}`.toLowerCase();
      if (hay.includes("captcha")) return { captchaEvents: s.captchaEvents + 1 };
      if (hay.includes("rate")) return { rateLimits: s.rateLimits + 1 };
      return {};
    }),
  clear: () =>
    set({ pagesCrawled: 0, sessionsOpened: 0, retries: 0, captchaEvents: 0, rateLimits: 0, navTimes: [] }),
}));

/** pages/min over the observed navigation window (0 until at least two navs). */
export function pagesPerMinute(navTimes: number[]): number {
  if (navTimes.length < 2) return 0;
  const span = navTimes[navTimes.length - 1] - navTimes[0];
  if (span <= 0) return 0;
  return (navTimes.length - 1) / (span / 60);
}

/** mean seconds between navigations (avg page time estimate); 0 until at least two navs. */
export function avgPageSeconds(navTimes: number[]): number {
  if (navTimes.length < 2) return 0;
  const span = navTimes[navTimes.length - 1] - navTimes[0];
  return span / (navTimes.length - 1);
}
