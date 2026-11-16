/**
 * `fetch` with a deadline. Use this for every `/api/...` call.
 *
 * WHY THIS EXISTS. Until 2026-11-16 the dashboard had 23 `fetch("/api/...")`
 * call sites and not one of them carried a timeout, an AbortController or an
 * AbortSignal — the whole frontend had no request deadline anywhere. The
 * browser's own timeout is the only backstop, and on iOS Safari over a
 * stalling cellular link that is on the order of a minute, during which the
 * UI simply shows its loading state. That is the "it just sits on loading"
 * report from a real phone session, where every request that DID arrive was
 * answered 200 in about 3ms: the server was never slow, there was just no
 * bound on how long the client would wait for one that stalled.
 *
 * A deadline does not make a stalled network work. It converts an unbounded
 * hang into a bounded, visible failure that the caller's existing error path
 * can render — which is the difference between "broken" and "stuck".
 *
 * WHY AbortController AND NOT `AbortSignal.timeout()`. The latter is tidier
 * but it cannot be composed with a caller-supplied signal without
 * `AbortSignal.any()`, which is newer than some of the Safari versions this
 * dashboard is installed on as a PWA. Hand-rolling the controller keeps one
 * code path for both cases and stays testable under jsdom.
 *
 * The timer is always cleared in `finally`, so a request that completes
 * normally cannot be aborted later by its own stale timeout — that bug would
 * be invisible in the common case and only bite under load.
 */

/** 10s. Generous next to the measured server latency (~3ms locally, and the
 *  heaviest endpoint on the VM answers in single-digit ms), so this only ever
 *  fires on a genuinely stalled connection rather than a slow one. It is NOT a
 *  pile-up guard for repeat polling — a poller must not overlap its own
 *  requests, which is the caller's job (see useStrategySnapshot). */
export const DEFAULT_API_TIMEOUT_MS = 10_000;

export interface ApiFetchInit extends RequestInit {
  /** Override the deadline for this one call. */
  timeoutMs?: number;
}

export function apiFetch(input: string, init: ApiFetchInit = {}): Promise<Response> {
  const { timeoutMs = DEFAULT_API_TIMEOUT_MS, signal: callerSignal, ...rest } = init;

  // Already cancelled before we started: reject without opening a request and
  // without arming a timer. The spec says `fetch` rejects on an aborted
  // signal, but leaning on that meant the outcome depended on fetch's own
  // abort handling — and a test with a stalled fetch double hung forever here,
  // which is precisely the failure mode this module exists to prevent.
  if (callerSignal?.aborted) {
    return Promise.reject(callerSignal.reason ?? new DOMException("Aborted", "AbortError"));
  }

  const controller = new AbortController();

  // Honour a caller's own signal as well as the deadline, so an unmounting
  // component can still cancel early and a hung request still dies on time.
  const forwardAbort = () => controller.abort(callerSignal?.reason);
  callerSignal?.addEventListener("abort", forwardAbort, { once: true });

  const timer = setTimeout(() => {
    controller.abort(
      new DOMException(
        `Request to ${input} exceeded the ${timeoutMs}ms deadline`,
        "TimeoutError",
      ),
    );
  }, timeoutMs);

  return fetch(input, { ...rest, signal: controller.signal }).finally(() => {
    clearTimeout(timer);
    callerSignal?.removeEventListener("abort", forwardAbort);
  });
}
