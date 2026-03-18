import { useEffect, useState, useCallback } from "react";
import { SimCountdown } from "../components/simulator/SimCountdown";
import { SimControls, DEFAULT_PARAMS } from "../components/simulator/SimControls";
import type { SimParamsState } from "../components/simulator/SimControls";
import { SimResults } from "../components/simulator/SimResults";
import { SimEquityCurve } from "../components/simulator/SimEquityCurve";
import { SimDayTable } from "../components/simulator/SimDayTable";

interface StatusData {
  required_days: number;
  full_sim_days: number;
  total_trading_days: number;
  data_start_date: string;
  full_sim_dates: string[];
  all_dates: string[];
  ready: boolean;
  days_remaining: number;
}

export function Simulator() {
  const [status, setStatus] = useState<StatusData | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [params, setParams] = useState<SimParamsState>({ ...DEFAULT_PARAMS });
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [simLoading, setSimLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch status on mount
  useEffect(() => {
    fetch("/api/simulator/status")
      .then((r) => r.json())
      .then((data) => {
        setStatus(data);
        setStatusLoading(false);
      })
      .catch(() => {
        setError("Failed to load simulator status");
        setStatusLoading(false);
      });
  }, []);

  const runSimulation = useCallback(async () => {
    setSimLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/simulator/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setResult(data);
    } catch (e) {
      setError(`Simulation failed: ${e instanceof Error ? e.message : "unknown error"}`);
    } finally {
      setSimLoading(false);
    }
  }, [params]);

  // Loading state
  if (statusLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-text-secondary text-sm">
        Loading simulator...
      </div>
    );
  }

  // Error state
  if (error && !result) {
    return (
      <div className="flex items-center justify-center h-64 text-loss text-sm">
        {error}
      </div>
    );
  }

  // Countdown state — not enough data yet
  if (status && !status.ready) {
    return (
      <SimCountdown
        requiredDays={status.required_days}
        collectedDays={status.full_sim_days}
        dataStartDate={status.data_start_date}
        allDates={status.full_sim_dates}
      />
    );
  }

  // Simulator ready
  const days = (result as Record<string, unknown>)?.days as Array<Record<string, unknown>> | undefined;

  return (
    <div className="space-y-3 px-3 pb-6">
      {/* Controls */}
      <SimControls
        params={params}
        onChange={setParams}
        onRun={runSimulation}
        loading={simLoading}
      />

      {/* Error banner */}
      {error && (
        <div className="bg-card rounded-lg border border-loss/30 p-3 text-xs text-loss">
          {error}
        </div>
      )}

      {/* Results (only shown after first run) */}
      {result && (
        <>
          <SimResults result={result as never} />
          <SimEquityCurve days={(days as never[]) ?? []} />
          <SimDayTable days={(days as never[]) ?? []} />
        </>
      )}

      {/* Prompt to run */}
      {!result && !simLoading && (
        <div className="flex flex-col items-center justify-center py-16 text-text-dim text-sm">
          <p>Adjust parameters above and click <strong className="text-text-secondary">Run Simulation</strong> to see results.</p>
          <p className="text-xs mt-1">
            {status?.total_trading_days ?? 0} trading days available
            ({status?.full_sim_days ?? 0} with full spread data)
          </p>
        </div>
      )}
    </div>
  );
}
