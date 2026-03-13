import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { DayDetailSummary } from "./DayDetailSummary";
import { DayDetailChart } from "./DayDetailChart";
import { DayDetailEntries } from "./DayDetailEntries";
import type { DaySummary, DayEntry, DayStop, OHLCBar } from "./types";

export function DayDetailModal({
  date,
  summary,
  onClose,
}: {
  date: string;
  summary: DaySummary | null;
  onClose: () => void;
}) {
  const [entries, setEntries] = useState<DayEntry[]>([]);
  const [stops, setStops] = useState<DayStop[]>([]);
  const [bars, setBars] = useState<OHLCBar[]>([]);
  const [loading, setLoading] = useState(true);

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

  // Escape key
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

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
          <h3 className="text-sm font-semibold text-text-primary">
            {dateFormatted}
          </h3>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-bg-elevated transition-colors text-text-secondary hover:text-text-primary"
          >
            <X size={16} />
          </button>
        </div>

        {/* Content */}
        <div className="p-5 space-y-5">
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
        </div>
      </div>
    </>,
    document.body
  );
}
