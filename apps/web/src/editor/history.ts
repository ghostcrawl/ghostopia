// ghostopia web — bounded undo/redo history for the Graveyard Builder.
//
// A generic, PURE snapshot-stack undo/redo (≥25 deep per the plan) over immutable editor
// states. The editor pushes a new draft after every edit op; undo/redo walk the stack. The
// stack is CAPPED (oldest snapshots drop) so a long editing session never grows unbounded.
// No PixiJS / SDK / key — pure state management, unit-tested.

/** The minimum undo depth the plan requires. */
export const MIN_HISTORY_DEPTH = 25;

/** A bounded undo/redo stack over immutable snapshots of type `T`. */
export class History<T> {
  private readonly limit: number;
  private past: T[] = [];
  private present: T;
  private future: T[] = [];

  constructor(initial: T, limit = 50) {
    this.present = initial;
    // never allow a limit below the required floor.
    this.limit = Math.max(MIN_HISTORY_DEPTH, limit);
  }

  /** The current snapshot. */
  get current(): T {
    return this.present;
  }

  /** True when there is a prior snapshot to undo to. */
  get canUndo(): boolean {
    return this.past.length > 0;
  }

  /** True when an undone snapshot can be redone. */
  get canRedo(): boolean {
    return this.future.length > 0;
  }

  /** How many undo steps are currently held (for a depth read-out / tests). */
  get depth(): number {
    return this.past.length;
  }

  /**
   * Push a NEW present snapshot. The prior present moves onto the undo stack, the redo stack
   * is cleared (a new edit forks history), and the oldest snapshot drops past the cap. A push
   * equal (identity) to the current present is ignored (a no-op edit records no history).
   */
  push(next: T): void {
    if (next === this.present) return;
    this.past.push(this.present);
    if (this.past.length > this.limit) this.past.shift();
    this.present = next;
    this.future = [];
  }

  /** Undo one step; returns the restored snapshot (or the unchanged present when at the base). */
  undo(): T {
    const prev = this.past.pop();
    if (prev === undefined) return this.present;
    this.future.push(this.present);
    if (this.future.length > this.limit) this.future.shift();
    this.present = prev;
    return this.present;
  }

  /** Redo one step; returns the restored snapshot (or the unchanged present when nothing to redo). */
  redo(): T {
    const next = this.future.pop();
    if (next === undefined) return this.present;
    this.past.push(this.present);
    if (this.past.length > this.limit) this.past.shift();
    this.present = next;
    return this.present;
  }

  /** Reset the whole history to a fresh base snapshot (used on enter / after a save). */
  reset(base: T): void {
    this.past = [];
    this.future = [];
    this.present = base;
  }
}
