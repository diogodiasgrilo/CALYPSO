import { CheckCircle, Clock, AlertCircle } from "lucide-react";
import { useHydraStore, type AgentInfo } from "../../store/hydraStore";
import { colors } from "../../lib/tradingColors";

const AGENT_SCHEDULE: Record<string, string> = {
  apollo: "8:30 AM",
  hermes: "7:00 PM",
  homer: "7:30 PM",
  clio: "Sat 9:00 AM",
  argus: "Every 15m",
};

export function AgentStatusPanel() {
  const agents = useHydraStore((s) => s.agentStatus);

  if (agents.length === 0) return null;

  return (
    <div>
      <h3 className="label-upper mb-2">Agents</h3>
      <div className="bg-card rounded-lg border border-border-dim p-3">
        <div className="flex flex-wrap gap-4">
          {agents.map((a: AgentInfo) => {
            const hasRun = a.last_run != null;
            const lastRunDate = hasRun ? new Date(a.last_run!) : null;
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
                        timeZone: "America/New_York",
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
