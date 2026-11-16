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

  // ── READ THE ACTIONS, SUBSCRIBE TO NOTHING ──────────────────────────────
  // This used to be `const { ...13 actions } = useHydraStore()`. Calling the
  // Zustand hook with no selector subscribes to the WHOLE store, and this hook
  // is mounted in `App` — so every store mutation re-rendered the entire routed
  // tree. The backend pushes `state_update` every 1.0s and `log_lines` every
  // 2.0s, which made that roughly 1.5 full-app re-renders per second for as
  // long as the page stayed open. On a phone it presented as "stuck loading"
  // while the network showed every request answering 200 in ~3ms.
  //
  // The subscription was never needed. These are ACTIONS, not state: Zustand
  // defines them once inside `create(...)`, so their identities are stable for
  // the store's lifetime and nothing here renders from them. `getState` reads
  // the current actions at call time without registering a listener.
  //
  // `getState` is itself a stable function reference, so it is safe in the
  // dependency arrays below — which is what lets `connect` stay stable and the
  // mount effect run exactly once instead of re-running (and tearing down a
  // live socket) on every push.
  const store = useHydraStore.getState;

  /** Reset heartbeat timeout — called on every received message. */
  const resetHeartbeatTimer = useCallback(() => {
    if (heartbeatTimer.current) clearTimeout(heartbeatTimer.current);
    heartbeatTimer.current = setTimeout(() => {
      // No message for 60s — connection is zombie
      console.warn("[WS] Heartbeat timeout — reconnecting");
      store().setConnectionStatus("disconnected");
      wsRef.current?.close();
    }, HEARTBEAT_TIMEOUT_MS);
  }, [store]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    store().setConnectionStatus("connecting");

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    // Session cookie rides along automatically (browsers always attach
    // matching cookies to same-origin WebSocket handshakes) — no secret in
    // the URL, unlike the old ?api_key= scheme.
    const url = `${protocol}//${host}/ws/dashboard`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      store().setConnectionStatus("connected");
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
            store().applySnapshot(msg);
            break;
          case "state_update":
            store().applyStateUpdate(msg.data);
            break;
          case "metrics_update":
            store().applyMetricsUpdate(msg.data);
            break;
          case "market_status":
            store().applyMarketStatus(msg.data);
            break;
          case "ohlc_update":
            store().applyOHLCUpdate(msg.data);
            break;
          case "log_lines":
            store().applyLogLines(msg.data);
            break;
          case "stop_events":
            store().applyStopEvents(msg.data);
            // Toast notification for new stops — the WS stream tracks the
            // PRIMARY strategy only. Suppress these toasts while the user is
            // viewing a DIFFERENT strategy on the dashboard, so a primary-C
            // stop doesn't pop while the body shows E (audit AUD-3-F1). The
            // stopEvents themselves are still applied (harmless; the primary
            // view reads them). null/unset selection = primary = show toasts.
            if (!isViewingNonPrimary()) {
              for (const stop of msg.data) {
                if (stop.stop_time) {
                  store().addToast({
                    type: "stop",
                    title: `Stop Triggered`,
                    message: `Entry #${stop.entry_number} ${stop.side} side stopped`,
                  });
                }
              }
            }
            break;
          case "agents_update":
            store().applyAgentsUpdate(msg.data);
            break;
          case "performance_update":
            if (msg.data?.daily_pnls) store().applyPerformanceUpdate(msg.data.daily_pnls);
            break;
          case "comparisons_update":
            store().applyComparisons(msg.data);
            break;
          case "heartbeat":
            if (msg.clients != null) store().setClientCount(msg.clients);
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

    ws.onclose = (event) => {
      wsRef.current = null;
      if (heartbeatTimer.current) clearTimeout(heartbeatTimer.current);
      // 4001 = ws/router.py's explicit "Not authenticated" close (session
      // expired/revoked, checked BEFORE accept()) — not a network blip.
      // Retrying forever here just re-fails identically every ~30s (a real
      // stale tab did this for hours). Stop and let LoginGate re-check auth
      // and send the user back through login instead.
      if (event.code === 4001) {
        store().setConnectionStatus("auth_expired");
        return;
      }
      store().setConnectionStatus("disconnected");
      // eslint-disable-next-line react-hooks/immutability -- a socket event handler, which by definition runs after render — that is what an event handler IS
      scheduleReconnect();
    };

    ws.onerror = () => {
      store().setConnectionStatus("error");
      ws.close();
    };
    // Was a 13-action list. Every one of those came from a whole-store
    // subscription; they are read through `store()` now, so `connect` depends
    // only on two stable references and is itself stable.
  }, [store, resetHeartbeatTimer]);

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
