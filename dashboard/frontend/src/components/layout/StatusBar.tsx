import { useHydraStore } from "../../store/hydraStore";

export function StatusBar() {
  const { hydraState, logLines, clientCount } = useHydraStore();

  const lastLog = logLines.length > 0 ? logLines[logLines.length - 1] : null;
  const botState = hydraState?.state ?? "Unknown";

  return (
    <footer className="flex items-center justify-between px-4 py-1.5 bg-bg border-t border-border-dim text-xs text-text-secondary">
      <div className="flex items-center gap-2 truncate max-w-[70%]">
        <span className="text-text-dim">BOT:</span>
        <span className="text-text-primary">{botState}</span>
        {lastLog && (
          <>
            <span className="text-text-dim mx-1">|</span>
            <span className="truncate">
              {lastLog.timestamp && (
                <span className="text-text-dim mr-1">{lastLog.timestamp.slice(11, 19)}</span>
              )}
              {lastLog.message}
            </span>
          </>
        )}
      </div>
      <div className="flex items-center gap-3">
        <span>{clientCount} client{clientCount !== 1 ? "s" : ""}</span>
        <span className="text-text-dim">
          {hydraState?.date ?? ""}
        </span>
      </div>
    </footer>
  );
}
