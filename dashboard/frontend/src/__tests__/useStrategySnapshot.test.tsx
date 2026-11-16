/**
 * THE PAYOFF PIN for conditional polling: a 304 must cost ZERO renders.
 *
 * The snapshot poller called `setSnapshot(json)` with a freshly parsed object
 * every 2s — 30 times a minute — whether or not a single value had changed.
 * Measured on the live seat after the close, two polls 2.5s apart differed in
 * exactly one field, `state_file_age_seconds`, a clock reading. So that was 30
 * full re-renders a minute of the whole strategy view to display identical
 * numbers, on top of ~11.5 kB of gzip per poll.
 *
 * A screenshot cannot see a render count, which is why this is a unit test and
 * why each group below carries a control: `the 200 path still re-renders` is
 * what stops "no re-renders" from being a worse bug wearing a passing test.
 */
import { Profiler } from "react";
import { render, cleanup, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStrategySnapshot } from "../hooks/useStrategySnapshot";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const POLL_MS = 2000;

function snapshotBody(id: string, age: number, entries: unknown[] = []) {
  return {
    id,
    data_kind: "ic_state",
    group_id: "ic_0dte",
    pnl_shape: "credit",
    is_primary: false,
    display_name: id.toUpperCase(),
    label: null,
    dry_run: true,
    underlying_symbol: "SPX",
    body: { available: true, state_file_age_seconds: age, entries },
  };
}

function res(body: unknown, etag: string, status = 200) {
  return new Response(status === 304 ? null : JSON.stringify(body), {
    status,
    headers: etag ? { ETag: etag } : {},
  });
}

function Host({ id, onRender }: { id: string; onRender: () => void }) {
  return (
    <Profiler id="host" onRender={onRender}>
      <Inner id={id} />
    </Profiler>
  );
}

function Inner({ id }: { id: string }) {
  const { snapshot, loading, error } = useStrategySnapshot(id, true);
  return (
    <div>
      <span data-testid="id">{snapshot?.id ?? "none"}</span>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="error">{error ?? ""}</span>
      <span data-testid="entries">{(snapshot?.body as { entries?: unknown[] })?.entries?.length ?? -1}</span>
    </div>
  );
}

/** Run the poller forward N ticks with fake timers, flushing microtasks.
 *
 *  NOTE ON COUNTING: `startPolling` runs its task IMMEDIATELY on creation (so a
 *  mount shows data without waiting 2s), which means the FIRST response is
 *  consumed during `render`, not on the first tick. Every count below is written
 *  against that; getting it wrong is what made the first draft of the error test
 *  assert against the response after the one it meant.
 */
async function tickTimes(n: number) {
  for (let i = 0; i < n; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
  }
}

describe("a 304 costs no render", () => {
  it("does not re-render per 304 — the cost does not grow with the poll", async () => {
    // WHY THIS IS "DOES NOT GROW" RATHER THAN "EXACTLY ZERO": the unchanged
    // branch calls setError(null)/setLoading(false), which are already at those
    // values, and React documents that a State Hook set to its CURRENT value
    // "may still render that component once more before bailing out". That is a
    // single bounded render on the transition, not one per poll — measured here
    // as 1 across the first five 304s and 0 across the next five. Asserting
    // "exactly zero" would have been asserting something React does not
    // promise, and chasing it would mean carrying extra refs to re-derive state
    // the hook already has.
    vi.useFakeTimers();
    const responses = [
      res(snapshotBody("bl", 1.0), '"v1"'),
      ...Array.from({ length: 12 }, () => res(null, '"v1"', 304)),
    ];
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(responses.shift()!)));

    let renders = 0;
    await act(async () => {
      render(<Host id="bl" onRender={() => { renders += 1; }} />);
    });
    // The 200 is consumed by the immediate first run, during render.
    const afterFirstPayload = renders;
    expect(afterFirstPayload).toBeGreaterThan(0);

    await tickTimes(5);
    const afterFiveUnchanged = renders;
    expect(afterFiveUnchanged).toBeLessThanOrEqual(afterFirstPayload + 1);

    // THE PIN: five more 304s cost nothing at all. Before this change each one
    // handed React a freshly parsed object and re-rendered the whole strategy
    // view — 30 times a minute, forever, for identical numbers.
    await tickTimes(5);
    expect(renders).toBe(afterFiveUnchanged);
  });

  it("CONTROL: the 200 path still re-renders when the data changes", async () => {
    vi.useFakeTimers();
    const responses = [
      res(snapshotBody("bl", 1.0, []), '"v1"'),
      res(snapshotBody("bl", 2.0, [{ entry_number: 1 }]), '"v2"'),
      res(snapshotBody("bl", 3.0, [{ entry_number: 1 }, { entry_number: 2 }]), '"v3"'),
    ];
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(responses.shift()!)));

    let renders = 0;
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<Host id="bl" onRender={() => { renders += 1; }} />);
    });
    const { getByTestId } = view;
    const afterFirst = renders;
    await tickTimes(2);
    expect(renders).toBeGreaterThan(afterFirst);
    expect(getByTestId("entries").textContent).toBe("2");
  });
});

describe("a 304 is a successful round-trip", () => {
  it("resolves loading and clears a prior error", async () => {
    vi.useFakeTimers();
    const responses = [
      res(snapshotBody("bl", 1.0), '"v1"'),
      new Response("boom", { status: 500 }),
      res(null, '"v1"', 304),
    ];
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(responses.shift()!)));

    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<Host id="bl" onRender={() => {}} />);
    });
    const { getByTestId } = view;
    // 200 consumed during render.
    expect(getByTestId("loading").textContent).toBe("false");
    await tickTimes(1); // the 500
    expect(getByTestId("error").textContent).toBe("HTTP 500");
    await tickTimes(1); // the 304 — a successful round-trip, so it clears it
    expect(getByTestId("error").textContent).toBe("");
  });

  it("keeps the previously parsed snapshot through a run of 304s", async () => {
    vi.useFakeTimers();
    const responses = [
      res(snapshotBody("bl", 1.0, [{ entry_number: 7 }]), '"v1"'),
      res(null, '"v1"', 304),
      res(null, '"v1"', 304),
    ];
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(responses.shift()!)));

    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<Host id="bl" onRender={() => {}} />);
    });
    const { getByTestId } = view;
    await tickTimes(2);
    expect(getByTestId("id").textContent).toBe("bl");
    expect(getByTestId("entries").textContent).toBe("1");
  });
});

describe("the validator never crosses strategies", () => {
  it("re-mounting for a new id polls unconditionally", async () => {
    vi.useFakeTimers();
    const seen: (string | null)[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        seen.push(new Headers(init?.headers).get("If-None-Match"));
        const id = input.includes("/g/") ? "g" : "bl";
        return Promise.resolve(res(snapshotBody(id, 1.0), `"${id}-1"`));
      }),
    );

    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<Host id="bl" onRender={() => {}} />);
    });
    await tickTimes(1);
    await act(async () => {
      view.rerender(<Host id="g" onRender={() => {}} />);
    });
    // The first request for each id must carry NO validator — otherwise one
    // strategy's ETag could be matched against another strategy's URL.
    // [bl: first, bl: conditional, g: first] — the third entry is the pin.
    expect(seen.slice(0, 3)).toEqual([null, '"bl-1"', null]);
  });
});
