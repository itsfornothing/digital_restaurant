"""
tests/e2e/conftest.py — Shared fixtures for E2E tests.

Provides fixtures for:
  - branch_with_table: Tenant, Branch, Table #5, active QRCode for Table #5
  - vegan_menu_items: 2 MenuItem records with dietary_tags=['vegan'], status='available'
  - non_vegan_item: 1 MenuItem with no vegan tag
  - kitchen_staff_user: User with role='Kitchen_Staff' linked to the branch

Requirements: 10.1, 14.2–14.10, 17.1, 17.2
"""

import uuid

import pytest
from decimal import Decimal

from apps.authentication.models import User, UserRole
from apps.branches.models import Branch, Table
from apps.menus.models import MenuItem
from apps.qr.models import QRCode


# ---------------------------------------------------------------------------
# Branch and Table fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def branch_with_table(db):
    """
    Create a Branch and Table #5 with an active QRCode.

    Returns:
        tuple: (branch, table, qr_code)
    """
    branch = Branch.objects.create(
        name="E2E Test Branch",
        address="123 E2E Street, Addis Ababa",
        phone="0911223344",
        email="e2e@restaurant.com",
    )
    table = Table.objects.create(
        branch=branch,
        number="5",
        seat_count=4,
    )
    qr_code = QRCode.objects.create(
        table=table,
        token=uuid.uuid4(),
        is_active=True,
        image_url="",
    )
    return branch, table, qr_code


# ---------------------------------------------------------------------------
# Menu item fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vegan_menu_items(db, branch_with_table):
    """
    Create 2 MenuItem records with dietary_tags=['vegan'], status='available',
    is_archived=False, linked to the branch.

    Returns:
        list: [item1, item2]
    """
    branch, table, qr_code = branch_with_table
    
    item1 = MenuItem.objects.create(
        branch=branch,
        name="Vegan Salad",
        description="Fresh mixed greens with tahini dressing",
        price=Decimal("85.00"),
        prep_time_minutes=10,
        status="available",
        is_archived=False,
        dietary_tags=["vegan", "vegetarian"],
    )
    
    item2 = MenuItem.objects.create(
        branch=branch,
        name="Vegan Soup",
        description="Lentil soup with vegetables",
        price=Decimal("60.00"),
        prep_time_minutes=15,
        status="available",
        is_archived=False,
        dietary_tags=["vegan", "vegetarian"],
    )
    
    return [item1, item2]


@pytest.fixture
def non_vegan_item(db, branch_with_table):
    """
    Create 1 MenuItem with no vegan tag, linked to the branch.

    Returns:
        MenuItem: the non-vegan item
    """
    branch, table, qr_code = branch_with_table
    
    item = MenuItem.objects.create(
        branch=branch,
        name="Chicken Tibs",
        description="Sautéed chicken with spices",
        price=Decimal("180.00"),
        prep_time_minutes=20,
        status="available",
        is_archived=False,
        dietary_tags=["halal"],
    )
    
    return item


# ---------------------------------------------------------------------------
# Kitchen staff user fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def kitchen_staff_user(db, branch_with_table):
    """
    Create a User with role='Kitchen_Staff' linked to the branch.

    Returns:
        User: the kitchen staff user
    """
    branch, table, qr_code = branch_with_table
    
    user = User.objects.create_user(
        email="kitchen@e2e.test",
        password="test-password-123",
        role=UserRole.KITCHEN_STAFF,
        branch=branch,
    )
    
    return user
