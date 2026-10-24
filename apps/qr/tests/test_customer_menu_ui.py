"""
Tests for customer menu template UI components and localization.
Verifies:
- Category swipe nav bar (.category-nav-bar, #btn-filter-all, #btn-open-filters-modal)
- All filters modal (#allFiltersModal)
- Item categories & tags pop-up modal (#itemTagsModal)
- Full localization in English and Amharic
"""

import uuid
import pytest
from django.test import Client
from apps.branches.models import Branch, Table
from apps.qr.models import QRCode


@pytest.fixture
def active_qr(db):
    branch = Branch.objects.create(
        name="Addis Bistro",
        address="1 Test Street, Addis Ababa",
        phone="0911000001",
        email="test@restaurant.com",
    )
    table = Table.objects.create(branch=branch, number="7", seat_count=4)
    return QRCode.objects.create(
        table=table,
        token=uuid.uuid4(),
        is_active=True,
    )


@pytest.mark.django_db
def test_customer_menu_ui_elements(active_qr):
    client = Client()
    # Create customer session via active QR token
    sess_resp = client.post("/api/v1/customer/session/", {"token": str(active_qr.token)}, content_type="application/json")
    assert sess_resp.status_code == 200

    resp = client.get("/customer/menu/")
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")

    # Category nav bar & modals must be present in DOM
    assert 'class="category-nav-bar"' in content
    assert 'id="btn-filter-all"' in content
    assert 'id="btn-open-filters-modal"' in content
    assert 'id="dietary-filters"' in content
    assert 'id="allFiltersModal"' in content
    assert 'id="itemTagsModal"' in content
    assert 'openItemTagsPopup' in content
    assert 'card-tags-row' in content


@pytest.mark.django_db
def test_customer_menu_ui_amharic_localization(active_qr):
    client = Client()
    client.post("/api/v1/customer/session/", {"token": str(active_qr.token)}, content_type="application/json")

    # Set django_language cookie to Amharic
    client.cookies["django_language"] = "am"
    resp = client.get("/customer/menu/")
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")

    # Verify key Amharic translated strings
    assert "ሁሉም" in content
    assert "ማጣሪያዎች" in content
    assert "ምናሌን አጣራ" in content
    assert "ምድቦች እና መለያዎች" in content
