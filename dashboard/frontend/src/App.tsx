import { useState, useCallback } from "react";
import { Routes, Route, NavLink, Navigate, useLocation } from "react-router-dom";
import { LayoutDashboard, CalendarDays, BarChart3, Scale, CalendarRange } from "lucide-react";
import { DashboardLayout } from "./components/layout/DashboardLayout";
import { ErrorBoundary } from "./components/shared/ErrorBoundary";
import { Dashboard } from "./pages/Dashboard";
import { History } from "./pages/History";
import { Analytics } from "./pages/Analytics";
import { GroupComparison } from "./pages/GroupComparison";
import { LongStrangle } from "./pages/LongStrangle";
import { useWebSocket } from "./hooks/useWebSocket";
import { useStrategyMeta } from "./hooks/useStrategyMeta";
import { CommandPalette } from "./components/shared/CommandPalette";
import { ToastContainer } from "./components/shared/ToastContainer";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";

// Tabs are now data-driven: one comparison tab per COMPARABLE group from the
// taxonomy (/api/strategies/meta), no hardcoded variant letters. The single
// ad-hoc useComparisonEnabled/useDcEnabled probes are gone — meta is the SSOT.
function NavTabs() {
  const meta = useStrategyMeta();
  // `shrink-0` + `whitespace-nowrap` keep each tab at its natural width inside
  // the scrollable strip below; without them flex would compress the labels
  // into ellipses rather than letting the strip scroll.
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    // `pointer-coarse:min-h-11` is Apple's 44pt HIG floor, applied to the HIT
    // AREA rather than the type: the label stays text-xs, only the target grows.
    // Measured before the change: these tabs were 107x36 on an iPhone 12.
    `flex items-center gap-1.5 px-3 max-sm:px-2 max-sm:gap-1 py-1.5 max-sm:py-2.5
     rounded text-xs font-medium
     transition-colors shrink-0 whitespace-nowrap
     pointer-coarse:min-h-11 pointer-coarse:min-w-11
     pointer-coarse:justify-center ${
      isActive
        ? "bg-bg-elevated text-text-primary"
        : "text-text-secondary hover:text-text-primary"
    }`;

  const comparableGroups = meta.groups.filter((g) => g.comparable);

  return (
    // Horizontally scrollable tab strip — the standard mobile pattern, and the
    // reason this nav no longer forces the page 45% wider than an iPhone. Five
    // tabs (three fixed + one per comparable group) need ~520px; a 390px phone
    // cannot fit them, and hiding tabs would hide whole pages. `nav-scroll`
    // hides the scrollbar chrome while keeping the scroll (see index.css).
    <nav
      className="flex gap-1 max-sm:gap-0.5 px-3 max-sm:px-2 py-1.5 bg-bg border-b border-border-dim
                 overflow-x-auto nav-scroll"
    >
      <NavLink to="/" end className={linkClass}>
        <LayoutDashboard size={14} />
        Dashboard
      </NavLink>
      <NavLink to="/history" className={linkClass}>
        <CalendarDays size={14} />
        History
      </NavLink>
      <NavLink to="/analytics" className={linkClass}>
        <BarChart3 size={14} />
        Analytics
      </NavLink>
      {/* NO standalone "Long Gamma" tab. It was added before the main dashboard
          could render H, and once `data_kind: "long_gamma"` dispatched there it
          became a duplicate of the Dashboard tab whenever H was selected — and
          it sat in the "views OF the selected strategy" row while actually
          being one specific strategy, the exact confusion the rule below
          describes. Pick H in the header; /long-strangle still resolves for a
          bookmarked link. */}
      {/* Two DIFFERENT axes lived in one undifferentiated row until 2026-09-18.
          Dashboard/History/Analytics are views OF the selected strategy; the
          comparison links LEAVE that strategy and show a whole group. Rendered
          identically, nothing told you that clicking one abandoned your
          context. A tab bar means "same thing, different view", and half of
          this one did not. Separated with a rule and labelled. */}
      {comparableGroups.length > 0 && (
        <>
          <span
            className="self-center h-4 w-px bg-border-dim mx-1.5 shrink-0"
            aria-hidden
          />
          {/* The WORD is hidden on a phone, the RULE is not. The rule carries
              the meaning described above (these links leave the selected
              strategy); the word merely restates it, and at 56px of a 360px
              strip it was a sixth of the budget. Measured: the strip needed
              708px in a 360px viewport, which is why the last tab was clipped
              mid-word as "COMP". */}
          <span className="self-center text-3xs font-bold uppercase tracking-wider text-text-dim pr-0.5 shrink-0 max-sm:hidden">
            Compare
          </span>
        </>
      )}
      {comparableGroups.map((g) => (
        // Icon-only below `sm`. "0DTE Iron Condor" + "Multi-day Calendar" are
        // 302px of label on their own; dropping the text (not the link) is what
        // takes the strip from 708px to under the 360px viewport, so nothing is
        // clipped at all. `title` + `aria-label` keep the name for pointer
        // users, screen readers and tests — the destination is unchanged.
        <NavLink
          key={g.id}
          to={`/comparison/${g.id}`}
          className={linkClass}
          title={g.label}
          aria-label={g.label}
        >
          {/* DISTINCT icons per group, because on a phone the label is hidden
              and two identical scales would make "0DTE Iron Condor" and
              "Multi-day Calendar" indistinguishable — worse than the clipped
              label this replaced. Keyed off `pnl_shape`, which the group meta
              already carries, so this adds no second source of truth. */}
          {g.pnl_shape === "debit" ? <CalendarRange size={14} /> : <Scale size={14} />}
          <span className="max-sm:hidden">{g.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

// Legacy /comparison → the first comparable IC (credit) group, else the first
// comparable group. Keeps an old bookmark working under the new structure.
function LegacyComparisonRedirect() {
  const meta = useStrategyMeta();
  if (meta.loading) return null;
  const comparable = meta.groups.filter((g) => g.comparable);
  const ic = comparable.find((g) => g.pnl_shape === "credit") ?? comparable[0];
  if (!ic) return <Navigate to="/" replace />;
  return <Navigate to={`/comparison/${ic.id}`} replace />;
}

// Legacy /dc → the calendar (debit) group's comparison. Falls back to "/" if
// no debit group is registered.
function LegacyDcRedirect() {
  const meta = useStrategyMeta();
  if (meta.loading) return null;
  const cal = meta.groups.find((g) => g.pnl_shape === "debit");
  if (!cal) return <Navigate to="/" replace />;
  return <Navigate to={`/comparison/${cal.id}`} replace />;
}

function App() {
  useWebSocket();
  const [cmdPaletteOpen, setCmdPaletteOpen] = useState(false);
  // Keep the location subscription so a route change re-renders the layout
  // chrome (the Header reads location to scope the picker to the Dashboard tab).
  useLocation();

  const togglePalette = useCallback(() => {
    setCmdPaletteOpen((prev) => !prev);
  }, []);

  useKeyboardShortcuts({ onCommandPalette: togglePalette });

  return (
    <DashboardLayout>
      <NavTabs />
      <div className="mt-3">
        <Routes>
          <Route path="/" element={<ErrorBoundary label="Dashboard"><Dashboard /></ErrorBoundary>} />
          <Route path="/history" element={<ErrorBoundary label="History"><History /></ErrorBoundary>} />
          <Route path="/analytics" element={<ErrorBoundary label="Analytics"><Analytics /></ErrorBoundary>} />
          {/* New group-scoped comparison. Route always registered so a direct
              URL works; the page self-handles unknown/unavailable groups. */}
          <Route path="/comparison/:groupId" element={<ErrorBoundary label="Comparison"><GroupComparison /></ErrorBoundary>} />
          {/* Strategy H's native view. Its own route because every other
              renderer here assumes premium was COLLECTED — "expired worthless"
              is the best outcome there and the WORST one here. */}
          <Route path="/long-strangle" element={<ErrorBoundary label="Long Strangle"><LongStrangle /></ErrorBoundary>} />
          {/* Legacy redirects into the new structure. */}
          <Route path="/comparison" element={<LegacyComparisonRedirect />} />
          <Route path="/dc" element={<LegacyDcRedirect />} />
        </Routes>
      </div>
      <CommandPalette open={cmdPaletteOpen} onClose={() => setCmdPaletteOpen(false)} />
      <ToastContainer />
    </DashboardLayout>
  );
}

export default App;
