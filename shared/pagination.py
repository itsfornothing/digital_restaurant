"""
Cursor-based pagination for the platform's REST API.

Cursor pagination is used instead of offset-based pagination to support
large datasets (audit logs, orders) efficiently without the performance
degradation of OFFSET queries on tables with millions of rows.

The cursor encodes the last-seen record's ID and timestamp, allowing
subsequent pages to be fetched with a WHERE clause rather than OFFSET.

Full implementation is in Task 5.
This stub establishes the class interface used across all ViewSets.
"""

from rest_framework.pagination import CursorPagination as _BaseCursorPagination


class CursorPagination(_BaseCursorPagination):
    """
    Default cursor paginator used across all API ViewSets.

    Configuration:
        page_size       — 50 records per page (matches REST_FRAMEWORK['PAGE_SIZE'])
        ordering        — '-created_at' descending by default; overridden per ViewSet
        cursor_query_param — 'cursor' query parameter in the URL

    Usage in a ViewSet:
        pagination_class = CursorPagination
    """

    page_size = 50
    ordering = "-id"  # safe default — all models have a PK; override per ViewSet as needed
    cursor_query_param = "cursor"
    page_size_query_param = "page_size"
    max_page_size = 200
