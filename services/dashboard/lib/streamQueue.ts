/**
 * Admission control for the live wall.
 *
 * Thirty tiles mounting at once used to open thirty sessions at once. The
 * gateway serves each connected client its own copy of the stream, so the
 * burst competed with itself: a handful connected, the rest were refused or
 * timed out, and their tiles sat on "Opening..." indefinitely. The wall looked
 * broken while every camera on it was healthy.
 *
 * The fix is not fewer feeds, it is fewer feeds STARTING at once. A slot is
 * held only across the expensive part - session request, manifest fetch, first
 * segment - and released the moment the tile is playing. Playback itself
 * costs the gateway a steady trickle and needs no permission, so the wall
 * still ends up with every tile live; they just arrive in waves of a few
 * rather than all trampling each other.
 *
 * FIFO, so a tile that queued first starts first and the wall fills top to
 * bottom instead of at random.
 */

/** Give the slot back. Safe to call more than once. */
export type Release = () => void;

interface Waiter {
  admit: () => void;
  cancelled: boolean;
}

class StreamQueue {
  /**
   * Feeds allowed to be *starting* simultaneously.
   *
   * Raised from 3 to 12 once session-opens stopped costing a slow grid probe
   * and all consumers moved onto one shared grid session. The old limit was
   * conservative for a reason that no longer holds: each start used to hold
   * its slot through a ~30s cold manifest fetch, so at 3-at-a-time a wall of
   * thirty took minutes to fill. A slot is still held only across the start,
   * and the per-camera manifest cache means most starts after the first are
   * near-instant - so twelve lets the wall come up all at once without asking
   * the browser to spin up thirty video decoders in the very same tick.
   */
  private limit = 12;

  /**
   * Milliseconds between consecutive starts, even with slots free.
   *
   * Small: it exists only to keep a dozen hls.js instances from initialising
   * in one synchronous burst, not to pace the gateway (the shared session and
   * manifest cache do that). At 80ms the wall still reads as filling at once.
   */
  private gapMs = 80;

  private active = 0;
  private queue: Waiter[] = [];
  private lastStart = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;

  configure(options: { limit?: number; gapMs?: number }): void {
    if (options.limit != null) this.limit = Math.max(1, options.limit);
    if (options.gapMs != null) this.gapMs = Math.max(0, options.gapMs);
    this.pump();
  }

  /**
   * Wait for permission to start a feed.
   *
   * `signal` lets a tile that scrolled away or unmounted drop out of the queue
   * instead of being admitted into a component that no longer exists.
   */
  acquire(signal?: AbortSignal): Promise<Release> {
    return new Promise<Release>((resolve, reject) => {
      if (signal?.aborted) {
        reject(new DOMException("cancelled", "AbortError"));
        return;
      }

      const waiter: Waiter = {
        cancelled: false,
        admit: () => {
          let released = false;
          resolve(() => {
            if (released) return;
            released = true;
            this.active -= 1;
            this.pump();
          });
        },
      };

      signal?.addEventListener(
        "abort",
        () => {
          if (waiter.cancelled) return;
          waiter.cancelled = true;
          reject(new DOMException("cancelled", "AbortError"));
          this.pump();
        },
        { once: true },
      );

      this.queue.push(waiter);
      this.pump();
    });
  }

  /** Admit whoever can go now, and schedule the next look if anyone is left. */
  private pump(): void {
    if (this.timer) return;

    while (this.queue.length > 0 && this.queue[0].cancelled) this.queue.shift();
    if (this.queue.length === 0 || this.active >= this.limit) return;

    const wait = this.gapMs - (Date.now() - this.lastStart);
    if (wait > 0) {
      this.timer = setTimeout(() => {
        this.timer = null;
        this.pump();
      }, wait);
      return;
    }

    const next = this.queue.shift();
    if (!next) return;
    this.active += 1;
    this.lastStart = Date.now();
    next.admit();
    this.pump();
  }

  /** For the wall's own status line. */
  get pending(): number {
    return this.queue.filter((w) => !w.cancelled).length;
  }

  get starting(): number {
    return this.active;
  }
}

/**
 * One queue for the whole tab.
 *
 * Deliberately module scope rather than React context: the constraint being
 * modelled is the gateway's, and it does not care how many component trees the
 * dashboard happens to have. A second wall opened in a split view queues
 * behind the first, which is the correct behaviour.
 */
export const streamQueue = new StreamQueue();
