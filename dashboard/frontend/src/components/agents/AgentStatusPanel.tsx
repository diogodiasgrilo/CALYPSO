import { useEffect, useState } from "react";
import { CheckCircle, Clock, AlertCircle } from "lucide-react";
import { colors } from "../../lib/tradingColors";

interface AgentInfo {
  agent: string;
  last_run: string | null;
  last_file: string | null;
  available: boolean;
}

const AGENT_SCHEDULE: Record<string, string> = {
  apollo: "8:30 AM",
  hermes: "5:00 PM",
  homer: "5:30 PM",
  clio: "Sat 9:00 AM",
  argus: "Every 15m",
};

export function AgentStatusPanel() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);

  useEffect(() => {
    fetch("/api/agents/status")
      .then((r) => r.json())
      .then((data) => setAgents(data.agents ?? []))
      .catch(() => {});
  }, []);

  if (agents.length === 0) return null;

  return (
    <div>
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
        Agents
      </h3>
      <div className="bg-card rounded-lg border border-border-dim p-3">
        <div className="flex flex-wrap gap-4">
          {agents.map((a) => {
            const hasRun = a.last_run != null;
            const lastRunDate = hasRun ? new Date(a.last_run!) : null;
            // "Recent" = ran today (same ET calendar date)
            const nowET = new Date().toLocaleDateString("en-US", { timeZone: "America/New_York" });
            const runET = lastRunDate
              ? lastRunDate.toLocaleDateString("en-US", { timeZone: "America/New_York" })
              : null;
            const isRecent = runET === nowET;

            return (
              <div
                key={a.agent}
                className="flex items-center gap-1.5 text-xs"
              >
                {hasRun && isRecent ? (
                  <CheckCircle size={12} color={colors.profit} />
                ) : hasRun ? (
                  <Clock size={12} color={colors.warning} />
                ) : (
                  <AlertCircle size={12} color={colors.textDim} />
                )}
                <span className="text-text-primary uppercase font-semibold">
                  {a.agent}
                </span>
                <span className="text-text-dim">
                  {hasRun
                    ? lastRunDate!.toLocaleTimeString("en-US", {
                        hour: "numeric",
                        minute: "2-digit",
                        hour12: true,
                      })
                    : AGENT_SCHEDULE[a.agent] ?? "—"}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
