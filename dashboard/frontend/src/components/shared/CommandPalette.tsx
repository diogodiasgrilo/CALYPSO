import { useState, useEffect, useRef, useCallback, useId } from "react";
import { useNavigate } from "react-router-dom";
import { Search, LayoutDashboard, CalendarDays, BarChart3, Download, Volume2, VolumeX, Eye } from "lucide-react";
import { useHydraStore } from "../../store/hydraStore";
import { exportDailySummariesCSV } from "../../lib/exportUtils";

interface Command {
  id: string;
  label: string;
  icon: React.ReactNode;
  action: () => void;
  keywords: string;
  shortcut?: string;
}

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const listboxId = useId();

  const toggleStrikes = useHydraStore((s) => s.toggleStrikes);
  const toggleMuted = useHydraStore((s) => s.toggleMuted);
  const muted = useHydraStore((s) => s.muted);

  const commands: Command[] = [
    {
      id: "dashboard",
      label: "Go to Dashboard",
      icon: <LayoutDashboard size={14} />,
      action: () => { navigate("/"); onClose(); },
      keywords: "dashboard home main live",
      shortcut: "1",
    },
    {
      id: "history",
      label: "Go to History",
      icon: <CalendarDays size={14} />,
      action: () => { navigate("/history"); onClose(); },
      keywords: "history calendar past days",
      shortcut: "2",
    },
    {
      id: "analytics",
      label: "Go to Analytics",
      icon: <BarChart3 size={14} />,
      action: () => { navigate("/analytics"); onClose(); },
      keywords: "analytics charts performance stats",
      shortcut: "3",
    },
    {
      id: "strikes",
      label: "Toggle Strike Lines",
      icon: <Eye size={14} />,
      action: () => { toggleStrikes(); onClose(); },
      keywords: "strikes lines chart toggle show hide",
      shortcut: "S",
    },
    {
      id: "mute",
      label: muted ? "Unmute Sound" : "Mute Sound",
      icon: muted ? <VolumeX size={14} /> : <Volume2 size={14} />,
      action: () => { toggleMuted(); onClose(); },
      keywords: "sound mute unmute audio",
      shortcut: "M",
    },
    {
      id: "export",
      label: "Export CSV",
      icon: <Download size={14} />,
      action: () => {
        fetch(`/api/metrics/daily?year=${new Date().getFullYear()}`)
          .then((r) => r.json())
          .then((data) => {
            if (data.summaries?.length > 0) {
              exportDailySummariesCSV(data.summaries);
            }
          })
          .catch(() => {});
        onClose();
      },
      keywords: "export csv download data",
    },
  ];

  const filtered = query
    ? commands.filter(
        (c) =>
          c.label.toLowerCase().includes(query.toLowerCase()) ||
          c.keywords.toLowerCase().includes(query.toLowerCase())
      )
    : commands;

  useEffect(() => {
    if (open) {
      setQuery("");
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = "";
      };
    }
  }, [open]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  const executeSelected = useCallback(() => {
    if (filtered[selectedIndex]) {
      filtered[selectedIndex].action();
    }
  }, [filtered, selectedIndex]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => {
          const next = Math.min(i + 1, filtered.length - 1);
          listRef.current?.children[next]?.scrollIntoView({ block: "nearest" });
          return next;
        });
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => {
          const next = Math.max(i - 1, 0);
          listRef.current?.children[next]?.scrollIntoView({ block: "nearest" });
          return next;
        });
      } else if (e.key === "Enter") {
        e.preventDefault();
        executeSelected();
      } else if (e.key === "Escape") {
        onClose();
      }
    },
    [filtered.length, executeSelected, onClose]
  );

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] cmd-backdrop flex items-start justify-center pt-[20vh]" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="bg-bg-elevated rounded-xl border border-border-dim shadow-2xl w-full max-w-md overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search input */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border-dim">
          <Search size={16} className="text-text-dim shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a command..."
            role="combobox"
            aria-expanded="true"
            aria-autocomplete="list"
            aria-controls={listboxId}
            className="flex-1 bg-transparent text-sm text-text-primary placeholder-text-dim outline-none"
          />
          <kbd className="text-[10px] text-text-dim bg-card px-1.5 py-0.5 rounded">ESC</kbd>
        </div>

        {/* Results */}
        <div ref={listRef} id={listboxId} role="listbox" className="max-h-64 overflow-y-auto py-1">
          {filtered.length === 0 ? (
            <div className="px-4 py-6 text-center text-xs text-text-dim">
              No commands found
            </div>
          ) : (
            filtered.map((cmd, i) => (
              <button
                key={cmd.id}
                role="option"
                aria-selected={i === selectedIndex}
                onClick={cmd.action}
                className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
                  i === selectedIndex
                    ? "bg-card text-text-primary"
                    : "text-text-secondary hover:bg-card/50"
                }`}
                onMouseEnter={() => setSelectedIndex(i)}
              >
                <span className="text-text-dim">{cmd.icon}</span>
                <span className="flex-1 text-left">{cmd.label}</span>
                {cmd.shortcut && (
                  <kbd className="text-[10px] text-text-dim bg-bg px-1.5 py-0.5 rounded">{cmd.shortcut}</kbd>
                )}
              </button>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center gap-3 px-4 py-2 border-t border-border-dim text-[10px] text-text-dim">
          <span><kbd className="bg-card px-1 py-0.5 rounded">↑↓</kbd> Navigate</span>
          <span><kbd className="bg-card px-1 py-0.5 rounded">↵</kbd> Select</span>
        </div>
      </div>
    </div>
  );
}
