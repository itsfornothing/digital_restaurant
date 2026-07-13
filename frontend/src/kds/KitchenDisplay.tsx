/**
 * KitchenDisplay.tsx — Kitchen Display System (KDS) React component.
 *
 * Connects to the branch kitchen WebSocket channel group and shows live
 * orders with elapsed timers, status update buttons (optimistic UI), and
 * a recipe viewer modal.
 *
 * WebSocket: ws://<host>/ws/branch/<branchId>/kitchen/
 * Channel group: branch_{branchId}_kitchen
 *
 * Requirements: 10.1, 10.2, 10.5, 10.6
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
  menu_item: string; // UUID
  menu_item_name: string;
  quantity: number;
  unit_price: string;
  special_instructions: string;
}

interface Order {
  id: string;
  order_number: string;
  table_number: string;
  status: "confirmed" | "received" | "preparing" | "ready" | "served" | "cancelled";
  placed_at: string; // ISO 8601
  total_amount: string;
  items: OrderItem[];
}

interface Recipe {
  menu_item_id: string;
  menu_item_name: string;
  method: string;
  cook_time_minutes: number;
  ingredients: Array<{ name: string; quantity: string; unit: string }>;
}

interface WsMessage {
  type: "order.new" | "order.cancelled" | "order.status_changed";
  payload: Order;
}

// ---------------------------------------------------------------------------
// State machine: next valid action buttons per current status
// ---------------------------------------------------------------------------

declare const KDS_I18N: Record<string, string> | undefined;
const _ = (key: string): string => {
  const i18n = (typeof KDS_I18N !== "undefined" ? KDS_I18N : {}) as Record<string, string>;
  return i18n[key] ?? key;
};

const NEXT_STATUS: Record<string, string | null> = {
  confirmed: "received",
  received: "preparing",
  preparing: "ready",
  ready: "served",
  served: null,
  cancelled: null,
};

const STATUS_BUTTON_LABEL: Record<string, string> = {
  received: _("markReceived"),
  preparing: _("startPreparing"),
  ready: _("markReady"),
  served: _("markServed"),
};

// ---------------------------------------------------------------------------
// Orders reducer
// ---------------------------------------------------------------------------

type OrdersAction =
  | { type: "ADD"; order: Order }
  | { type: "UPDATE"; order: Order }
  | { type: "REMOVE"; orderId: string }
  | { type: "SET_ALL"; orders: Order[] };

function ordersReducer(state: Order[], action: OrdersAction): Order[] {
  switch (action.type) {
    case "ADD":
      // Avoid duplicates
      if (state.find((o) => o.id === action.order.id)) return state;
      return [...state, action.order].sort(
        (a, b) => new Date(a.placed_at).getTime() - new Date(b.placed_at).getTime()
      );
    case "UPDATE":
      return state
        .map((o) => (o.id === action.order.id ? action.order : o))
        .sort(
          (a, b) => new Date(a.placed_at).getTime() - new Date(b.placed_at).getTime()
        );
    case "REMOVE":
      return state.filter((o) => o.id !== action.orderId);
    case "SET_ALL":
      return [...action.orders].sort(
        (a, b) => new Date(a.placed_at).getTime() - new Date(b.placed_at).getTime()
      );
    default:
      return state;
  }
}

// ---------------------------------------------------------------------------
// Elapsed timer hook
// ---------------------------------------------------------------------------

function useElapsedSeconds(placedAt: string): number {
  const [elapsed, setElapsed] = useState<number>(() =>
    Math.floor((Date.now() - new Date(placedAt).getTime()) / 1000)
  );

  useEffect(() => {
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - new Date(placedAt).getTime()) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [placedAt]);

  return elapsed;
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// WebSocket reconnect hook with exponential back-off
// ---------------------------------------------------------------------------

const MIN_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30000;

function useReconnectingWs(
  url: string,
  onMessage: (msg: WsMessage) => void
): WebSocket | null {
  const wsRef = useRef<WebSocket | null>(null);
  const backoffRef = useRef<number>(MIN_BACKOFF_MS);
  const unmountedRef = useRef(false);

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
      backoffRef.current = MIN_BACKOFF_MS; // reset back-off on successful connect
    };

    ws.onclose = () => {
      if (unmountedRef.current) return;
      const delay = backoffRef.current;
      backoffRef.current = Math.min(delay * 2, MAX_BACKOFF_MS);
      setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close(); // triggers onclose and the reconnect logic
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

  return wsRef.current;
}

// ---------------------------------------------------------------------------
// ElapsedTimer — isolated component so only the timer text re-renders
// ---------------------------------------------------------------------------

const ElapsedTimer: React.FC<{ placedAt: string }> = ({ placedAt }) => {
  const elapsed = useElapsedSeconds(placedAt);
  const style: React.CSSProperties = {
    fontWeight: "bold",
    fontVariantNumeric: "tabular-nums",  // prevents width change as digits change
    minWidth: 48,
    display: "inline-block",
    color: elapsed < 600 ? "#5D7061" : elapsed < 1200 ? "#D4A373" : "#8B3A2A",
  };
  return <span style={style}>{formatElapsed(elapsed)}</span>;
};

// ---------------------------------------------------------------------------
// OrderCard component
// ---------------------------------------------------------------------------

interface OrderCardProps {
  order: Order;
  onStatusUpdate: (orderId: string, newStatus: string, prevStatus: string) => void;
  onViewRecipe: (menuItemId: string, menuItemName: string) => void;
}

const OrderCard: React.FC<OrderCardProps> = ({
  order,
  onStatusUpdate,
  onViewRecipe,
}) => {
  const nextStatus = NEXT_STATUS[order.status];

  return (
    <div
      style={{
        border: "1px solid #ccc",
        borderRadius: 8,
        padding: "16px",
        background: "#fff",
        boxShadow: "0 1px 4px rgba(0,0,0,0.08)",
        // Fixed layout so timer ticks don't cause reflow
        contain: "layout",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, alignItems: "center" }}>
        <strong style={{ fontSize: 16 }}>{order.order_number}</strong>
        <ElapsedTimer placedAt={order.placed_at} />
      </div>

      <div style={{ marginBottom: 6, color: "#555" }}>
        {_("table")}: <strong>{order.table_number}</strong>
      </div>

      {/* Status badge */}
      <div style={{ marginBottom: 10 }}>
        <span
          style={{
            display: "inline-block",
            padding: "2px 10px",
            borderRadius: 12,
            background: statusBgColor(order.status),
            color: "#fff",
            fontSize: 12,
            textTransform: "uppercase",
            letterSpacing: 1,
          }}
        >
          {statusLabel(order.status)}
        </span>
      </div>

      {/* Items list */}
      <ul style={{ margin: "0 0 12px", padding: "0 0 0 16px" }}>
        {order.items.map((item) => (
          <li key={item.id} style={{ marginBottom: 4 }}>
            <button
              onClick={() => onViewRecipe(item.menu_item, item.menu_item_name)}
              style={{
                background: "none",
                border: "none",
                color: "#8B3A2A",
                cursor: "pointer",
                padding: 0,
                fontWeight: "bold",
                textDecoration: "underline",
              }}
              title={_("viewRecipe")}
            >
              {item.menu_item_name}
            </button>
            {" "}× {item.quantity}
            {item.special_instructions && (
              <span style={{ display: "block", fontSize: 12, color: "#888", fontStyle: "italic" }}>
                {item.special_instructions}
              </span>
            )}
          </li>
        ))}
      </ul>

      {/* Status update button */}
      {nextStatus && (
        <button
          onClick={() => onStatusUpdate(order.id, nextStatus, order.status)}
          style={{
            width: "100%",
            padding: "8px 0",
            background: "#8B3A2A",
            color: "#fff",
            border: "none",
            borderRadius: 6,
            cursor: "pointer",
            fontWeight: "bold",
            fontSize: 14,
          }}
        >
          {STATUS_BUTTON_LABEL[nextStatus] ?? `→ ${nextStatus}`}
        </button>
      )}
    </div>
  );
};

function statusBgColor(s: string): string {
  const palette: Record<string, string> = {
    confirmed: "#857370",
    received: "#5D7061",
    preparing: "#D4A373",
    ready: "#77574e",
    served: "#817471",
    cancelled: "#6E2E21",
  };
  return palette[s] ?? "#857370";
}

function statusLabel(s: string): string {
  const labels: Record<string, string> = {
    confirmed: _("statusConfirmed"),
    received: _("statusReceived"),
    preparing: _("statusPreparing"),
    ready: _("statusReady"),
    served: _("statusServed"),
    cancelled: _("statusCancelled"),
  };
  return labels[s] ?? s;
}

// ---------------------------------------------------------------------------
// RecipeModal component
// ---------------------------------------------------------------------------

interface RecipeModalProps {
  recipe: Recipe | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}

const RecipeModal: React.FC<RecipeModalProps> = ({ recipe, loading, error, onClose }) => {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "#fff",
          borderRadius: 10,
          padding: 28,
          minWidth: 340,
          maxWidth: 540,
          maxHeight: "80vh",
          overflowY: "auto",
          position: "relative",
        }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={_("recipeViewer")}
      >
        <button
          onClick={onClose}
          aria-label={_("closeRecipeModal")}
          style={{
            position: "absolute",
            top: 12,
            right: 14,
            background: "none",
            border: "none",
            fontSize: 22,
            cursor: "pointer",
            color: "#555",
          }}
        >
          ×
        </button>

        {loading && <p>{_("loadingRecipe")}</p>}
        {error && <p style={{ color: "#c0392b" }}>{error}</p>}

        {recipe && (
          <>
            <h2 style={{ marginTop: 0, marginBottom: 4 }}>{recipe.menu_item_name}</h2>
            <p style={{ color: "#777", marginBottom: 16 }}>
              {_("cookTime")} {recipe.cook_time_minutes} {_("min")}
            </p>

            <h3 style={{ marginBottom: 8 }}>{_("ingredients")}</h3>
            {recipe.ingredients.length === 0 ? (
              <p>{_("noIngredients")}</p>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 16 }}>
                <thead>
                  <tr>
                    <th style={thStyle}>{_("ingredient")}</th>
                    <th style={thStyle}>{_("qty")}</th>
                    <th style={thStyle}>{_("unit")}</th>
                  </tr>
                </thead>
                <tbody>
                  {recipe.ingredients.map((ing, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #eee" }}>
                      <td style={tdStyle}>{ing.name}</td>
                      <td style={tdStyle}>{ing.quantity}</td>
                      <td style={tdStyle}>{ing.unit}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <h3 style={{ marginBottom: 8 }}>{_("method")}</h3>
            <p style={{ whiteSpace: "pre-line", lineHeight: 1.6 }}>{recipe.method}</p>
          </>
        )}
      </div>
    </div>
  );
};

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
  borderBottom: "2px solid #ddd",
  background: "#fff0ee",
};

const tdStyle: React.CSSProperties = {
  padding: "5px 8px",
};

// ---------------------------------------------------------------------------
// Main KitchenDisplay component
// ---------------------------------------------------------------------------

interface KitchenDisplayProps {
  /** Branch UUID used to construct the WebSocket URL and filter orders */
  branchId: string;
  /** Base URL for API calls (defaults to window.location.origin) */
  apiBase?: string;
  /** Django session key for WebSocket auth */
  sessionKey?: string;
}

const KitchenDisplay: React.FC<KitchenDisplayProps> = ({
  branchId,
  apiBase,
  sessionKey = "",
}) => {
  const base = apiBase ?? (typeof window !== "undefined" ? window.location.origin : "");
  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsHost = typeof window !== "undefined" ? window.location.host : "localhost";
  let wsUrl = `${wsProtocol}//${wsHost}/ws/kitchen/`;
  if (sessionKey) wsUrl += `?sessionid=${sessionKey}`;

  const [orders, dispatch] = useReducer(ordersReducer, []);

  // Recipe modal state
  const [recipeModalOpen, setRecipeModalOpen] = useState(false);
  const [recipeLoading, setRecipeLoading] = useState(false);
  const [recipeError, setRecipeError] = useState<string | null>(null);
  const [currentRecipe, setCurrentRecipe] = useState<Recipe | null>(null);

  // Handle incoming WebSocket messages
  const handleMessage = useCallback((msg: WsMessage) => {
    switch (msg.type) {
      case "order.new":
        dispatch({ type: "ADD", order: msg.payload });
        if (typeof window !== "undefined" && (window as any).showOrderNotification) {
          (window as any).showOrderNotification(msg.payload);
        }
        break;
      case "order.status_changed":
        dispatch({ type: "UPDATE", order: msg.payload });
        break;
      case "order.cancelled":
        dispatch({ type: "REMOVE", orderId: msg.payload.id });
        break;
    }
  }, []);

  useReconnectingWs(wsUrl, handleMessage);

  // Fetch existing active orders on mount via REST API
  useEffect(() => {
    if (!base) return;
    fetch(`${base}/api/v1/branches/${branchId}/orders/?status=confirmed,received,preparing,ready&placed_date=today`, {
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
      .catch(() => {/* silently ignore — WS will populate on reconnect */});
  }, [base, branchId]);

  // -- Status update with optimistic UI -----------------------------------

  const handleStatusUpdate = useCallback(
    async (orderId: string, newStatus: string, prevStatus: string) => {
      // Optimistic update: update the order in local state immediately
      const currentOrder = orders.find((o) => o.id === orderId);
      if (!currentOrder) return;

      dispatch({
        type: "UPDATE",
        order: { ...currentOrder, status: newStatus as Order["status"] },
      });

      try {
        const csrfToken =
          typeof document !== "undefined"
            ? document.cookie
                .split("; ")
                .find((r) => r.startsWith("csrftoken="))
                ?.split("=")[1] || ""
            : "";
        const response = await fetch(`${base}/api/v1/orders/${orderId}/status/`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken,
          },
          credentials: "include",
          body: JSON.stringify({ status: newStatus }),
        });

        if (!response.ok) {
          // Revert on error
          dispatch({
            type: "UPDATE",
            order: { ...currentOrder, status: prevStatus as Order["status"] },
          });
          const errBody = await response.json().catch(() => ({}));
          console.error("Status update failed:", errBody);
        }
      } catch (err) {
        // Network error — revert
        dispatch({
          type: "UPDATE",
          order: { ...currentOrder, status: prevStatus as Order["status"] },
        });
        console.error("Network error updating order status:", err);
      }
    },
    [orders, base]
  );

  // -- Recipe modal -------------------------------------------------------

  const handleViewRecipe = useCallback(
    async (menuItemId: string, menuItemName: string) => {
      setRecipeModalOpen(true);
      setRecipeLoading(true);
      setRecipeError(null);
      setCurrentRecipe(null);

      try {
        const response = await fetch(
          `${base}/api/v1/menu-items/${menuItemId}/recipe/`,
          { credentials: "include" }
        );

        if (!response.ok) {
          if (response.status === 404) {
            setRecipeError(`${_("noRecipe")} "${menuItemName}".`);
          } else {
            setRecipeError("Failed to load recipe. Please try again.");
          }
        } else {
          const data: Recipe = await response.json();
          setCurrentRecipe(data);
        }
      } catch {
        setRecipeError("Network error loading recipe.");
      } finally {
        setRecipeLoading(false);
      }
    },
    [base]
  );

  const closeRecipeModal = useCallback(() => {
    setRecipeModalOpen(false);
    setCurrentRecipe(null);
    setRecipeError(null);
  }, []);

  // -- Render -------------------------------------------------------------

  // Show only active (non-served, non-cancelled) orders on KDS
  const activeOrders = orders.filter(
    (o) => o.status !== "served" && o.status !== "cancelled"
  );

  return (
    <div
      style={{
        fontFamily: "system-ui, sans-serif",
        background: "#fff8f6",
        minHeight: "100vh",
        padding: 24,
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 24,
        }}
      >
        <h1 style={{ margin: 0, fontSize: 22 }}>{_("kitchenDisplaySystem")}</h1>
        <span style={{ color: "#555", fontSize: 14 }}>
          {activeOrders.length} {activeOrders.length === 1 ? _("activeOrder") : _("activeOrders")}
        </span>
      </div>

      {/* Orders grid */}
      {activeOrders.length === 0 ? (
        <div
          style={{
            textAlign: "center",
            marginTop: 80,
            color: "#aaa",
            fontSize: 18,
          }}
        >
          {_("noActiveOrders")}
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
            gap: 16,
            alignItems: "start",
          }}
        >
          {activeOrders.map((order) => (
            <OrderCard
              key={order.id}
              order={order}
              onStatusUpdate={handleStatusUpdate}
              onViewRecipe={handleViewRecipe}
            />
          ))}
        </div>
      )}

      {/* Recipe modal */}
      {recipeModalOpen && (
        <RecipeModal
          recipe={currentRecipe}
          loading={recipeLoading}
          error={recipeError}
          onClose={closeRecipeModal}
        />
      )}
    </div>
  );
};

export default KitchenDisplay;
