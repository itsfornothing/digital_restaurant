"""
financials/urls.py

URL patterns for the Financials API.

  GET/POST  /api/v1/branches/{branch_pk}/income/          — list + create income
  GET       /api/v1/branches/{branch_pk}/financials/       — branch financial dashboard
  GET       /api/v1/tenant/financials/                     — consolidated (Tenant_Owner)
  POST      /api/v1/branches/{branch_pk}/reports/          — report export trigger

Requirements: 13.1, 13.2, 12.4, 4.2
"""

from django.urls import path

from apps.financials.views import (
    ConsolidatedFinancialViewSet,
    FinancialDashboardViewSet,
    FinancialReportViewSet,
    IncomeViewSet,
)

# ---------------------------------------------------------------------------
# View bindings
# ---------------------------------------------------------------------------
income_list = IncomeViewSet.as_view({"get": "list", "post": "create"})
income_export = IncomeViewSet.as_view({"get": "export_csv"})
dashboard = FinancialDashboardViewSet.as_view({"get": "list"})
consolidated = ConsolidatedFinancialViewSet.as_view({"get": "list"})
report_export = FinancialReportViewSet.as_view({"post": "create"})

urlpatterns = [
    # Income — branch-scoped list + create
    path(
        "branches/<uuid:branch_pk>/income/",
        income_list,
        name="branch-income-list",
    ),
    # Income CSV export
    path(
        "branches/<uuid:branch_pk>/income/export-csv/",
        income_export,
        name="branch-income-export-csv",
    ),
    # Financial dashboard — branch-level KPIs
    path(
        "branches/<uuid:branch_pk>/financials/",
        dashboard,
        name="branch-financial-dashboard",
    ),
    # Consolidated tenant-wide financials (Tenant_Owner only)
    path(
        "tenant/financials/",
        consolidated,
        name="tenant-financials",
    ),
    # Report export trigger
    path(
        "branches/<uuid:branch_pk>/reports/",
        report_export,
        name="branch-report-export",
    ),
]
