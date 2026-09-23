import { useState, useCallback } from "react";
import { Routes, Route, NavLink, Navigate, useLocation } from "react-router-dom";
import { LayoutDashboard, CalendarDays, BarChart3, Scale, Waves } from "lucide-react";
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
    `flex items-center gap-1.5 px-3 py-1.5 max-sm:py-2.5 rounded text-xs font-medium
     transition-colors shrink-0 whitespace-nowrap ${
      isActive
        ? "bg-bg-elevated text-text-primary"
        : "text-text-secondary hover:text-text-primary"
    }`;

  const comparableGroups = meta.groups.filter((g) => g.comparable);
  // Strategy H's own tab. It is NOT under "Compare": its group is
  // comparable=false (one member, and nothing here shares its P&L shape), and
  // the page is a view OF one strategy rather than a head-to-head. Driven off
  // the taxonomy so it simply does not render until the group is registered.
  const longGamma = meta.groups.find((g) => g.id === "long_gamma_0dte");

  return (
    // Horizontally scrollable tab strip — the standard mobile pattern, and the
    // reason this nav no longer forces the page 45% wider than an iPhone. Five
    // tabs (three fixed + one per comparable group) need ~520px; a 390px phone
    // cannot fit them, and hiding tabs would hide whole pages. `nav-scroll`
    // hides the scrollbar chrome while keeping the scroll (see index.css).
    <nav
      className="flex gap-1 px-3 py-1.5 bg-bg border-b border-border-dim
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
      {longGamma && (
        <NavLink to="/long-strangle" className={linkClass}>
          <Waves size={14} />
          Long Gamma
        </NavLink>
      )}
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
          <span className="self-center text-3xs font-bold uppercase tracking-wider text-text-dim pr-0.5 shrink-0">
            Compare
          </span>
        </>
      )}
      {comparableGroups.map((g) => (
        <NavLink key={g.id} to={`/comparison/${g.id}`} className={linkClass}>
          <Scale size={14} />
          {g.label}
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
