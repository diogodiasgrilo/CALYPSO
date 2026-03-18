import { useState, useCallback } from "react";
import { Routes, Route, NavLink } from "react-router-dom";
import { LayoutDashboard, CalendarDays, BarChart3, FlaskConical } from "lucide-react";
import { DashboardLayout } from "./components/layout/DashboardLayout";
import { Dashboard } from "./pages/Dashboard";
import { History } from "./pages/History";
import { Analytics } from "./pages/Analytics";
import { Simulator } from "./pages/Simulator";
import { useWebSocket } from "./hooks/useWebSocket";
import { CommandPalette } from "./components/shared/CommandPalette";
import { ToastContainer } from "./components/shared/ToastContainer";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";

function NavTabs() {
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
      <NavLink to="/simulator" className={linkClass}>
        <FlaskConical size={14} />
        Simulator
      </NavLink>
    </nav>
  );
}

function App() {
  useWebSocket();
  const [cmdPaletteOpen, setCmdPaletteOpen] = useState(false);

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
          <Route path="/simulator" element={<Simulator />} />
        </Routes>
      </div>
      <CommandPalette open={cmdPaletteOpen} onClose={() => setCmdPaletteOpen(false)} />
      <ToastContainer />
    </DashboardLayout>
  );
}

export default App;
