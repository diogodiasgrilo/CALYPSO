/** Catch a render-time throw and SAY SO, instead of blanking the page.
 *
 * Added 2026-09-18. There was no error boundary anywhere in the app, so any
 * exception thrown during render unmounted the whole tree and left a white
 * page with nothing on it — no message, no reload affordance, no indication
 * that anything had gone wrong at all.
 *
 * That is not a theoretical concern. Measured with uiaudit/diag-degraded.mjs,
 * which replays each surface against the data shapes production actually
 * produces rather than the happy-path fixtures:
 *
 *   backend returns 500          dashboard + /dc  -> white page
 *   a date field arrives null    history + analytics -> white page
 *   numbers arrive as strings    history + both comparisons + /dc -> white page
 *
 * The 500 case is the one that matters most: it happens every time
 * dashboard.service restarts, and the operator sees a blank browser rather than
 * "the backend is down". A boundary cannot make bad data good, but it turns
 * every one of these — including the modes nobody has thought of yet — into a
 * visible, explained state with a way out.
 *
 * Deliberately a class component: `componentDidCatch` /
 * `getDerivedStateFromError` have no hook equivalent, and React still offers no
 * function-component API for this.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCw } from "lucide-react";
import { colors } from "../../lib/tradingColors";

interface Props {
  children: ReactNode;
  /** Shown in the message so the operator knows WHICH part failed. */
  label?: string;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // The console is the only place this can go — the dashboard is read-only
    // and deliberately has no write path back to the server, so there is
    // nothing to report to. Logged in full because the on-screen message is
    // deliberately short.
    console.error("[ErrorBoundary]", this.props.label ?? "app", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div
        role="alert"
        className="flex flex-col items-center justify-center gap-3 p-8 text-center"
        style={{ minHeight: "12rem" }}
      >
        <span
          className="flex items-center gap-2 text-sm font-semibold"
          style={{ color: colors.loss }}
        >
          <AlertTriangle size={16} />
          {this.props.label ? `${this.props.label} failed to render` : "Something went wrong"}
        </span>
        <p className="text-text-secondary text-xs max-w-md leading-relaxed">
          This panel hit an error and stopped. The bot is unaffected — the
          dashboard is read-only and never writes to it. If the backend was
          restarting, reloading should clear this.
        </p>
        <code className="text-text-dim text-2xs font-mono max-w-md break-all">
          {String(error.message || error).slice(0, 200)}
        </code>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="min-h-11 flex items-center justify-center gap-2 rounded-lg px-4 text-sm font-bold tracking-wide"
          style={{ backgroundColor: colors.profit, color: "#0d1117" }}
        >
          <RotateCw size={14} />
          Reload
        </button>
      </div>
    );
  }
}
