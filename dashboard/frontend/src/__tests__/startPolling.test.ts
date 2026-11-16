/**
 * Pins for the non-overlapping poller. The control proves a BARE setInterval
 * does overlap under the same conditions — without it these tests could pass
 * against a plain setInterval and prove nothing.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { startPolling } from "../lib/startPolling";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/** A task that stays pending until released. */
function controllable() {
  const releases: Array<() => void> = [];
  let started = 0;
  const task = () => {
    started += 1;
    return new Promise<void>((resolve) => releases.push(resolve));
  };
  return {
    task,
    get started() { return started; },
    releaseAll() { releases.splice(0).forEach((r) => r()); },
  };
}

describe("startPolling", () => {
  it("fires once immediately", () => {
    vi.useFakeTimers();
    const c = controllable();
    const stop = startPolling(c.task, 2000);
    expect(c.started).toBe(1);
    stop();
  });

  it("does not start a second tick while the first is still in flight", async () => {
    vi.useFakeTimers();
    const c = controllable();
    const stop = startPolling(c.task, 2000);
    expect(c.started).toBe(1);

    // Five intervals elapse with the first request still hanging. A bare
    // setInterval would now have five more in flight.
    for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(2000);
    expect(c.started).toBe(1);

    stop();
  });

  it("resumes on the next interval once the in-flight tick settles", async () => {
    vi.useFakeTimers();
    const c = controllable();
    const stop = startPolling(c.task, 2000);

    await vi.advanceTimersByTimeAsync(2000);
    expect(c.started).toBe(1);

    c.releaseAll();
    await vi.advanceTimersByTimeAsync(0);   // let the finally run
    await vi.advanceTimersByTimeAsync(2000);
    expect(c.started).toBe(2);

    stop();
  });

  it("a REJECTED tick still releases the latch", async () => {
    vi.useFakeTimers();
    let started = 0;
    const task = () => {
      started += 1;
      return Promise.reject(new Error("network error"));
    };
    const stop = startPolling(task, 1000);
    expect(started).toBe(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(started).toBe(2);           // not wedged at 1 forever
    await vi.advanceTimersByTimeAsync(1000);
    expect(started).toBe(3);

    stop();
  });

  it("stops firing after the stop function is called", async () => {
    vi.useFakeTimers();
    const c = controllable();
    const stop = startPolling(c.task, 1000);
    c.releaseAll();
    await vi.advanceTimersByTimeAsync(0);
    stop();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(c.started).toBe(1);
  });

  it("a tick in flight when stop() is called cannot restart polling", async () => {
    vi.useFakeTimers();
    const c = controllable();
    const stop = startPolling(c.task, 1000);
    stop();
    c.releaseAll();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(c.started).toBe(1);
  });
});

describe("control — a bare setInterval DOES overlap", () => {
  it("accumulates in-flight ticks under the same conditions", async () => {
    vi.useFakeTimers();
    const c = controllable();
    const id = setInterval(() => void c.task(), 2000);
    c.task();                                   // the immediate fire
    for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(2000);
    clearInterval(id);
    // 1 immediate + 5 intervals, none of them waiting for the others.
    expect(c.started).toBe(6);
  });
});
