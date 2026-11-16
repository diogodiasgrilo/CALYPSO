/**
 * An interval poller that cannot overlap itself.
 *
 * WHEN YOU NEED THIS. A plain `setInterval(asyncTick, ms)` fires on the clock
 * regardless of whether the previous tick finished. That is harmless while
 * responses are faster than the interval and compounding when they are not:
 * requests queue up, each one's response is older than the last, and the UI
 * renders them in whatever order they land.
 *
 * The precise condition is `request deadline > interval`. Every `/api` call now
 * carries a 10s deadline (see apiFetch), so a 2s poller can have five requests
 * in flight at once on a slow link, while a 30s poller provably cannot overlap
 * at all. That is why this is applied to the 2s pollers and deliberately NOT to
 * the 30s ones — a guard there would be ceremony, and ceremony hides which
 * guards are load-bearing.
 *
 * Behaviour matches the hand-rolled pattern it replaces: fire once
 * immediately, then on the interval, and return a stop function for the
 * effect's cleanup. A tick that is skipped is simply skipped; it is not queued,
 * because the next one is only `intervalMs` away and will read fresher data
 * than the one that was dropped.
 */
export function startPolling(task: () => Promise<unknown>, intervalMs: number): () => void {
  let inFlight = false;
  let stopped = false;

  const run = () => {
    if (inFlight || stopped) return;
    inFlight = true;

    // Call `task` SYNCHRONOUSLY. The first draft wrapped it in
    // `Promise.resolve().then(task)`, which defers it by a microtask — so the
    // immediate fire was no longer immediate. That is a real behavioural
    // change from the hand-rolled `tick(); setInterval(tick, ms)` this
    // replaces, and the "fires once immediately" test caught it.
    let running: Promise<unknown>;
    try {
      running = Promise.resolve(task());
    } catch {
      // A task that throws synchronously must not leave the latch stuck, or
      // polling stops for the life of the page after one bad tick.
      inFlight = false;
      return;
    }

    // `catch` BEFORE `finally`: `finally` passes the rejection through, so
    // without this a task that rejects becomes an unhandled rejection (which
    // is exactly what the rejected-tick test reported). Swallowing is correct
    // here — every caller already renders its own error state — but the latch
    // release is the part that matters: one network error must not end
    // polling.
    void running.catch(() => undefined).finally(() => {
      inFlight = false;
    });
  };

  run();
  const id = setInterval(run, intervalMs);

  return () => {
    stopped = true;
    clearInterval(id);
  };
}
