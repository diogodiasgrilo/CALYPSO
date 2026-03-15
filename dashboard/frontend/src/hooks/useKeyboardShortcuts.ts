import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useHydraStore } from "../store/hydraStore";

interface ShortcutOptions {
  onCommandPalette: () => void;
}

/** Global keyboard shortcuts. */
export function useKeyboardShortcuts({ onCommandPalette }: ShortcutOptions) {
  const navigate = useNavigate();
  const toggleStrikes = useHydraStore((s) => s.toggleStrikes);
  const toggleMuted = useHydraStore((s) => s.toggleMuted);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      // Cmd+K / Ctrl+K → Command Palette (works even in inputs)
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        onCommandPalette();
        return;
      }

      // Don't fire plain keys when typing in inputs or contentEditable
      const el = e.target as HTMLElement;
      const tag = el.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable) return;

      // Only without modifier keys
      if (!e.metaKey && !e.ctrlKey && !e.altKey) {
        if (e.key === "1") { navigate("/"); return; }
        if (e.key === "2") { navigate("/history"); return; }
        if (e.key === "3") { navigate("/analytics"); return; }
        if (e.key === "s" || e.key === "S") { toggleStrikes(); return; }
        if (e.key === "m" || e.key === "M") { toggleMuted(); return; }
      }

      // Escape closes modals (handled by individual components)
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate, onCommandPalette, toggleStrikes, toggleMuted]);
}
