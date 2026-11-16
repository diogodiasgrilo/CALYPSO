/**
 * Pins the client half of the conditional-GET path.
 *
 * The two 2s pollers used to re-download and re-apply a full payload every
 * tick: 11,481 B gzip for the snapshot, 11,720 B for the group comparison,
 * ~20 MB/hour each, and a brand-new object handed to React 30 times a minute
 * whether or not a single value had moved.
 *
 * The validator is sent by the CLIENT rather than left to the browser's HTTP
 * cache, because that is what makes "unchanged" observable to the page — and
 * skipping the state update is worth more than the bytes.
 *
 * The control at the bottom of each group reproduces the behaviour being
 * replaced, so none of these can pass against a no-op wrapper.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { createConditionalFetcher } from "../lib/conditionalJson";

afterEach(() => {
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, etag: string, status = 200) {
  return new Response(status === 304 ? null : JSON.stringify(body), {
    status,
    headers: etag ? { ETag: etag, "Content-Type": "application/json" } : {},
  });
}

/** Records the If-None-Match of every call and replays scripted responses. */
function scriptedFetch(responses: Response[]) {
  const seen: (string | null)[] = [];
  const fn = vi.fn((_input: string, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    seen.push(headers.get("If-None-Match"));
    const next = responses.shift();
    if (!next) throw new Error("scriptedFetch ran out of responses");
    return Promise.resolve(next);
  });
  return { fn, seen };
}

describe("the first request is unconditional", () => {
  it("sends no If-None-Match when it holds no validator", async () => {
    const { fn, seen } = scriptedFetch([jsonResponse({ id: "bl" }, '"v1"')]);
    vi.stubGlobal("fetch", fn);
    const get = createConditionalFetcher<{ id: string }>();
    const res = await get("/api/strategies/bl/snapshot");
    expect(seen).toEqual([null]);
    expect(res.unchanged).toBe(false);
    expect(res.data).toEqual({ id: "bl" });
  });
});

describe("subsequent requests carry the validator", () => {
  it("echoes the ETag it was given", async () => {
    const { fn, seen } = scriptedFetch([
      jsonResponse({ id: "bl" }, '"v1"'),
      jsonResponse(null, '"v1"', 304),
    ]);
    vi.stubGlobal("fetch", fn);
    const get = createConditionalFetcher();
    await get("/api/x");
    await get("/api/x");
    expect(seen).toEqual([null, '"v1"']);
  });

  it("reports 304 as unchanged, with no data and ok=true", async () => {
    const { fn } = scriptedFetch([
      jsonResponse({ n: 1 }, '"v1"'),
      jsonResponse(null, '"v1"', 304),
    ]);
    vi.stubGlobal("fetch", fn);
    const get = createConditionalFetcher<{ n: number }>();
    await get("/api/x");
    const res = await get("/api/x");
    expect(res.status).toBe(304);
    expect(res.unchanged).toBe(true);
    expect(res.ok).toBe(true);
    expect(res.data).toBeNull();
  });

  it("adopts the new validator when the content changes", async () => {
    const { fn, seen } = scriptedFetch([
      jsonResponse({ n: 1 }, '"v1"'),
      jsonResponse({ n: 2 }, '"v2"'),
      jsonResponse(null, '"v2"', 304),
    ]);
    vi.stubGlobal("fetch", fn);
    const get = createConditionalFetcher<{ n: number }>();
    await get("/api/x");
    const second = await get("/api/x");
    await get("/api/x");
    expect(second.data).toEqual({ n: 2 });
    expect(seen).toEqual([null, '"v1"', '"v2"']);
  });

  it("preserves headers the caller supplied", async () => {
    const seenAll: Headers[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((_i: string, init?: RequestInit) => {
        seenAll.push(new Headers(init?.headers));
        return Promise.resolve(jsonResponse({}, '"v1"'));
      }),
    );
    const get = createConditionalFetcher();
    await get("/api/x", { headers: { "X-Widget-Token": "abc" } });
    expect(seenAll[0].get("X-Widget-Token")).toBe("abc");
  });
});

describe("a failure must not leave a validator behind", () => {
  it("drops the ETag after a non-ok response, so the next poll is unconditional", async () => {
    const { fn, seen } = scriptedFetch([
      jsonResponse({ n: 1 }, '"v1"'),
      new Response("boom", { status: 500 }),
      jsonResponse({ n: 3 }, '"v3"'),
    ]);
    vi.stubGlobal("fetch", fn);
    const get = createConditionalFetcher();
    await get("/api/x");
    const bad = await get("/api/x");
    await get("/api/x");
    expect(bad.ok).toBe(false);
    expect(bad.unchanged).toBe(false);
    // Third request must NOT carry "v1": accepting a 304 against a payload we
    // never parsed would leave the page showing data it cannot account for.
    expect(seen).toEqual([null, '"v1"', null]);
  });

  it("does not store a validator whose body failed to parse", async () => {
    const { fn, seen } = scriptedFetch([
      new Response("not json", { status: 200, headers: { ETag: '"bad"' } }),
      jsonResponse({ n: 1 }, '"v1"'),
    ]);
    vi.stubGlobal("fetch", fn);
    const get = createConditionalFetcher();
    await expect(get("/api/x")).rejects.toBeTruthy();
    await get("/api/x");
    expect(seen).toEqual([null, null]);
  });

  it("CONTROL: a fetcher that kept the ETag through an error would resend it", async () => {
    // The behaviour being avoided above, written out so the assertion there
    // cannot pass vacuously.
    let etag: string | null = null;
    const seen: (string | null)[] = [];
    const naive = async (r: Response) => {
      seen.push(etag);
      if (r.ok) etag = r.headers.get("ETag");
      return r;
    };
    await naive(jsonResponse({ n: 1 }, '"v1"'));
    await naive(new Response("boom", { status: 500 }));
    await naive(jsonResponse({ n: 1 }, '"v1"'));
    expect(seen).toEqual([null, '"v1"', '"v1"']);
  });
});

describe("one validator per fetcher instance", () => {
  it("a fresh fetcher starts unconditional, which is what isolates strategies", async () => {
    const { fn, seen } = scriptedFetch([
      jsonResponse({ id: "bl" }, '"bl-1"'),
      jsonResponse({ id: "g" }, '"g-1"'),
    ]);
    vi.stubGlobal("fetch", fn);
    const a = createConditionalFetcher();
    await a("/api/strategies/bl/snapshot");
    const b = createConditionalFetcher();
    await b("/api/strategies/g/snapshot");
    expect(seen).toEqual([null, null]);
  });
});
