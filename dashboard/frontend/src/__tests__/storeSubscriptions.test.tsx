/**
 * RE-RENDER PINS — the one class of defect this project's six Playwright specs
 * and 42-surface visual audit are structurally blind to.
 *
 * A screenshot cannot see how many times a component rendered to produce it.
 * So the dashboard carried, undetected, seven components calling
 * `useHydraStore()` with NO selector. Zustand treats that as "subscribe to the
 * whole store", so every one of them re-rendered on every store mutation
 * regardless of what it read. The backend pushes `state_update` every 1.0s and
 * `log_lines` every 2.0s (dashboard/backend settings), so steady state was
 * roughly 1.5 full re-renders per second, forever.
 *
 * The worst instance was `useWebSocket()`, which lives in `App` — so the whole
 * routed tree reconciled on every push. Measured on a real iPhone session
 * (2026-11-16, nginx access log): every request answered 200 in ~3ms while the
 * user watched a loading state, i.e. the server was never the problem.
 *
 * HOW THESE TESTS WORK. React's <Profiler> fires `onRender` on every commit of
 * its subtree, so it counts renders without touching the component under test.
 * Each pin asserts a component does NOT re-render for a store change it does
 * not read — and a matching pin asserts it DOES still re-render for one it
 * does, because "never re-renders" would be a worse bug wearing a passing test.
 *
 * `TestTheHarnessCanActuallySeeTheDefect` is the control. It renders a
 * deliberately selector-less component and asserts it DOES re-render. If a
 * future refactor makes these assertions unreachable, that control fails
 * first and says so, instead of the suite going quietly green.
 */
import { Profiler } from "react";
import { render, act, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useHydraStore } from "../store/hydraStore";
import { LiveLogFeed } from "../components/logs/LiveLogFeed";
import type { HydraState, LogEntry, CumulativeMetrics } from "../store/hydraStore";

const PRISTINE = useHydraStore.getState();

afterEach(() => {
  cleanup();
  act(() => {
    useHydraStore.setState(PRISTINE, true);
  });
});

function logLine(msg: string): LogEntry {
  return {
    timestamp: "2026-11-16 12:15:00",
    level: "INFO",
    component: "test",
    message: msg,
  };
}

/** A minimal HydraState. Only identity matters here — these tests assert on
 *  render COUNTS, never on rendered values. */
function hydraState(spx: number): HydraState {
  return { date: "2026-11-16", state: "Running", entries: [], spx_price: spx } as unknown as HydraState;
}

function metrics(total: number): CumulativeMetrics {
  return { total_pnl: total } as unknown as CumulativeMetrics;
}

/** Render `node` under a Profiler and return a live commit counter. */
function mountCounted(node: React.ReactNode) {
  const commits = { n: 0 };
  const result = render(
    <Profiler id="probe" onRender={() => { commits.n += 1; }}>
      {node}
    </Profiler>,
  );
  return { commits, result };
}

/** Seed one log line so LiveLogFeed renders at all (it returns null on empty). */
function seedLogs() {
  act(() => {
    useHydraStore.getState().applyLogLines([logLine("seed")]);
  });
}

describe("LiveLogFeed reads ONLY logLines", () => {
  it("does not re-render when only hydraState changes", () => {
    seedLogs();
    const { commits } = mountCounted(<LiveLogFeed />);
    const afterMount = commits.n;
    expect(afterMount).toBeGreaterThan(0); // it mounted at all

    act(() => {
      useHydraStore.getState().applyStateUpdate(hydraState(7460));
    });
    act(() => {
      useHydraStore.getState().applyStateUpdate(hydraState(7461));
    });

    // Two state pushes it does not read. A selector-less subscription commits
    // twice more here; a scoped one commits zero more.
    expect(commits.n).toBe(afterMount);
  });

  it("does not re-render when only cumulative metrics change", () => {
    seedLogs();
    const { commits } = mountCounted(<LiveLogFeed />);
    const afterMount = commits.n;

    act(() => {
      useHydraStore.getState().applyMetricsUpdate(metrics(1234));
    });

    expect(commits.n).toBe(afterMount);
  });

  it("DOES still re-render when logLines change", () => {
    seedLogs();
    const { commits } = mountCounted(<LiveLogFeed />);
    const afterMount = commits.n;

    act(() => {
      useHydraStore.getState().applyLogLines([logLine("a new line")]);
    });

    // The counterpart pin: scoping the subscription must not cost liveness.
    expect(commits.n).toBeGreaterThan(afterMount);
  });
});

describe("TestTheHarnessCanActuallySeeTheDefect (control)", () => {
  /** The defect, on purpose: subscribe to the whole store, read one field. */
  function BareSubscriber() {
    const { logLines } = useHydraStore();
    return <span>{logLines.length}</span>;
  }

  it("a selector-less component DOES re-render on an unrelated change", () => {
    const { commits } = mountCounted(<BareSubscriber />);
    const afterMount = commits.n;

    act(() => {
      useHydraStore.getState().applyStateUpdate(hydraState(7460));
    });

    // If this ever stops being true, the pins above have become vacuous and
    // the whole file is worthless — so it must fail loudly rather than pass.
    expect(commits.n).toBeGreaterThan(afterMount);
  });
});

describe("useWebSocket must not subscribe to the store at all", () => {
  /** It lives in App. Any subscription there re-renders the entire routed
   *  tree on every push, which is the single largest cost measured. It needs
   *  the store's ACTIONS, which Zustand defines once in `create` and are
   *  therefore stable — so it can read them via getState() and subscribe to
   *  nothing. */
  it("its host does not re-render on store pushes", async () => {
    class FakeWebSocket {
      static OPEN = 1;
      readyState = 0;
      onopen: (() => void) | null = null;
      onmessage: ((e: unknown) => void) | null = null;
      onclose: ((e: unknown) => void) | null = null;
      onerror: (() => void) | null = null;
      close = vi.fn();
      send = vi.fn();
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);

    const { useWebSocket } = await import("../hooks/useWebSocket");

    function Host() {
      useWebSocket();
      // No render counter here: the <Profiler> above is what counts commits.
      // An earlier version incremented a useRef during render, which is both
      // redundant and a react-hooks/refs violation.
      return <span>host</span>;
    }

    const { commits } = mountCounted(<Host />);
    const afterMount = commits.n;

    act(() => {
      useHydraStore.getState().applyStateUpdate(hydraState(7460));
    });
    act(() => {
      useHydraStore.getState().applyLogLines([logLine("x")]);
    });
    act(() => {
      useHydraStore.getState().applyMetricsUpdate(metrics(1));
    });

    expect(commits.n).toBe(afterMount);
  });
});
