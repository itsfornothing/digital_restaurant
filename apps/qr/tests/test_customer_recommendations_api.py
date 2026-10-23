"""
apps/qr/tests/test_customer_recommendations_api.py

API tests for GET /api/v1/customer/recommendations/
- Session authentication required (IsCustomerSession)
- Query parameters: type (for_you, popular, pairings), dietary_tags, item_ids, limit, wrap
- Response headers (X-Recommendation-Type, X-Recommendation-Count)
- Multi-branch isolation
"""

from decimal import Decimal
import uuid
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.branches.models import Branch, Table
from apps.menus.models import MenuItem
from apps.orders.models import Order, OrderItem
from apps.qr.models import QRCode


@pytest.fixture
def recommendations_api_setup(db):
    branch = Branch.objects.create(
        name="API Rec Test Branch",
        address="Bole Road, Addis Ababa",
        phone="0911223344",
        email="apirec@example.com",
    )
    table = Table.objects.create(branch=branch, number="T-REC-API")
    qr = QRCode.objects.create(table=table, is_active=True)

    other_branch = Branch.objects.create(
        name="Other Branch",
        address="Piazza, Addis Ababa",
        phone="0922334455",
        email="other@example.com",
    )

    item1 = MenuItem.objects.create(
        branch=branch,
        name="Beef Tibs",
        price=Decimal("200.00"),
        prep_time_minutes=20,
        status="available",
        dietary_tags=["halal"],
    )
    item2 = MenuItem.objects.create(
        branch=branch,
        name="Vegan Beyaynetu",
        price=Decimal("150.00"),
        prep_time_minutes=15,
        status="available",
        dietary_tags=["vegan", "vegetarian"],
    )
    item3 = MenuItem.objects.create(
        branch=branch,
        name="Injera Bread",
        price=Decimal("25.00"),
        prep_time_minutes=5,
        status="available",
        dietary_tags=["vegan", "vegetarian", "gluten_free", "halal"],
    )
    other_item = MenuItem.objects.create(
        branch=other_branch,
        name="Foreign Dish",
        price=Decimal("300.00"),
        prep_time_minutes=15,
        status="available",
        dietary_tags=["vegan"],
    )

    client = APIClient()
    resp = client.post("/api/v1/customer/session/", {"token": str(qr.token)}, format="json")
    assert resp.status_code == 200

    return {
        "branch": branch,
        "table": table,
        "item1": item1,
        "item2": item2,
        "item3": item3,
        "other_item": other_item,
        "client": client,
    }


def test_recommendations_requires_session(db):
    client = APIClient()
    resp = client.get("/api/v1/customer/recommendations/")
    assert resp.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


def test_recommendations_default_for_you(recommendations_api_setup):
    client = recommendations_api_setup["client"]
    resp = client.get("/api/v1/customer/recommendations/")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.headers.get("X-Recommendation-Type") == "for_you"
    assert isinstance(resp.data, list)
    assert len(resp.data) >= 1


def test_recommendations_popular_type(recommendations_api_setup):
    client = recommendations_api_setup["client"]
    resp = client.get("/api/v1/customer/recommendations/?type=popular")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.headers.get("X-Recommendation-Type") == "popular"
    assert isinstance(resp.data, list)


def test_recommendations_pairings_with_item_ids(recommendations_api_setup):
    client = recommendations_api_setup["client"]
    item1 = recommendations_api_setup["item1"]
    resp = client.get(f"/api/v1/customer/recommendations/?type=pairings&item_ids={item1.id}")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.headers.get("X-Recommendation-Type") == "pairings"
    returned_ids = [item["id"] for item in resp.data]
    assert str(item1.id) not in returned_ids


def test_recommendations_dietary_filtering(recommendations_api_setup):
    client = recommendations_api_setup["client"]
    item1 = recommendations_api_setup["item1"]
    resp = client.get("/api/v1/customer/recommendations/?dietary_tags=vegan")
    assert resp.status_code == status.HTTP_200_OK
    for item in resp.data:
        assert "vegan" in item.get("dietary_tags", [])
    returned_ids = [item["id"] for item in resp.data]
    assert str(item1.id) not in returned_ids


def test_recommendations_wrap_envelope(recommendations_api_setup):
    client = recommendations_api_setup["client"]
    resp = client.get("/api/v1/customer/recommendations/?wrap=true")
    assert resp.status_code == status.HTTP_200_OK
    assert isinstance(resp.data, dict)
    assert "recommendation_type" in resp.data
    assert "count" in resp.data
    assert "items" in resp.data
    assert isinstance(resp.data["items"], list)


def test_recommendations_branch_isolation(recommendations_api_setup):
    client = recommendations_api_setup["client"]
    other_item = recommendations_api_setup["other_item"]
    resp = client.get("/api/v1/customer/recommendations/")
    assert resp.status_code == status.HTTP_200_OK
    returned_ids = [item["id"] for item in resp.data]
    assert str(other_item.id) not in returned_ids
