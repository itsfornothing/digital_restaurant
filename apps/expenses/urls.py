"""
expenses/urls.py

URL patterns for the Expenses API.

  GET/POST  /api/v1/branches/{branch_pk}/expenses/         — list + create
  GET       /api/v1/branches/{branch_pk}/expenses/report/  — category/period report
  POST      /api/v1/branches/{branch_pk}/expenses/export/  — trigger async export
  GET/PATCH/DELETE  /api/v1/expenses/{pk}/                 — retrieve, partial_update, destroy

Requirements: 12.1, 12.2, 12.3, 12.4
"""

from django.urls import path

from apps.expenses.views import ExpenseViewSet

# ---------------------------------------------------------------------------
# View bindings
# ---------------------------------------------------------------------------
expense_list = ExpenseViewSet.as_view({"get": "list", "post": "create"})
expense_detail = ExpenseViewSet.as_view(
    {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
)
expense_report = ExpenseViewSet.as_view({"get": "report"})
expense_export = ExpenseViewSet.as_view({"post": "export"})
expense_export_csv = ExpenseViewSet.as_view({"get": "export_csv"})

urlpatterns = [
    # Branch-scoped list + create
    path(
        "branches/<uuid:branch_pk>/expenses/",
        expense_list,
        name="branch-expense-list",
    ),
    # Branch-scoped CSV export
    path(
        "branches/<uuid:branch_pk>/expenses/export-csv/",
        expense_export_csv,
        name="branch-expense-export-csv",
    ),
    # Branch-scoped expense report (summary by category / period)
    path(
        "branches/<uuid:branch_pk>/expenses/report/",
        expense_report,
        name="branch-expense-report",
    ),
    # Branch-scoped export trigger
    path(
        "branches/<uuid:branch_pk>/expenses/export/",
        expense_export,
        name="branch-expense-export",
    ),
    # Expense detail — not branch-nested (pk lookup, branch scoped in queryset)
    path(
        "expenses/<uuid:pk>/",
        expense_detail,
        name="expense-detail",
    ),
]
