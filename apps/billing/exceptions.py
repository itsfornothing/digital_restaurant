"""
billing/exceptions.py

Domain exception for subscription resource-limit enforcement.

``ResourceLimitExceeded`` is a plain Python exception (not a DRF APIException)
so that ``BillingService`` can be imported and used in non-HTTP contexts (e.g.
management commands, Celery tasks) without pulling in the REST framework.

The view/serializer layer catches this exception and re-raises it as the DRF
``shared.exceptions.ResourceLimitExceeded`` (HTTP 402) so the API client sees
the standard error envelope.

Requirements: 2.3
"""


class ResourceLimitExceeded(Exception):
    """
    Raised by ``BillingService.check_resource_limit`` when a tenant has reached
    or exceeded the plan's cap for a given resource type.

    Attributes:
        resource_type  (str)  — e.g. 'branches', 'menu_items', 'staff_accounts'
        current_count  (int)  — how many the tenant currently has
        limit          (int)  — the plan's maximum; -1 indicates "no subscription"
    """

    def __init__(self, resource_type: str, current_count: int, limit: int) -> None:
        self.resource_type = resource_type
        self.current_count = current_count
        self.limit = limit
        super().__init__(
            f"Resource limit exceeded for {resource_type}: {current_count}/{limit}"
        )
