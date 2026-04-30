import { useState, useCallback, useEffect } from "react";
import { Routes, Route, NavLink } from "react-router-dom";
import { LayoutDashboard, CalendarDays, BarChart3, Scale } from "lucide-react";
import { DashboardLayout } from "./components/layout/DashboardLayout";
import { Dashboard } from "./pages/Dashboard";
import { History } from "./pages/History";
import { Analytics } from "./pages/Analytics";
import { Comparison } from "./pages/Comparison";
import { useWebSocket } from "./hooks/useWebSocket";
import { CommandPalette } from "./components/shared/CommandPalette";
import { ToastContainer } from "./components/shared/ToastContainer";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";

// Comparison-mode flag from backend. We fetch /api/variants/health once on
// mount so the nav tab is hidden cleanly when comparison mode is off — same
// gating as the backend endpoints (single source of truth lives in
// dashboard/backend/config.py:Settings.comparison_mode_enabled).
function useComparisonEnabled() {
  const [enabled, setEnabled] = useState<boolean>(false);
  useEffect(() => {
    fetch("/api/variants/health")
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => setEnabled(Boolean(j?.enabled)))
      .catch(() => setEnabled(false));
  }, []);
  return enabled;
}

function NavTabs({ comparisonEnabled }: { comparisonEnabled: boolean }) {
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
      isActive
        ? "bg-bg-elevated text-text-primary"
        : "text-text-secondary hover:text-text-primary"
    }`;

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
      {comparisonEnabled && (
        <NavLink to="/comparison" className={linkClass}>
          <Scale size={14} />
          Comparison
        </NavLink>
      )}
    </nav>
  );
}

function App() {
  useWebSocket();
  const [cmdPaletteOpen, setCmdPaletteOpen] = useState(false);
  const comparisonEnabled = useComparisonEnabled();

  const togglePalette = useCallback(() => {
    setCmdPaletteOpen((prev) => !prev);
  }, []);

  useKeyboardShortcuts({ onCommandPalette: togglePalette });

  return (
    <DashboardLayout>
      <NavTabs comparisonEnabled={comparisonEnabled} />
      <div className="mt-3">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/history" element={<History />} />
          <Route path="/analytics" element={<Analytics />} />
          {/* Always register the route so direct URL works when enabled.
              The page itself self-protects with a "disabled" notice when
              comparison_mode_enabled is false on the backend. */}
          <Route path="/comparison" element={<Comparison />} />
        </Routes>
      </div>
      <CommandPalette open={cmdPaletteOpen} onClose={() => setCmdPaletteOpen(false)} />
      <ToastContainer />
    </DashboardLayout>
  );
}

export default App;
