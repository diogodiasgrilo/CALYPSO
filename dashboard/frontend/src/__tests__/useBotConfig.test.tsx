/**
 * In-flight de-duplication for useBotConfig.
 *
 * Measured on a real page load (nginx access log, 2026-11-16):
 * /api/hydra/bot-config was requested TWICE within the same second, because
 * the hook's cache is only written when a response arrives and three
 * components call the hook on the same mount tick.
 *
 * The control renders the same number of consumers against a hook with no
 * in-flight map and asserts the duplicate requests DO happen — otherwise a
 * passing test here would prove nothing about the real one.
 */
import { render, cleanup, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEffect, useState } from "react";

const BODY = { entry_times: ["12:15", "12:45"], primary_label: "BL · LIVE" };

function jsonOnce() {
  // Params are declared so `spy.mock.calls[n]` types as the real tuple rather
  // than `[]`, and referenced so the unused-args rule is satisfied.
  return vi.fn(async (input: string, init?: RequestInit) => {
    void init;
    return new Response(JSON.stringify({ ...BODY, _url: input }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
}

beforeEach(() => {
  vi.resetModules();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("useBotConfig coalesces concurrent callers", () => {
  it("three components mounting together issue ONE request", async () => {
    const spy = jsonOnce();
    vi.stubGlobal("fetch", spy);
    vi.doMock("../hooks/useSelectedStrategy", () => ({
      useSelectedStrategy: () => ({ strategy: { id: "bl" }, isPrimarySelected: true, loading: false }),
    }));
    const { useBotConfig } = await import("../hooks/useBotConfig");

    function Consumer() {
      const cfg = useBotConfig();
      return <span>{cfg.entry_times.join(",")}</span>;
    }

    await act(async () => {
      render(<><Consumer /><Consumer /><Consumer /></>);
    });

    const botConfigCalls = spy.mock.calls.filter((c) =>
      String(c[0]).includes("/api/hydra/bot-config"));
    expect(botConfigCalls.length).toBe(1);
  });

  it("a FAILED load is retried by a later mount, not cached as requested", async () => {
    let n = 0;
    const spy = vi.fn(async (input: string) => {
      void input;
      n += 1;
      if (n === 1) throw new Error("network down");
      return new Response(JSON.stringify(BODY), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", spy);
    vi.doMock("../hooks/useSelectedStrategy", () => ({
      useSelectedStrategy: () => ({ strategy: { id: "bl" }, isPrimarySelected: true, loading: false }),
    }));
    const { useBotConfig } = await import("../hooks/useBotConfig");

    function Consumer() {
      const cfg = useBotConfig();
      return <span>{cfg.entry_times.join(",")}</span>;
    }

    await act(async () => { render(<Consumer />); });
    cleanup();
    await act(async () => { render(<Consumer />); });

    // The in-flight slot must be cleared on failure too, or the page is stuck
    // with defaults until reload.
    expect(spy.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});

describe("control — a hook WITHOUT in-flight dedup duplicates the request", () => {
  it("three consumers of a resolve-then-cache hook fire three requests", async () => {
    const spy = jsonOnce();
    vi.stubGlobal("fetch", spy);

    // The shape useBotConfig had before the fix: cache written only on
    // arrival, so concurrent mounts all miss.
    const cache = new Map<string, unknown>();
    function useUndeduped() {
      const [, bump] = useState(0);
      useEffect(() => {
        if (cache.has("bl")) return;
        void (globalThis.fetch as unknown as typeof fetch)("/api/hydra/bot-config?strategy_id=bl")
          .then((r) => r.json())
          .then((d) => { cache.set("bl", d); bump((x) => x + 1); });
      }, []);
      return cache.get("bl");
    }
    function Consumer() {
      useUndeduped();
      return <span>x</span>;
    }

    await act(async () => {
      render(<><Consumer /><Consumer /><Consumer /></>);
    });

    expect(spy.mock.calls.length).toBe(3);
  });
});
