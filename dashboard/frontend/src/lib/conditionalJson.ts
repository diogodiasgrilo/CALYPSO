/**
 * Client half of the conditional-GET (ETag / 304) path for the 2s pollers.
 *
 * WHY THE CLIENT SENDS THE VALIDATOR ITSELF
 * -----------------------------------------
 * The browser's HTTP cache would revalidate a `Cache-Control: private, no-cache`
 * response on its own and resolve the 304 transparently — but it would do so
 * invisibly, so the page could not tell "unchanged" from "changed", and whether
 * it happens at all depends on the HTTP cache being available (private windows,
 * cleared site data, and a service worker sitting in front of every `/api/*`
 * request all make that a guess rather than a guarantee).
 *
 * Sending `If-None-Match` explicitly makes the 304 OBSERVABLE, and that is worth
 * more than the bytes: a `304` means nothing changed, so the caller can SKIP THE
 * STATE UPDATE. The snapshot poller previously called `setSnapshot(json)` with a
 * brand-new object every 2s whether or not a single value had moved — 30
 * re-renders a minute of the whole strategy view for no new information. Now it
 * re-renders when the data actually changes.
 *
 * A request carrying a conditional header bypasses the HTTP cache, so exactly one
 * revalidation happens per poll — the explicit one. The matching service-worker
 * route is NetworkOnly for these two endpoints (see `vite.config.ts`), so a
 * Workbox handler can never answer a live poll out of its own cache.
 */

import { apiFetch, type ApiFetchInit } from "./apiFetch";

export interface ConditionalResult<T> {
  /** Parsed body on a 200; null on a 304 or any failure. */
  data: T | null;
  /** True when the server answered 304 — the caller's existing data is current. */
  unchanged: boolean;
  /** True for 200 and 304 alike: both mean the request succeeded. */
  ok: boolean;
  status: number;
  /** Response headers, so a caller can read a value excluded from the validator. */
  headers: Headers | null;
}

/**
 * Create a stateful conditional fetcher. The returned function remembers the
 * last ETag it was given, so each caller gets ONE validator.
 *
 * Create one per polling effect, not one per module: an instance scoped to the
 * effect is discarded when the selected strategy changes, which is what keeps a
 * previous strategy's validator from being sent against a new strategy's URL.
 */
export function createConditionalFetcher<T>() {
  let etag: string | null = null;

  return async function fetchConditional(
    url: string,
    init: ApiFetchInit = {},
  ): Promise<ConditionalResult<T>> {
    const headers = new Headers(init.headers);
    if (etag) headers.set("If-None-Match", etag);

    const r = await apiFetch(url, { ...init, headers });

    if (r.status === 304) {
      return { data: null, unchanged: true, ok: true, status: 304, headers: r.headers };
    }
    if (!r.ok) {
      // Drop the validator on an error. Keeping it would mean that after a
      // transient failure we could send a stale validator and accept a 304
      // against data we never successfully parsed.
      etag = null;
      return { data: null, unchanged: false, ok: false, status: r.status, headers: r.headers };
    }

    let data: T;
    try {
      data = (await r.json()) as T;
    } catch (e) {
      etag = null;
      throw e;
    }
    // Only remember the validator once the body it describes is parsed. If
    // parsing throws, the next poll must be unconditional.
    etag = r.headers.get("ETag");
    return { data, unchanged: false, ok: true, status: r.status, headers: r.headers };
  };
}
