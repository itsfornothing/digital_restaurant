/**
 * apps/financials/frontend/types.ts
 *
 * TypeScript interfaces for the Financial Dashboard.
 *
 * Requirements: 13.4
 */

// ---------------------------------------------------------------------------
// Period summary returned by the backend for daily / weekly / monthly
// ---------------------------------------------------------------------------
export interface PeriodSummary {
  income: string;
  expenses: string;
  profit: string;
  period_start: string;
  period_end: string;
}

// ---------------------------------------------------------------------------
// Single data point in the 30-day revenue trend
// ---------------------------------------------------------------------------
export interface RevenueTrendPoint {
  date: string;
  income: string;
  expenses: string;
  profit: string;
}

// ---------------------------------------------------------------------------
// Expense category breakdown item
// ---------------------------------------------------------------------------
export interface ExpenseBreakdownItem {
  category: string;
  total: string;
}

// ---------------------------------------------------------------------------
// Top-selling item by revenue
// ---------------------------------------------------------------------------
export interface TopItem {
  menu_item_id: string;
  name: string;
  total_revenue: string;
}

// ---------------------------------------------------------------------------
// Full dashboard data shape returned by GET /api/v1/branches/{id}/financials/
// ---------------------------------------------------------------------------
export interface DashboardData {
  branch_id: string;
  daily: PeriodSummary;
  weekly: PeriodSummary;
  monthly: PeriodSummary;
  revenue_trend: RevenueTrendPoint[];
  expense_breakdown: ExpenseBreakdownItem[];
  top_items_by_revenue: TopItem[];
  order_volume_by_hour: Record<string, number>;
}

// ---------------------------------------------------------------------------
// WebSocket live-update message shapes
// ---------------------------------------------------------------------------
export type LiveUpdateType =
  | "profit_update"
  | "new_income"
  | "new_expense"
  | "report_ready";

export interface LiveUpdateMessage {
  type: LiveUpdateType;
  payload: Partial<DashboardData>;
}

// ---------------------------------------------------------------------------
// KPI period toggle value
// ---------------------------------------------------------------------------
export type Period = "daily" | "weekly" | "monthly";

// ---------------------------------------------------------------------------
// Recharts-compatible data shapes
// ---------------------------------------------------------------------------
export interface TrendChartPoint {
  date: string;
  income: number;
  expenses: number;
  profit: number;
}

export interface PieChartSegment {
  name: string;
  value: number;
}

export interface BarChartItem {
  name: string;
  revenue: number;
}

export interface AreaChartPoint {
  hour: string;
  orders: number;
}
