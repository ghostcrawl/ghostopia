// ghostopia web — the customer-surface language boundary (TS mirror).
//
// The thin frontend must NEVER render a raw, low-level status string — an anti-bot vendor name
// or a networking/anti-detection term — even if a (buggy or future) server path let a raw
// provider string through. This is the client-side LAST LINE of the boundary: the server
// sanitizes at broadcast (curated vocabulary), and any code/reason the client still renders is
// passed through `safeSurfaceText` first, so the DOM can never contain the banned lexicon.
//
// This is the TS mirror of `ghostopia_shared.surface_safe`; the two lexicons are kept in sync
// and the `check_surface_language.sh` gate allowlists BOTH definition sites (they must contain
// the terms in order to match them) while scanning every other surface for them.

// The banned lexicon. Compound tokens tolerate a space / hyphen / underscore separator.
const BANNED_SURFACE_RE =
  /\b(?:datadome|kasada|perimeterx|imperva|akamai|cloudflare|waf|anti[\s_-]?bot|proxy|residential|data[\s_-]?center|datacenter|fingerprint|stealth|spoof(?:ing|ed)?|bypass|clearance|cf[\s_-]?clearance|captcha[\s_-]?bypass|human|real[\s_-]?human)\b/i;

/** True when `text` contains NONE of the banned lexicon (empty / non-string is safe). */
export function isSurfaceSafe(text: unknown): boolean {
  if (typeof text !== "string" || text.length === 0) return true;
  return !BANNED_SURFACE_RE.test(text);
}

/**
 * Return `raw` when it is present and surface-safe; otherwise `fallback`. Use at every render
 * point that shows a server-derived code / reason so a leaked literal never reaches the DOM.
 */
export function safeSurfaceText(raw: unknown, fallback: string): string {
  if (typeof raw === "string" && raw.trim().length > 0 && isSurfaceSafe(raw)) return raw;
  return fallback;
}
