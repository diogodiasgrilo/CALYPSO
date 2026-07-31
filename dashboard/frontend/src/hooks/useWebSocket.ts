/** WebSocket hook with exponential backoff reconnection and heartbeat timeout. */

import { useEffect, useRef, useCallback } from "react";
import { useHydraStore } from "../store/hydraStore";
import { useSelectedSnapshotStore } from "../components/dashboard/selectedSnapshotStore";

/** True when the dashboard is currently showing a NON-primary strategy.
 *  StrategyDashboard publishes the polled snapshot ONLY for a non-primary
 *  selection (null on the primary), so a non-null snapshot here means "not the
 *  primary" — exactly the cross-strategy-toast guard (audit AUD-3-F1). */
function isViewingNonPrimary(): boolean {
  return useSelectedSnapshotStore.getState().snapshot !== null;
}

const MAX_RECONNECT_DELAY = 30_000;
/** If no message received for this long, declare connection dead and reconnect. */
const HEARTBEAT_TIMEOUT_MS = 60_000;

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelay = useRef(1000);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const heartbeatTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const {
    setConnectionStatus,
    applySnapshot,
    applyStateUpdate,
    applyMetricsUpdate,
    applyMarketStatus,
    applyOHLCUpdate,
    applyLogLines,
    applyStopEvents,
    applyAgentsUpdate,
    applyComparisons,
    applyPerformanceUpdate,
    addToast,
    setClientCount,
  } = useHydraStore();

  /** Reset heartbeat timeout — called on every received message. */
  const resetHeartbeatTimer = useCallback(() => {
    if (heartbeatTimer.current) clearTimeout(heartbeatTimer.current);
    heartbeatTimer.current = setTimeout(() => {
      // No message for 60s — connection is zombie
      console.warn("[WS] Heartbeat timeout — reconnecting");
      setConnectionStatus("disconnected");
      wsRef.current?.close();
    }, HEARTBEAT_TIMEOUT_MS);
  }, [setConnectionStatus]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    setConnectionStatus("connecting");

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    // Session cookie rides along automatically (browsers always attach
    // matching cookies to same-origin WebSocket handshakes) — no secret in
    // the URL, unlike the old ?api_key= scheme.
    const url = `${protocol}//${host}/ws/dashboard`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnectionStatus("connected");
      reconnectDelay.current = 1000; // Reset backoff
      resetHeartbeatTimer(); // Start heartbeat monitoring
    };

    ws.onmessage = (event) => {
      // Reset heartbeat on ANY message
      resetHeartbeatTimer();

      try {
        const msg = JSON.parse(event.data);

        switch (msg.type) {
          case "snapshot":
            applySnapshot(msg);
            break;
          case "state_update":
            applyStateUpdate(msg.data);
            break;
          case "metrics_update":
            applyMetricsUpdate(msg.data);
            break;
          case "market_status":
            applyMarketStatus(msg.data);
            break;
          case "ohlc_update":
            applyOHLCUpdate(msg.data);
            break;
          case "log_lines":
            applyLogLines(msg.data);
            break;
          case "stop_events":
            applyStopEvents(msg.data);
            // Toast notification for new stops — the WS stream tracks the
            // PRIMARY strategy only. Suppress these toasts while the user is
            // viewing a DIFFERENT strategy on the dashboard, so a primary-C
            // stop doesn't pop while the body shows E (audit AUD-3-F1). The
            // stopEvents themselves are still applied (harmless; the primary
            // view reads them). null/unset selection = primary = show toasts.
            if (!isViewingNonPrimary()) {
              for (const stop of msg.data) {
                if (stop.stop_time) {
                  addToast({
                    type: "stop",
                    title: `Stop Triggered`,
                    message: `Entry #${stop.entry_number} ${stop.side} side stopped`,
                  });
                }
              }
            }
            break;
          case "agents_update":
            applyAgentsUpdate(msg.data);
            break;
          case "performance_update":
            if (msg.data?.daily_pnls) applyPerformanceUpdate(msg.data.daily_pnls);
            break;
          case "comparisons_update":
            applyComparisons(msg.data);
            break;
          case "heartbeat":
            if (msg.clients != null) setClientCount(msg.clients);
            // Respond with pong to keep alive
            if (ws.readyState === WebSocket.OPEN) {
              ws.send("pong");
            }
            break;
        }
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = () => {
      setConnectionStatus("disconnected");
      wsRef.current = null;
      if (heartbeatTimer.current) clearTimeout(heartbeatTimer.current);
      scheduleReconnect();
    };

    ws.onerror = () => {
      setConnectionStatus("error");
      ws.close();
    };
  }, [
    setConnectionStatus,
    applySnapshot,
    applyStateUpdate,
    applyMetricsUpdate,
    applyMarketStatus,
    applyOHLCUpdate,
    applyLogLines,
    applyStopEvents,
    applyAgentsUpdate,
    applyComparisons,
    applyPerformanceUpdate,
    addToast,
    setClientCount,
    resetHeartbeatTimer,
  ]);

  const scheduleReconnect = useCallback(() => {
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    reconnectTimer.current = setTimeout(() => {
      connect();
      // Exponential backoff
      reconnectDelay.current = Math.min(
        reconnectDelay.current * 2,
        MAX_RECONNECT_DELAY
      );
    }, reconnectDelay.current);
  }, [connect]);

  const refresh = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send("refresh");
    }
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (heartbeatTimer.current) clearTimeout(heartbeatTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { refresh };
}
