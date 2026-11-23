// Filters low-level status terms from customer-facing copy; the Python and TypeScript
// lexicons are kept in sync.

// Terms filtered from customer-facing copy. Compound tokens tolerate a space/hyphen/underscore separator.
const BANNED_SURFACE_RE =
  /\b(?:datadome|kasada|perimeterx|imperva|akamai|cloudflare|waf|anti[\s_-]?bot|proxy|residential|data[\s_-]?center|datacenter|fingerprint|stealth|spoof(?:ing|ed)?|bypass|clearance|cf[\s_-]?clearance|captcha[\s_-]?bypass|evasion|undetectable|human|real[\s_-]?human|)\b/i;

/** True when `text` contains NONE of the banned lexicon (empty/non-string is safe). */
export function isSurfaceSafe(text: unknown): boolean {
  if (typeof text !== "string" || text.length === 0) return true;
  return !BANNED_SURFACE_RE.test(text);
}

/**
 * Return `raw` when it is present and surface-safe; otherwise `fallback`. Use at every render
 * point that shows a server-derived code/reason so a leaked literal never reaches the DOM.
 */
export function safeSurfaceText(raw: unknown, fallback: string): string {
  if (typeof raw === "string" && raw.trim().length > 0 && isSurfaceSafe(raw)) return raw;
  return fallback;
}
