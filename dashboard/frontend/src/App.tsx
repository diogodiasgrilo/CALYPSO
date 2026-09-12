import { useState, useCallback } from "react";
import { Routes, Route, NavLink, Navigate, useLocation } from "react-router-dom";
import { LayoutDashboard, CalendarDays, BarChart3, Scale } from "lucide-react";
import { DashboardLayout } from "./components/layout/DashboardLayout";
import { Dashboard } from "./pages/Dashboard";
import { History } from "./pages/History";
import { Analytics } from "./pages/Analytics";
import { GroupComparison } from "./pages/GroupComparison";
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
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
      isActive
        ? "bg-bg-elevated text-text-primary"
        : "text-text-secondary hover:text-text-primary"
    }`;

  const comparableGroups = meta.groups.filter((g) => g.comparable);

  return (
    <nav className="flex gap-1 px-3 py-1.5 bg-bg border-b border-border-dim">
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
          <Route path="/" element={<Dashboard />} />
          <Route path="/history" element={<History />} />
          <Route path="/analytics" element={<Analytics />} />
          {/* New group-scoped comparison. Route always registered so a direct
              URL works; the page self-handles unknown/unavailable groups. */}
          <Route path="/comparison/:groupId" element={<GroupComparison />} />
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
