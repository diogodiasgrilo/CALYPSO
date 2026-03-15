import { useEffect, useMemo, useState } from "react";
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
  allDates,
  onNavigate,
  onClose,
}: {
  date: string;
  summary: DaySummary | null;
  allDates: string[];
  onNavigate: (date: string) => void;
  onClose: () => void;
}) {
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

    Promise.all([
      fetch(`/api/hydra/entries?date_str=${date}`).then((r) => r.json()),
      fetch(`/api/market/ohlc?date_str=${date}`).then((r) => r.json()),
    ])
      .then(([entryData, ohlcData]) => {
        setEntries(entryData.entries ?? []);
        setStops(entryData.stops ?? []);
        setBars(ohlcData.bars ?? []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [date]);

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
      <div className="fixed inset-4 z-50 bg-bg-deep rounded-xl border border-border overflow-y-auto max-sm:inset-0 max-sm:rounded-none">
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
            <SessionReplay date={date} />
          )}
        </div>
      </div>
    </>,
    document.body
  );
}
