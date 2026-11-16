import { useHydraStore } from "../../store/hydraStore";

export function StatusBar() {
  // Scoped selectors, not a bare `useHydraStore()`. Calling the hook with no
  // selector subscribes to the WHOLE store, so this component re-rendered on
  // every unrelated push (~1.5/s from the backend's 1s state + 2s log loops).
  // One selector per field: each re-renders only when the field it reads
  // actually changes. Pinned by src/__tests__/storeSubscriptions.test.tsx.
  const hydraState = useHydraStore((s) => s.hydraState);
  const logLines = useHydraStore((s) => s.logLines);
  const clientCount = useHydraStore((s) => s.clientCount);

  const lastLog = logLines.length > 0 ? logLines[logLines.length - 1] : null;
  const botState = hydraState?.state ?? "Unknown";

  return (
    <footer className="flex items-center justify-between px-4 py-1.5 bg-bg border-t border-border-dim text-xs text-text-secondary">
      <div className="flex items-center gap-2 truncate min-w-0 flex-1">
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
      <div className="flex items-center gap-3 shrink-0 whitespace-nowrap pl-3">
        <span>{clientCount} client{clientCount !== 1 ? "s" : ""}</span>
        <span className="text-text-dim">
          {hydraState?.date ?? ""}
        </span>
      </div>
    </footer>
  );
}
