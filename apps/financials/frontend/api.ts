/**
 * apps/financials/frontend/api.ts
 *
 * API fetching helpers for the Financial Dashboard.
 *
 * Requirements: 13.4
 */

import type { DashboardData } from "./types";

const API_BASE = "/api/v1";

/**
 * Fetch financial dashboard data for a specific branch.
 *
 * Optionally pass a date range as query params.
 * Falls back gracefully — returns null on network failure.
 */
export async function fetchDashboardData(
  branchId: string,
  params?: { date_from?: string; date_to?: string }
): Promise<DashboardData | null> {
  const url = new URL(`${API_BASE}/branches/${branchId}/financials/`, window.location.origin);

  if (params?.date_from) {
    url.searchParams.set("date_from", params.date_from);
  }
  if (params?.date_to) {
    url.searchParams.set("date_to", params.date_to);
  }

  try {
    const response = await fetch(url.toString(), {
      method: "GET",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
    });

    if (!response.ok) {
      console.error(
        `fetchDashboardData: HTTP ${response.status} for branch ${branchId}`
      );
      return null;
    }

    return (await response.json()) as DashboardData;
  } catch (error) {
    console.error("fetchDashboardData: network error", error);
    return null;
  }
}

/**
 * Trigger an async financial report export.
 *
 * Returns the task_id for status polling, or null on failure.
 */
export async function triggerReportExport(
  branchId: string,
  format: "pdf" | "csv",
  period: string,
  reportType: string
): Promise<{ task_id: string; status: string } | null> {
  try {
    const response = await fetch(
      `${API_BASE}/branches/${branchId}/reports/`,
      {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ format, period, report_type: reportType }),
      }
    );

    if (!response.ok) {
      console.error(
        `triggerReportExport: HTTP ${response.status} for branch ${branchId}`
      );
      return null;
    }

    return await response.json();
  } catch (error) {
    console.error("triggerReportExport: network error", error);
    return null;
  }
}

/**
 * Get a CSRF token from the cookie jar (required for Django SessionAuth).
 */
export function getCsrfToken(): string {
  const name = "csrftoken";
  const cookies = document.cookie.split(";");
  for (const cookie of cookies) {
    const [key, value] = cookie.trim().split("=");
    if (key === name) {
      return decodeURIComponent(value);
    }
  }
  return "";
}
