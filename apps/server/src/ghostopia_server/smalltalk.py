"""Ghost small-talk — idle co-located ghosts exchange a couple of spooky lines.

When work is light the graveyard should still feel ALIVE: two IDLE ghosts standing near each
other occasionally pair up and trade a short, turn-based ``ghost.say`` exchange (original
spooky one-liners), then part. This module owns the PURE pairing predicate + the line bank
(both unit-tested) and a small stateful :class:`SmallTalkDirector` that schedules the alternating
turns over time and emits them as :class:`~ghostopia_shared.GhostCommand` ``say`` commands.

Guardrails: a WORKING / attention ghost NEVER small-talks; pairs fire only under a
global cooldown + a max-concurrent cap + a per-ghost cooldown; bubbles carry a capped TTL. This
is optional ambient delight layered on the existing bubble renderer — it never speaks over real
work status (a working ghost is excluded from the candidate set).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ghostopia_shared import GhostCommand

# --------------------------------------------------------------------------------------
# PURE — the candidate shape, the pairing predicate, and the line bank (unit-tested).
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TalkCandidate:
    """One ghost the director may pair for small-talk: its id, world position, and idleness."""

    ghost_id: str
    x: float
    y: float
    idle: bool


def _dist(a: TalkCandidate, b: TalkCandidate) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def find_talk_pairs(
    candidates: Iterable[TalkCandidate],
    *,
    max_dist: float,
    exclude: frozenset[str] = frozenset(),
    max_pairs: int = 1,
) -> list[tuple[str, str]]:
    """Greedily pair IDLE, co-located ghosts (within ``max_dist``) for small-talk.

    PURE: only ``idle`` candidates not in ``exclude`` are eligible; each ghost is used at most
    once; nearest eligible partners are paired first; the result is capped at ``max_pairs``.
    Deterministic (ties broken by ghost id) so it is unit-testable.
    """
    pool = sorted(
        (c for c in candidates if c.idle and c.ghost_id not in exclude),
        key=lambda c: c.ghost_id,
    )
    used: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for i, a in enumerate(pool):
        if a.ghost_id in used:
            continue
        best: TalkCandidate | None = None
        best_d = max_dist
        for b in pool[i + 1 :]:
            if b.ghost_id in used:
                continue
            d = _dist(a, b)
            if d <= best_d:
                best_d = d
                best = b
        if best is not None:
            used.add(a.ghost_id)
            used.add(best.ghost_id)
            pairs.append((a.ghost_id, best.ghost_id))
            if len(pairs) >= max_pairs:
                break
    return pairs


# Original spooky one-liners (graveyard idiom — NOT any reference project's lines). An exchange
# alternates opener → reply → (optional) closer between the two ghosts.
_OPENERS: tuple[str, ...] = (
    "Quiet shift tonight…",
    "Did you feel that chill?",
    "The moon's watching again.",
    "Heard the gate creak?",
    "Restless bones, this hour.",
    "Fog's thick by the crypt.",
)
_REPLIES: tuple[str, ...] = (
    "Always is, near the crypt.",
    "Just the wind, I hope.",
    "It never stops watching.",
    "That's only old Mr. Crane.",
    "Same as every century.",
    "Best not to look too long.",
)
_CLOSERS: tuple[str, ...] = (
    "…back to haunting, then.",
    "Mind the wisps.",
    "Rest easy, friend.",
    "See you at moonset.",
)


def make_exchange(seed: int) -> list[str]:
    """Build a deterministic 2–3 line alternating exchange (opener, reply, maybe closer).

    PURE — the lines are chosen by ``seed`` so a given pair/turn is reproducible and testable.
    Returns them in speaking order (first speaker, second speaker, first speaker again).
    """
    opener = _OPENERS[seed % len(_OPENERS)]
    reply = _REPLIES[(seed // 7) % len(_REPLIES)]
    lines = [opener, reply]
    if seed % 3 == 0:
        lines.append(_CLOSERS[(seed // 13) % len(_CLOSERS)])
    return lines


# --------------------------------------------------------------------------------------
# The stateful director: schedules alternating turns over time + emits say commands.
# --------------------------------------------------------------------------------------


@dataclass
class _Turn:
    due_s: float
    ghost_id: str
    text: str


@dataclass
class SmallTalkDirector:
    """Schedules + emits idle small-talk exchanges under cooldown + concurrency caps.

    ``step(candidates, now)`` releases any due turns (as ``say`` commands) and, when allowed,
    starts a new exchange between a fresh idle co-located pair. Kept framework-free (returns the
    commands) so the runtime just forwards them to its sink and the pure logic stays testable.
    """

    max_dist: float = 3.0
    global_cooldown_s: float = 6.0
    per_ghost_cooldown_s: float = 12.0
    max_concurrent: int = 1
    turn_gap_s: float = 1.1
    ttl_ms: float = 2600.0

    _pending: list[_Turn] = field(default_factory=list)
    _busy_until: dict[str, float] = field(default_factory=dict)
    _last_start_s: float = float("-inf")
    _seed: int = 1

    def step(self, candidates: Iterable[TalkCandidate], now: float) -> list[GhostCommand]:
        cmds = self._pump(now)
        cmds.extend(self._maybe_start(list(candidates), now))
        return cmds

    def _pump(self, now: float) -> list[GhostCommand]:
        due = [t for t in self._pending if t.due_s <= now]
        if not due:
            return []
        self._pending = [t for t in self._pending if t.due_s > now]
        return [
            GhostCommand(
                kind="say",
                ghost_id=t.ghost_id,
                args={"text": t.text, "bubble": "say", "ttl_ms": self.ttl_ms},
            )
            for t in due
        ]

    def _active_pairs(self) -> int:
        # count ghosts still scheduled to speak → an in-flight exchange ties up ~2 ghosts.
        return max(1, len({t.ghost_id for t in self._pending})) if self._pending else 0

    def _maybe_start(self, candidates: list[TalkCandidate], now: float) -> list[GhostCommand]:
        if self._pending and self._active_pairs() >= self.max_concurrent:
            return []
        if now - self._last_start_s < self.global_cooldown_s:
            return []
        busy = frozenset(
            gid for gid, until in self._busy_until.items() if until > now
        ) | {t.ghost_id for t in self._pending}
        pairs = find_talk_pairs(
            candidates, max_dist=self.max_dist, exclude=busy, max_pairs=1
        )
        if not pairs:
            return []
        a, b = pairs[0]
        self._seed = (self._seed * 1103515245 + 12345) & 0x7FFFFFFF
        lines = make_exchange(self._seed)
        speakers = [a, b, a]
        immediate: list[GhostCommand] = []
        for i, text in enumerate(lines):
            gid = speakers[i % 2] if i < 2 else a
            due = now + i * self.turn_gap_s
            if i == 0:
                immediate.append(
                    GhostCommand(
                        kind="say",
                        ghost_id=gid,
                        args={"text": text, "bubble": "say", "ttl_ms": self.ttl_ms},
                    )
                )
            else:
                self._pending.append(_Turn(due_s=due, ghost_id=gid, text=text))
        # cool both partners down so they don't immediately re-pair.
        self._busy_until[a] = now + self.per_ghost_cooldown_s
        self._busy_until[b] = now + self.per_ghost_cooldown_s
        self._last_start_s = now
        return immediate
