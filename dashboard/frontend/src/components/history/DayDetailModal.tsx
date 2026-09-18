import { useEffect, useMemo, useState, useRef } from "react";
import { createPortal } from "react-dom";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { DayDetailSummary } from "./DayDetailSummary";
import { DayDetailChart } from "./DayDetailChart";
import { DayDetailEntries } from "./DayDetailEntries";
import { SessionReplay } from "./SessionReplay";
import type { DaySummary, DayEntry, DayStop, OHLCBar } from "./types";

type DetailTab = "overview" | "replay";

export function DayDetailModal({
  date,
  summary,
  strategyId = "",
  allDates,
  onNavigate,
  onClose,
}: {
  date: string;
  summary: DaySummary | null;
  strategyId?: string;
  allDates: string[];
  onNavigate: (date: string) => void;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [entries, setEntries] = useState<DayEntry[]>([]);
  const [stops, setStops] = useState<DayStop[]>([]);
  const [bars, setBars] = useState<OHLCBar[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailTab, setDetailTab] = useState<DetailTab>("overview");

  // Fetch detail data
  useEffect(() => {
    setLoading(true);
    setEntries([]);
    setStops([]);
    setBars([]);

    // Entries/stops are scoped to the picked variant so the tables match the
    // header cards (which come from /api/metrics/daily?strategy_id=…). The SPX
    // OHLC chart is intentionally variant-agnostic (same index, densest source);
    // only its entry/stop markers — which ride on `entries`/`stops` — need scoping.
    const sid = encodeURIComponent(strategyId);
    Promise.all([
      fetch(`/api/hydra/entries?date_str=${date}&strategy_id=${sid}`).then((r) => r.json()),
      fetch(`/api/market/ohlc?date_str=${date}`).then((r) => r.json()),
    ])
      .then(([entryData, ohlcData]) => {
        setEntries(entryData.entries ?? []);
        setStops(entryData.stops ?? []);
        setBars(ohlcData.bars ?? []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [date, strategyId]);

  // Navigation
  const sortedDates = useMemo(
    () => [...allDates].sort((a, b) => a.localeCompare(b)),
    [allDates]
  );
  const currentIdx = sortedDates.indexOf(date);
  const prevDate = currentIdx > 0 ? sortedDates[currentIdx - 1] : null;
  const nextDate =
    currentIdx < sortedDates.length - 1 ? sortedDates[currentIdx + 1] : null;

  // Keyboard: Escape, Left/Right arrows
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft" && prevDate) onNavigate(prevDate);
      if (e.key === "ArrowRight" && nextDate) onNavigate(nextDate);
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose, onNavigate, prevDate, nextDate]);

  // Focus trap + restore.
  //
  // Measured 2026-09-18 with uiaudit/diag-keyboard.mjs: this modal had five tab
  // stops, after which Tab walked the ENTIRE page behind it — header, strategy
  // picker, mute, sign-out, all five nav links, the year picker, Export CSV —
  // every one of them hidden behind the backdrop. WCAG 2.4.3.
  //
  // Listens in the CAPTURE phase so it sees Tab before anything inside the
  // panel can consume it, and restores focus to whatever opened the modal on
  // unmount; without that a keyboard user is dropped at the top of the
  // document. Runs once per mount — navigating between days re-renders but
  // does not remount, which is correct: focus should stay where the user put
  // it while stepping through dates.
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();

    function trap(e: KeyboardEvent) {
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusable = Array.from(
        panel.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], select, input, textarea, [tabindex]:not([tabindex="-1"])'
        )
      ).filter((el) => el.offsetParent !== null);
      if (focusable.length === 0) {
        e.preventDefault();
        panel.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      const inside = panel.contains(active);
      if (e.shiftKey && (active === first || !inside)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !inside)) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", trap, true);
    return () => {
      document.removeEventListener("keydown", trap, true);
      previouslyFocused?.focus?.();
    };
  }, []);

  // Lock body scroll
  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  // Format date for header
  const dateObj = new Date(date + "T12:00:00");
  const dateFormatted = dateObj.toLocaleDateString("en-US", {
    weekday: "long",
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  return createPortal(
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Panel */}
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={`Trading detail for ${dateFormatted}`}
        className="fixed inset-4 z-50 bg-bg-deep rounded-xl border border-border overflow-y-auto max-sm:inset-0 max-sm:rounded-none focus:outline-none"
      >
        {/* Header */}
        <div className="sticky top-0 z-10 flex items-center justify-between px-5 py-3 bg-bg-deep border-b border-border-dim">
          <div className="flex items-center gap-2">
            <button
              onClick={() => prevDate && onNavigate(prevDate)}
              disabled={!prevDate}
              className="p-1 rounded-md hover:bg-bg-elevated transition-colors text-text-secondary hover:text-text-primary disabled:opacity-20 disabled:cursor-not-allowed"
              title={prevDate ? `Previous: ${prevDate}` : undefined}
            >
              <ChevronLeft size={16} />
            </button>
            <h3 className="text-sm font-semibold text-text-primary">
              {dateFormatted}
            </h3>
            <button
              onClick={() => nextDate && onNavigate(nextDate)}
              disabled={!nextDate}
              className="p-1 rounded-md hover:bg-bg-elevated transition-colors text-text-secondary hover:text-text-primary disabled:opacity-20 disabled:cursor-not-allowed"
              title={nextDate ? `Next: ${nextDate}` : undefined}
            >
              <ChevronRight size={16} />
            </button>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-bg-elevated transition-colors text-text-secondary hover:text-text-primary"
          >
            <X size={16} />
          </button>
        </div>

        {/* Tab selector */}
        <div className="flex gap-1 mx-5 mt-3 bg-bg rounded-lg p-1 w-fit">
          {(["overview", "replay"] as DetailTab[]).map((tab) => (
            <button
              key={tab}
              onClick={() => setDetailTab(tab)}
              className={`px-3 py-1 text-xs font-semibold uppercase tracking-wider rounded-md transition-colors ${
                detailTab === tab
                  ? "bg-bg-elevated text-text-primary"
                  : "text-text-dim hover:text-text-secondary"
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="p-5 space-y-5">
          {detailTab === "overview" && (
            <>
              {/* Summary stats (instant — uses pre-loaded summary) */}
              {summary && <DayDetailSummary summary={summary} />}

              {loading ? (
                <div className="flex items-center justify-center h-40 text-text-dim text-xs">
                  Loading day details...
                </div>
              ) : (
                <>
                  {/* SPX Chart */}
                  <DayDetailChart date={date} bars={bars} entries={entries} stops={stops} />

                  {/* Entries + Stops Tables */}
                  <DayDetailEntries entries={entries} stops={stops} />
                </>
              )}
            </>
          )}

          {detailTab === "replay" && (
            <SessionReplay date={date} strategyId={strategyId} />
          )}
        </div>
      </div>
    </>,
    document.body
  );
}
