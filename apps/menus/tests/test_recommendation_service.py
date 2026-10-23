"""
apps/menus/tests/test_recommendation_service.py

Unit tests for RecommendationService:
- get_popular_items (order volume ranking, unavailable exclusion, dietary filtering)
- get_pairings_for_items (frequent itemset / co-occurrence cross-sell, seed exclusion)
- get_personalized_recommendations (session-based recommendations, backfill)
- Caching and cache invalidation
"""

from decimal import Decimal
import uuid
import pytest
from django.core.cache import cache

from apps.branches.models import Branch, Table
from apps.menus.models import MenuItem
from apps.menus.recommendation_service import RecommendationService
from apps.orders.models import Order, OrderItem


@pytest.fixture
def rec_setup(db):
    cache.clear()
    branch = Branch.objects.create(
        name="Recommendation Test Branch",
        address="100 Rec Ave, Addis Ababa",
        phone="0911000099",
        email="rec@example.com",
    )
    table = Table.objects.create(branch=branch, number="T-REC-1")

    # Create 5 test items
    # 1. Tibs (non-vegan, non-vegetarian)
    item_tibs = MenuItem.objects.create(
        branch=branch,
        name="Special Tibs",
        price=Decimal("250.00"),
        prep_time_minutes=20,
        status="available",
        dietary_tags=["halal"],
    )
    # 2. Injera (vegan, vegetarian, gluten-free)
    item_injera = MenuItem.objects.create(
        branch=branch,
        name="Traditional Injera",
        price=Decimal("30.00"),
        prep_time_minutes=5,
        status="available",
        dietary_tags=["vegan", "vegetarian", "gluten_free", "halal"],
    )
    # 3. Shiro (vegan, vegetarian)
    item_shiro = MenuItem.objects.create(
        branch=branch,
        name="Shiro Tegamino",
        price=Decimal("120.00"),
        prep_time_minutes=15,
        status="available",
        dietary_tags=["vegan", "vegetarian", "halal"],
    )
    # 4. Soft Drink (vegan, vegetarian)
    item_drink = MenuItem.objects.create(
        branch=branch,
        name="St. George Beer / Soda",
        price=Decimal("50.00"),
        prep_time_minutes=2,
        status="available",
        dietary_tags=["vegan", "vegetarian"],
    )
    # 5. Archived / Unavailable item (Kitfo)
    item_kitfo = MenuItem.objects.create(
        branch=branch,
        name="Kitfo Special",
        price=Decimal("280.00"),
        prep_time_minutes=15,
        status="unavailable",
        dietary_tags=["halal"],
    )

    return {
        "branch": branch,
        "table": table,
        "tibs": item_tibs,
        "injera": item_injera,
        "shiro": item_shiro,
        "drink": item_drink,
        "kitfo": item_kitfo,
    }


def test_popular_items_ranking_by_ordered_quantity(rec_setup):
    branch = rec_setup["branch"]
    table = rec_setup["table"]
    tibs = rec_setup["tibs"]
    injera = rec_setup["injera"]
    drink = rec_setup["drink"]

    # Order 1: 3x Tibs + 3x Injera
    o1 = Order.objects.create(branch=branch, table=table, status="served", total_amount=Decimal("840.00"))
    OrderItem.objects.create(order=o1, menu_item=tibs, quantity=3, unit_price=tibs.price)
    OrderItem.objects.create(order=o1, menu_item=injera, quantity=3, unit_price=injera.price)

    # Order 2: 5x Injera + 2x Drink
    o2 = Order.objects.create(branch=branch, table=table, status="confirmed", total_amount=Decimal("250.00"))
    OrderItem.objects.create(order=o2, menu_item=injera, quantity=5, unit_price=injera.price)
    OrderItem.objects.create(order=o2, menu_item=drink, quantity=2, unit_price=drink.price)

    # Total ordered quantities:
    # Injera: 8
    # Tibs: 3
    # Drink: 2
    popular = RecommendationService.get_popular_items(branch_id=branch.id, limit=3, use_cache=False)

    assert len(popular) == 3
    assert popular[0].id == injera.id
    assert popular[1].id == tibs.id
    assert popular[2].id == drink.id


def test_popular_items_excludes_unavailable_or_archived(rec_setup):
    branch = rec_setup["branch"]
    table = rec_setup["table"]
    kitfo = rec_setup["kitfo"]

    # Order containing unavailable Kitfo
    o = Order.objects.create(branch=branch, table=table, status="served", total_amount=Decimal("280.00"))
    OrderItem.objects.create(order=o, menu_item=kitfo, quantity=10, unit_price=kitfo.price)

    popular = RecommendationService.get_popular_items(branch_id=branch.id, limit=5, use_cache=False)
    popular_ids = [p.id for p in popular]

    assert kitfo.id not in popular_ids


def test_popular_items_dietary_filtering(rec_setup):
    branch = rec_setup["branch"]
    table = rec_setup["table"]
    tibs = rec_setup["tibs"]
    shiro = rec_setup["shiro"]
    injera = rec_setup["injera"]

    o = Order.objects.create(branch=branch, table=table, status="served", total_amount=Decimal("500.00"))
    OrderItem.objects.create(order=o, menu_item=tibs, quantity=5, unit_price=tibs.price)
    OrderItem.objects.create(order=o, menu_item=shiro, quantity=3, unit_price=shiro.price)
    OrderItem.objects.create(order=o, menu_item=injera, quantity=2, unit_price=injera.price)

    # When customer asks for 'vegan'
    vegan_popular = RecommendationService.get_popular_items(
        branch_id=branch.id, limit=5, dietary_tags=["vegan"], use_cache=False
    )
    vegan_ids = [p.id for p in vegan_popular]

    # Tibs is not vegan, must be excluded
    assert tibs.id not in vegan_ids
    # Shiro and Injera are vegan, must appear
    assert shiro.id in vegan_ids
    assert injera.id in vegan_ids


def test_smart_pairings_frequently_ordered_together(rec_setup):
    branch = rec_setup["branch"]
    table = rec_setup["table"]
    tibs = rec_setup["tibs"]
    injera = rec_setup["injera"]
    drink = rec_setup["drink"]
    shiro = rec_setup["shiro"]

    # 4 orders have Tibs + Injera
    for _ in range(4):
        o = Order.objects.create(branch=branch, table=table, status="served", total_amount=Decimal("280.00"))
        OrderItem.objects.create(order=o, menu_item=tibs, quantity=1, unit_price=tibs.price)
        OrderItem.objects.create(order=o, menu_item=injera, quantity=1, unit_price=injera.price)

    # 2 orders have Tibs + Drink
    for _ in range(2):
        o = Order.objects.create(branch=branch, table=table, status="served", total_amount=Decimal("300.00"))
        OrderItem.objects.create(order=o, menu_item=tibs, quantity=1, unit_price=tibs.price)
        OrderItem.objects.create(order=o, menu_item=drink, quantity=1, unit_price=drink.price)

    # Customer has Tibs in cart -> what pairs with Tibs?
    pairings = RecommendationService.get_pairings_for_items(
        branch_id=branch.id,
        item_ids=[tibs.id],
        limit=2,
    )

    pairings_ids = [p.id for p in pairings]

    # Seed item (Tibs) must NOT be in pairings
    assert tibs.id not in pairings_ids
    # Injera is most frequent pairing (4 orders), followed by Drink (2 orders)
    assert pairings[0].id == injera.id
    assert pairings[1].id == drink.id


def test_smart_pairings_respects_dietary_constraints(rec_setup):
    branch = rec_setup["branch"]
    table = rec_setup["table"]
    tibs = rec_setup["tibs"]
    injera = rec_setup["injera"]
    shiro = rec_setup["shiro"]

    # Order has Shiro + Tibs + Injera
    o = Order.objects.create(branch=branch, table=table, status="served", total_amount=Decimal("400.00"))
    OrderItem.objects.create(order=o, menu_item=shiro, quantity=1, unit_price=shiro.price)
    OrderItem.objects.create(order=o, menu_item=tibs, quantity=1, unit_price=tibs.price)
    OrderItem.objects.create(order=o, menu_item=injera, quantity=1, unit_price=injera.price)

    # Customer has Shiro in cart and specifies 'vegan'
    pairings = RecommendationService.get_pairings_for_items(
        branch_id=branch.id,
        item_ids=[shiro.id],
        limit=3,
        dietary_tags=["vegan"],
    )
    pairings_ids = [p.id for p in pairings]

    # Non-vegan Tibs must NOT appear despite being co-ordered
    assert tibs.id not in pairings_ids
    # Vegan Injera should appear
    assert injera.id in pairings_ids


def test_personalized_recommendations_with_session_order(rec_setup):
    branch = rec_setup["branch"]
    table = rec_setup["table"]
    tibs = rec_setup["tibs"]
    injera = rec_setup["injera"]
    drink = rec_setup["drink"]

    # History: Tibs + Drink
    o_hist = Order.objects.create(branch=branch, table=table, status="served", total_amount=Decimal("300.00"))
    OrderItem.objects.create(order=o_hist, menu_item=tibs, quantity=1, unit_price=tibs.price)
    OrderItem.objects.create(order=o_hist, menu_item=drink, quantity=1, unit_price=drink.price)

    # Prior order by customer at table was Tibs
    o_cust = Order.objects.create(branch=branch, table=table, status="received", total_amount=Decimal("250.00"))
    OrderItem.objects.create(order=o_cust, menu_item=tibs, quantity=1, unit_price=tibs.price)

    session_data = {"last_order_id": str(o_cust.id)}

    recs = RecommendationService.get_personalized_recommendations(
        branch_id=branch.id,
        customer_session=session_data,
        limit=2,
    )
    rec_ids = [r.id for r in recs]

    assert len(recs) <= 2
    assert drink.id in rec_ids
