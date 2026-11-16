/**
 * Pins for the request deadline. The control at the bottom is the important
 * one: it shows that WITHOUT a deadline a stalled request never settles, which
 * is the exact condition these tests exist to rule out. Without it, every
 * assertion here could pass against a no-op wrapper.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { apiFetch, DEFAULT_API_TIMEOUT_MS } from "../lib/apiFetch";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

/** A fetch that never settles — a stalled connection.
 *
 *  It rejects on abort, INCLUDING when handed a signal that is already
 *  aborted. The first version of this double only listened for the abort
 *  event, so an already-aborted signal produced a promise that never settled
 *  and the test hung for 5s. Real `fetch` rejects immediately in that case, so
 *  the double was the unrealistic half — but it also exposed that apiFetch was
 *  depending on fetch to do that job, which it now does itself. */
function stalledFetch() {
  return vi.fn(
    (_input: string, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal;
        if (signal?.aborted) {
          reject(signal.reason ?? new Error("aborted"));
          return;
        }
        signal?.addEventListener("abort", () => {
          reject(signal.reason ?? new Error("aborted"));
        });
      }),
  );
}

describe("apiFetch imposes a deadline", () => {
  it("rejects a stalled request instead of hanging forever", async () => {
    vi.stubGlobal("fetch", stalledFetch());
    const p = apiFetch("/api/health", { timeoutMs: 20 });
    await expect(p).rejects.toMatchObject({ name: "TimeoutError" });
  });

  it("names the endpoint and the deadline in the error", async () => {
    vi.stubGlobal("fetch", stalledFetch());
    await expect(apiFetch("/api/strategies/bl/snapshot", { timeoutMs: 5 })).rejects.toThrow(
      /\/api\/strategies\/bl\/snapshot.*5ms/,
    );
  });

  it("passes a normal response straight through", async () => {
    const body = new Response('{"ok":true}', { status: 200 });
    vi.stubGlobal("fetch", vi.fn(async () => body));
    const r = await apiFetch("/api/health");
    expect(r).toBe(body);
    expect(await r.json()).toEqual({ ok: true });
  });

  it("forwards method, headers and body unchanged", async () => {
    // Typed params, so `spy.mock.calls[0]` is the real tuple rather than `[]`
    // (which needed an `as` cast that tsc rightly rejected).
    // The parameters exist so `spy.mock.calls[0]` types as the real tuple
    // rather than `[]`. They are referenced in the body because this config
    // has no underscore exemption for unused args.
    const spy = vi.fn(async (input: string, init?: RequestInit) =>
      new Response(JSON.stringify({ input, method: init?.method ?? "GET" }), { status: 200 }));
    vi.stubGlobal("fetch", spy);
    await apiFetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: '{"u":1}',
    });
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"u":1}');
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("does NOT abort a request that already completed", async () => {
    // A stale timer firing after success would abort nothing visible here but
    // would leak a pending timer per request; clearing it is the invariant.
    vi.useFakeTimers();
    const spy = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", spy);
    const r = await apiFetch("/api/health", { timeoutMs: 10 });
    expect(r.status).toBe(200);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("still honours a caller's own abort signal", async () => {
    vi.stubGlobal("fetch", stalledFetch());
    const ac = new AbortController();
    const p = apiFetch("/api/health", { signal: ac.signal, timeoutMs: 60_000 });
    ac.abort(new Error("component unmounted"));
    await expect(p).rejects.toThrow("component unmounted");
  });

  it("rejects immediately when the caller's signal is already aborted", async () => {
    vi.stubGlobal("fetch", stalledFetch());
    const ac = new AbortController();
    ac.abort(new Error("already gone"));
    await expect(
      apiFetch("/api/health", { signal: ac.signal, timeoutMs: 60_000 }),
    ).rejects.toThrow("already gone");
  });

  it("defaults to a bounded deadline rather than none", () => {
    expect(DEFAULT_API_TIMEOUT_MS).toBeGreaterThan(0);
    expect(DEFAULT_API_TIMEOUT_MS).toBeLessThanOrEqual(30_000);
  });
});

describe("control — the condition being ruled out", () => {
  it("a BARE fetch to a stalled endpoint never settles", async () => {
    vi.stubGlobal("fetch", stalledFetch());
    let settled = false;
    void (globalThis.fetch as unknown as typeof fetch)("/api/health").then(
      () => { settled = true; },
      () => { settled = true; },
    );
    // Give the microtask queue every chance to run.
    await new Promise((r) => setTimeout(r, 50));
    expect(settled).toBe(false);
  });
});
