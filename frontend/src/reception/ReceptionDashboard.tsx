/**
 * ReceptionDashboard.tsx — Reception order management dashboard.
 *
 * Connects to the branch reception WebSocket channel group and displays
 * incoming orders in a sortable table. New (confirmed) orders are
 * highlighted in yellow for immediate staff attention.
 *
 * WebSocket: ws://<host>/ws/branch/<branchId>/reception/
 * Channel group: branch_{branchId}_reception
 *
 * Requirements: 17.1
 */

import React, {
  useState,
  useEffect,
  useRef,
  useCallback,
  useReducer,
} from "react";

// ---------------------------------------------------------------------------
// TypeScript interfaces
// ---------------------------------------------------------------------------

interface OrderItem {
  id: string;
  menu_item: string;
  menu_item_name: string;
  quantity: number;
  unit_price: string;
  special_instructions: string;
}

interface Order {
  id: string;
  order_number: string;
  table_number: string;
  status:
    | "confirmed"
    | "received"
    | "preparing"
    | "ready"
    | "served"
    | "cancelled";
  placed_at: string; // ISO 8601
  total_amount: string;
  customer_name: string;
  customer_phone: string;
  items: OrderItem[];
}

interface WsMessage {
  type: "order.new" | "order.status_changed" | "order.cancelled";
  payload: Order;
}

// ---------------------------------------------------------------------------
// Orders reducer — newest orders first for reception view
// ---------------------------------------------------------------------------

type OrdersAction =
  | { type: "ADD"; order: Order }
  | { type: "UPDATE"; order: Order }
  | { type: "REMOVE"; orderId: string }
  | { type: "SET_ALL"; orders: Order[] };

function ordersReducer(state: Order[], action: OrdersAction): Order[] {
  switch (action.type) {
    case "ADD":
      if (state.find((o) => o.id === action.order.id)) return state;
      // Newest first
      return [action.order, ...state];
    case "UPDATE":
      return state.map((o) => (o.id === action.order.id ? action.order : o));
    case "REMOVE":
      return state.filter((o) => o.id !== action.orderId);
    case "SET_ALL":
      // Sort newest first
      return [...action.orders].sort(
        (a, b) =>
          new Date(b.placed_at).getTime() - new Date(a.placed_at).getTime()
      );
    default:
      return state;
  }
}

// ---------------------------------------------------------------------------
// WebSocket reconnect hook with exponential back-off
// ---------------------------------------------------------------------------

const MIN_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30000;

function useReconnectingWs(
  url: string,
  onMessage: (msg: WsMessage) => void
): { connected: boolean } {
  const wsRef = useRef<WebSocket | null>(null);
  const backoffRef = useRef<number>(MIN_BACKOFF_MS);
  const unmountedRef = useRef(false);
  const [connected, setConnected] = useState(false);

  const connect = useCallback(() => {
    if (unmountedRef.current) return;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      try {
        const msg: WsMessage = JSON.parse(event.data);
        onMessage(msg);
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onopen = () => {
      backoffRef.current = MIN_BACKOFF_MS;
      setConnected(true);
    };

    ws.onclose = () => {
      setConnected(false);
      if (unmountedRef.current) return;
      const delay = backoffRef.current;
      backoffRef.current = Math.min(delay * 2, MAX_BACKOFF_MS);
      setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [url, onMessage]);

  useEffect(() => {
    unmountedRef.current = false;
    connect();
    return () => {
      unmountedRef.current = true;
      wsRef.current?.close();
    };
  }, [connect]);

  return { connected };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

declare const RECEPTION_I18N: Record<string, string> | undefined;
const _ = (key: string): string => {
  const i18n = (typeof RECEPTION_I18N !== "undefined" ? RECEPTION_I18N : {}) as Record<string, string>;
  return i18n[key] ?? key;
};

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatItems(items: OrderItem[]): React.ReactNode {
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {items.map((item) => (
        <li key={item.id} style={{ marginBottom: 2 }}>
          <strong>{item.quantity}×</strong> {item.menu_item_name}
          {item.special_instructions && (
            <span
              style={{ color: "#888", fontStyle: "italic", marginLeft: 4 }}
            >
              ({item.special_instructions})
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

function collectSpecialInstructions(items: OrderItem[]): string {
  return items
    .filter((i) => i.special_instructions)
    .map((i) => `${i.menu_item_name}: ${i.special_instructions}`)
    .join("; ");
}

const STATUS_LABELS: Record<string, string> = {
  confirmed: _("statusConfirmed"),
  received: _("statusReceived"),
  preparing: _("statusPreparing"),
  ready: _("statusReady"),
  served: _("statusServed"),
  cancelled: _("statusCancelled"),
};

function statusBadge(s: string): React.ReactNode {
  const palette: Record<string, { bg: string; color: string }> = {
    confirmed: { bg: "#f0ece9", color: "#857370" },
    received: { bg: "#edf2ee", color: "#5D7061" },
    preparing: { bg: "#f6f0e8", color: "#D4A373" },
    ready: { bg: "#f0edeb", color: "#77574e" },
    served: { bg: "#f0eeed", color: "#817471" },
    cancelled: { bg: "#f5edec", color: "#6E2E21" },
  };
  const style = palette[s] ?? { bg: "#f0ece9", color: "#857370" };
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 10px",
        borderRadius: 12,
        background: style.bg,
        color: style.color,
        fontSize: 12,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: 0.5,
      }}
    >
      {STATUS_LABELS[s] ?? s}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main ReceptionDashboard component
// ---------------------------------------------------------------------------

interface ReceptionDashboardProps {
  /** Branch UUID — used to build the WebSocket URL */
  branchId: string;
  /** Django session key for WebSocket auth (bypasses SameSite cookie issues) */
  sessionKey?: string;
}

const ReceptionDashboard: React.FC<ReceptionDashboardProps> = ({ branchId, sessionKey = "" }) => {
  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsHost = typeof window !== "undefined" ? window.location.host : "localhost";
  let wsUrl = `${wsProtocol}//${wsHost}/ws/reception/`;
  if (sessionKey) wsUrl += `?sessionid=${sessionKey}`;

  const [orders, dispatch] = useReducer(ordersReducer, []);

  // Handle WebSocket messages
  const handleMessage = useCallback((msg: WsMessage) => {
    switch (msg.type) {
      case "order.new":
        dispatch({ type: "ADD", order: msg.payload });
        break;
      case "order.status_changed":
        dispatch({ type: "UPDATE", order: msg.payload });
        break;
      case "order.cancelled":
        // Mark cancelled in state (keep row visible with cancelled status)
        dispatch({ type: "UPDATE", order: msg.payload });
        break;
    }
  }, []);

  const { connected } = useReconnectingWs(wsUrl, handleMessage);

  // Fetch existing orders on mount via REST API
  useEffect(() => {
    const apiBase = typeof window !== "undefined" ? window.location.origin : "";
    const now = new Date();
    const today = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0") + "-" + String(now.getDate()).padStart(2, "0");
    fetch(`${apiBase}/api/v1/branches/${branchId}/orders/?placed_date=${today}`, {
      credentials: "include",
      headers: { "Accept": "application/json" },
    })
      .then((r) => (r.ok ? r.json() : Promise.resolve([])))
      .then((data) => {
        const orders: Order[] = Array.isArray(data) ? data : (data.results ?? []);
        if (orders.length > 0) {
          dispatch({ type: "SET_ALL", orders });
        }
      })
      .catch(() => {/* silently ignore */});
  }, [branchId]);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div
      className="reception-wrapper"
      style={{
        fontFamily: "system-ui, sans-serif",
        background: "#fff8f6",
        minHeight: "100vh",
        padding: "24px",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 20,
        }}
      >
        <h1 style={{ margin: 0, fontSize: 22 }}>{_("receptionDashboard")}</h1>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              display: "inline-block",
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: connected ? "#2e7d32" : "#b71c1c",
            }}
            title={connected ? _("connected") : _("reconnecting")}
          />
          <span style={{ fontSize: 12, color: "#555" }}>
            {connected ? _("live") : _("reconnecting")}
          </span>
          <span style={{ color: "#aaa", margin: "0 8px" }}>|</span>
          <span style={{ fontSize: 14, color: "#555" }}>
            {orders.length} {orders.length === 1 ? _("order") : _("orders")}
          </span>
        </div>
      </div>

      {/* Orders table */}
      {orders.length === 0 ? (
        <div
          style={{
            textAlign: "center",
            marginTop: 80,
            color: "#aaa",
            fontSize: 18,
          }}
        >
          {_("noOrders")}
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              background: "#fff",
              borderRadius: 8,
              boxShadow: "0 1px 4px rgba(0,0,0,0.08)",
              overflow: "hidden",
            }}
            aria-label={_("incomingOrders")}
          >
            <thead>
              <tr style={{ background: "#fff0ee" }}>
                <th style={thStyle}>{_("orderHash")}</th>
                <th style={thStyle}>{_("table")}</th>
                <th style={thStyle}>{_("items")}</th>
                <th style={thStyle}>{_("specialInstructions")}</th>
                <th style={thStyle}>{_("status")}</th>
                <th style={thStyle}>{_("time")}</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => {
                const isNew = order.status === "confirmed";
                const isCancelled = order.status === "cancelled";
                const rowStyle: React.CSSProperties = {
                  borderBottom: "1px solid #eee",
                  background: isNew
                    ? "#f6f0e8" // soft warm for new/confirmed orders
                    : isCancelled
                    ? "#f5edec" // soft muted for cancelled
                    : "#fff",
                  opacity: isCancelled ? 0.7 : 1,
                };

                return (
                  <tr key={order.id} style={rowStyle}>
                    <td data-label={_("orderHash")} style={tdStyle}>
                      <strong>{order.order_number}</strong>
                    </td>
                    <td data-label={_("table")} style={{ ...tdStyle, textAlign: "center" }}>
                      {order.table_number}
                    </td>
                    <td data-label={_("items")} style={tdStyle}>{formatItems(order.items)}</td>
                    <td data-label={_("specialInstructions")} style={{ ...tdStyle, maxWidth: 220, color: "#666", fontSize: 13 }}>
                      {collectSpecialInstructions(order.items) || (
                        <span style={{ color: "#ccc" }}>—</span>
                      )}
                    </td>
                    <td data-label={_("status")} style={{ ...tdStyle, textAlign: "center" }}>
                      {statusBadge(order.status)}
                    </td>
                    <td
                      data-label={_("time")}
                      style={{
                        ...tdStyle,
                        textAlign: "center",
                        whiteSpace: "nowrap",
                        color: "#555",
                        fontSize: 13,
                      }}
                    >
                      {formatTime(order.placed_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default ReceptionDashboard;

// ---------------------------------------------------------------------------
// Table styles
// ---------------------------------------------------------------------------

const thStyle: React.CSSProperties = {
  padding: "10px 14px",
  textAlign: "left",
  fontWeight: 700,
  fontSize: 13,
  color: "#333",
  borderBottom: "2px solid #c5cae9",
  whiteSpace: "nowrap",
};

const tdStyle: React.CSSProperties = {
  padding: "10px 14px",
  verticalAlign: "top",
  fontSize: 14,
};
