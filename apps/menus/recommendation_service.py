"""
menus/recommendation_service.py

Recommendation engine for customer-facing smart menu recommendations.

Capabilities:
1. Popular / Trending Items (Branch-scoped):
   - Analyzes historical OrderItem volume across non-cancelled orders for the branch.
   - Ranks items by order volume and recent popularity.
   - Filters out archived, unavailable, or non-dietary-compliant items.
   - Falls back gracefully to available branch items if order history is sparse.
   - In-memory/Redis cached for responsive menu loading.

2. Smart Pairings & Cross-Selling ("Frequently Ordered Together"):
   - Co-occurrence association mining on order line items in the branch.
   - Given a list of items (e.g. items in customer cart), identifies complementary
     dishes, sides, and beverages commonly ordered in the same ticket.
   - Excludes items already in the seed list.
   - Respects dietary restrictions (e.g., vegan cart yields only vegan pairings).
   - Falls back to popular items from complementary categories or branch best-sellers.

3. "For You" Personalized Recommendations:
   - Evaluates active customer session (recent table orders) combined with branch
     trending items and dietary preferences.

Requirements: SRS Section 2.1, Section 7, FR-P2.1, FR-C1.4
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Sequence
from uuid import UUID

from django.core.cache import cache
from django.db.models import Count, Q, Sum

from apps.menus.models import MenuItem
from apps.orders.models import Order, OrderItem

logger = logging.getLogger(__name__)


class RecommendationService:
    """
    High-performance menu recommendation engine.
    Scoped by branch and isolated per tenant.
    """

    CACHE_TTL = 300  # 5 minutes

    @staticmethod
    def _cache_key(prefix: str, branch_id: str | UUID, key_extra: str = "") -> str:
        """Generate deterministic cache key for recommendation responses."""
        raw = f"{prefix}:{branch_id}:{key_extra}"
        return f"rec:{hashlib.md5(raw.encode('utf-8')).hexdigest()}"

    @classmethod
    def filter_by_dietary_tags(
        cls,
        queryset,
        dietary_tags: Sequence[str] | None = None,
    ):
        """
        Filter a MenuItem queryset or iterable to only items possessing ALL dietary_tags.
        Compatible with both PostgreSQL JSON containment and SQLite test materialization.
        """
        if not dietary_tags:
            return queryset

        clean_tags = [str(t).strip().lower() for t in dietary_tags if str(t).strip()]
        if not clean_tags:
            return queryset

        from django.db import connection as db_connection
        vendor = getattr(db_connection, "vendor", "")

        if hasattr(queryset, "filter") and vendor == "postgresql":
            for tag in clean_tags:
                queryset = queryset.filter(dietary_tags__contains=[tag])
            return queryset

        # In-memory / SQLite fallback
        items = list(queryset) if hasattr(queryset, "__iter__") else queryset
        filtered = []
        for item in items:
            item_tags = getattr(item, "dietary_tags", []) or []
            if isinstance(item_tags, list) and all(t in item_tags for t in clean_tags):
                filtered.append(item)
        return filtered

    @classmethod
    def get_popular_items(
        cls,
        branch_id: str | UUID,
        limit: int = 6,
        dietary_tags: Sequence[str] | None = None,
        use_cache: bool = True,
    ) -> list[MenuItem]:
        """
        Return the top-ordered available menu items for a branch.
        Ranked by total quantity ordered in confirmed/active/served orders.
        """
        clean_tags = [str(t).strip().lower() for t in (dietary_tags or []) if str(t).strip()]
        cache_key = cls._cache_key("popular", branch_id, ",".join(sorted(clean_tags)) + f":{limit}")

        if use_cache:
            cached_ids = cache.get(cache_key)
            if cached_ids is not None:
                items_by_id = {
                    str(item.id): item
                    for item in MenuItem.objects.filter(id__in=cached_ids)
                    .select_related("nutrition")
                    .prefetch_related("categories")
                }
                # Preserve rank order
                ranked = [items_by_id[cid] for cid in cached_ids if cid in items_by_id]
                if len(ranked) == len(cached_ids):
                    return ranked

        # Base available items query
        base_qs = MenuItem.objects.filter(
            branch_id=branch_id,
            status="available",
            is_archived=False,
        ).select_related("nutrition").prefetch_related("categories")

        # Query OrderItem aggregation for this branch
        popular_item_stats = (
            OrderItem.objects.filter(
                order__branch_id=branch_id,
                order__status__in=["confirmed", "received", "preparing", "ready", "served"],
                menu_item__branch_id=branch_id,
                menu_item__status="available",
                menu_item__is_archived=False,
            )
            .values("menu_item_id")
            .annotate(total_ordered=Sum("quantity"), order_count=Count("id"))
            .order_by("-total_ordered", "-order_count")
        )

        popular_ids = [stat["menu_item_id"] for stat in popular_item_stats]

        # Fetch menu items and order them according to popular_ids
        if popular_ids:
            items_dict = {
                item.id: item
                for item in base_qs.filter(id__in=popular_ids)
            }
            ordered_items = [items_dict[pid] for pid in popular_ids if pid in items_dict]
        else:
            ordered_items = []

        # If not enough items from order history, backfill with branch available items
        if len(ordered_items) < limit:
            existing_ids = {item.id for item in ordered_items}
            backfill = list(
                base_qs.exclude(id__in=existing_ids).order_by("created_at")[: (limit - len(ordered_items)) * 2]
            )
            ordered_items.extend(backfill)

        # Apply dietary constraints
        filtered_items = cls.filter_by_dietary_tags(ordered_items, clean_tags)
        result = list(filtered_items)[:limit]

        # Cache item IDs
        if use_cache and result:
            cache.set(cache_key, [str(item.id) for item in result], timeout=cls.CACHE_TTL)

        return result

    @classmethod
    def get_pairings_for_items(
        cls,
        branch_id: str | UUID,
        item_ids: Sequence[str | UUID],
        limit: int = 4,
        dietary_tags: Sequence[str] | None = None,
    ) -> list[MenuItem]:
        """
        Smart pairings / Market Basket Analysis.
        Finds menu items frequently co-ordered with any of `item_ids` in the same order.
        Excludes items that are already in `item_ids`.
        """
        clean_item_ids = [str(x) for x in item_ids if str(x).strip()]
        if not clean_item_ids:
            return cls.get_popular_items(branch_id, limit=limit, dietary_tags=dietary_tags)

        clean_tags = [str(t).strip().lower() for t in (dietary_tags or []) if str(t).strip()]

        # 1. Find orders in this branch containing at least one of the seed items
        orders_with_seed = Order.objects.filter(
            branch_id=branch_id,
            status__in=["confirmed", "received", "preparing", "ready", "served"],
            items__menu_item_id__in=clean_item_ids,
        ).values_list("id", flat=True)

        # 2. Find companion items ordered within those same orders
        co_ordered_stats = (
            OrderItem.objects.filter(
                order_id__in=orders_with_seed,
                menu_item__branch_id=branch_id,
                menu_item__status="available",
                menu_item__is_archived=False,
            )
            .exclude(menu_item_id__in=clean_item_ids)
            .values("menu_item_id")
            .annotate(co_count=Count("order_id", distinct=True), total_qty=Sum("quantity"))
            .order_by("-co_count", "-total_qty")
        )

        pairing_ids = [stat["menu_item_id"] for stat in co_ordered_stats]

        # 3. Retrieve MenuItem objects preserving rank
        base_qs = MenuItem.objects.filter(
            branch_id=branch_id,
            status="available",
            is_archived=False,
        ).select_related("nutrition").prefetch_related("categories")

        items_dict = {
            item.id: item
            for item in base_qs.filter(id__in=pairing_ids)
        }
        paired_items = [items_dict[pid] for pid in pairing_ids if pid in items_dict]

        # 4. If co-occurrence is low or sparse, complement with branch popular items
        if len(paired_items) < limit:
            excluded = set(clean_item_ids) | {str(p.id) for p in paired_items}
            popular_fallback = cls.get_popular_items(
                branch_id=branch_id,
                limit=limit * 2,
                dietary_tags=clean_tags,
                use_cache=True,
            )
            for pop in popular_fallback:
                if str(pop.id) not in excluded:
                    paired_items.append(pop)
                    excluded.add(str(pop.id))
                    if len(paired_items) >= limit:
                        break

        # 5. Apply dietary filter
        filtered = cls.filter_by_dietary_tags(paired_items, clean_tags)
        return list(filtered)[:limit]

    @classmethod
    def get_personalized_recommendations(
        cls,
        branch_id: str | UUID,
        customer_session: dict[str, Any] | None = None,
        limit: int = 6,
        dietary_tags: Sequence[str] | None = None,
    ) -> list[MenuItem]:
        """
        Personalized recommendations:
        - If customer has recent order items in their session, generate smart pairings
          combined with branch trending items.
        - Otherwise returns branch popular dishes filtered by dietary tags.
        """
        recent_item_ids: list[str] = []
        if customer_session:
            # Check last order or cart items stored in session
            last_order_id = customer_session.get("last_order_id")
            if last_order_id:
                recent_item_ids = list(
                    OrderItem.objects.filter(order_id=last_order_id).values_list("menu_item_id", flat=True)
                )

        if recent_item_ids:
            pairings = cls.get_pairings_for_items(
                branch_id=branch_id,
                item_ids=recent_item_ids,
                limit=limit,
                dietary_tags=dietary_tags,
            )
            if len(pairings) >= limit:
                return pairings[:limit]

            # Backfill with popular
            popular = cls.get_popular_items(
                branch_id=branch_id,
                limit=limit,
                dietary_tags=dietary_tags,
            )
            seen = {str(item.id) for item in pairings}
            for p in popular:
                if str(p.id) not in seen:
                    pairings.append(p)
                    seen.add(str(p.id))
                    if len(pairings) >= limit:
                        break
            return pairings[:limit]

        return cls.get_popular_items(
            branch_id=branch_id,
            limit=limit,
            dietary_tags=dietary_tags,
        )
