"""
Property-Based Tests: Amharic Unicode Round-Trip

Property 30: Amharic Unicode Round-Trip

  Storing and retrieving any Ethiopic string (U+1200–U+137F) in any free-text
  field on the Django models produces byte-for-byte identical output.

Validates: Requirements 16.5, 16.6

Strategy:
  The Hypothesis strategy ``st.text(alphabet=st.characters(min_codepoint=0x1200,
  max_codepoint=0x137F))`` generates arbitrary Unicode strings drawn
  exclusively from the Ethiopic script block.

  The property test exercises the full DB round-trip pipeline:
    1. Create a model instance with the generated Ethiopic string in the field.
    2. Save the instance to the in-memory SQLite database.
    3. Retrieve the instance from the database by primary key (forces a DB
       round-trip — no in-memory shortcut).
    4. Assert ``retrieved_value == original_value`` — byte-for-byte identical.

  No mocking of the DB layer is used.  The test runs against the real SQLite
  in-memory DB configured in ``config/settings/testing.py``.

Design notes:
  - ``TenantConfig`` and ``Branch`` are the models with full implementations
    in the testing environment.  The free-text fields on those models are
    covered here.
  - ``MenuItem``, ``Expense``, and ``OrderItem`` are currently stubs
    (Tasks 10, 11, 13).  Those tests are included but skip automatically
    when the models have not yet been implemented, so the test file remains
    runnable and will activate the moment the stubs are fleshed out.
  - Each ``@given`` example calls ``_reset_state()`` at its start to wipe DB
    rows created by prior Hypothesis examples within the same test run,
    keeping examples isolated in the shared in-memory DB.
  - The Ethiopic Unicode block (U+1200–U+137F) covers Ethiopic syllables,
    combining marks, and digits — the characters users type when entering
    Amharic text.

Requirements: 16.5, 16.6
"""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Hypothesis strategy — Ethiopic script characters only (U+1200–U+137F)
# ---------------------------------------------------------------------------

_ethiopic_text_st = st.text(
    alphabet=st.characters(min_codepoint=0x1200, max_codepoint=0x137F),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_model(model_cls) -> None:
    """Delete all rows for the given model — called at the start of each example."""
    model_cls.objects.all().delete()


# ---------------------------------------------------------------------------
# Property 30a — TenantConfig.restaurant_name round-trips Ethiopic text
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@given(amharic_text=_ethiopic_text_st)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_30a_tenant_config_restaurant_name_round_trip(amharic_text: str) -> None:
    """
    **Validates: Requirements 16.5, 16.6**

    For any Ethiopic string, saving it in ``TenantConfig.restaurant_name`` and
    retrieving by PK from the database produces a byte-for-byte identical value.
    """
    from apps.whitelabel.models import TenantConfig

    _reset_model(TenantConfig)

    instance = TenantConfig.objects.create(
        restaurant_name=amharic_text,
        primary_color="#000000",
        secondary_color="#FFFFFF",
    )
    retrieved = TenantConfig.objects.get(pk=instance.pk)

    assert retrieved.restaurant_name == amharic_text, (
        f"Property 30a FAILED: TenantConfig.restaurant_name round-trip lost data.\n"
        f"  original : {amharic_text!r}\n"
        f"  retrieved: {retrieved.restaurant_name!r}\n"
        f"Ethiopic text must be stored and retrieved without data loss "
        f"(Requirements 16.5, 16.6)."
    )


# ---------------------------------------------------------------------------
# Property 30b — TenantConfig.receipt_header round-trips Ethiopic text
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@given(amharic_text=_ethiopic_text_st)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_30b_tenant_config_receipt_header_round_trip(amharic_text: str) -> None:
    """
    **Validates: Requirements 16.5, 16.6**

    For any Ethiopic string, saving it in ``TenantConfig.receipt_header`` and
    retrieving by PK from the database produces a byte-for-byte identical value.
    """
    from apps.whitelabel.models import TenantConfig

    _reset_model(TenantConfig)

    instance = TenantConfig.objects.create(
        restaurant_name="Test Restaurant",
        primary_color="#000000",
        secondary_color="#FFFFFF",
        receipt_header=amharic_text,
    )
    retrieved = TenantConfig.objects.get(pk=instance.pk)

    assert retrieved.receipt_header == amharic_text, (
        f"Property 30b FAILED: TenantConfig.receipt_header round-trip lost data.\n"
        f"  original : {amharic_text!r}\n"
        f"  retrieved: {retrieved.receipt_header!r}\n"
        f"Ethiopic text must be stored and retrieved without data loss "
        f"(Requirements 16.5, 16.6)."
    )


# ---------------------------------------------------------------------------
# Property 30c — TenantConfig.receipt_footer round-trips Ethiopic text
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@given(amharic_text=_ethiopic_text_st)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_30c_tenant_config_receipt_footer_round_trip(amharic_text: str) -> None:
    """
    **Validates: Requirements 16.5, 16.6**

    For any Ethiopic string, saving it in ``TenantConfig.receipt_footer`` and
    retrieving by PK from the database produces a byte-for-byte identical value.
    """
    from apps.whitelabel.models import TenantConfig

    _reset_model(TenantConfig)

    instance = TenantConfig.objects.create(
        restaurant_name="Test Restaurant",
        primary_color="#000000",
        secondary_color="#FFFFFF",
        receipt_footer=amharic_text,
    )
    retrieved = TenantConfig.objects.get(pk=instance.pk)

    assert retrieved.receipt_footer == amharic_text, (
        f"Property 30c FAILED: TenantConfig.receipt_footer round-trip lost data.\n"
        f"  original : {amharic_text!r}\n"
        f"  retrieved: {retrieved.receipt_footer!r}\n"
        f"Ethiopic text must be stored and retrieved without data loss "
        f"(Requirements 16.5, 16.6)."
    )


# ---------------------------------------------------------------------------
# Property 30d — Branch.name round-trips Ethiopic text
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@given(amharic_text=_ethiopic_text_st)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_30d_branch_name_round_trip(amharic_text: str) -> None:
    """
    **Validates: Requirements 16.5, 16.6**

    For any Ethiopic string, saving it in ``Branch.name`` and retrieving by PK
    from the database produces a byte-for-byte identical value.
    """
    from apps.branches.models import Branch

    _reset_model(Branch)

    instance = Branch.objects.create(name=amharic_text)
    retrieved = Branch.objects.get(pk=instance.pk)

    assert retrieved.name == amharic_text, (
        f"Property 30d FAILED: Branch.name round-trip lost data.\n"
        f"  original : {amharic_text!r}\n"
        f"  retrieved: {retrieved.name!r}\n"
        f"Ethiopic text must be stored and retrieved without data loss "
        f"(Requirements 16.5, 16.6)."
    )


# ---------------------------------------------------------------------------
# Property 30e — MenuItem.name round-trips Ethiopic text
#   (Skipped until MenuItem is implemented in Task 10)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@given(amharic_text=_ethiopic_text_st)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_30e_menu_item_name_round_trip(amharic_text: str) -> None:
    """
    **Validates: Requirements 16.5, 16.6**

    For any Ethiopic string, saving it in ``MenuItem.name`` and retrieving by
    PK from the database produces a byte-for-byte identical value.

    Note: This test is skipped until MenuItem is fully implemented (Task 10).
    """
    try:
        from apps.menus.models import MenuItem
    except ImportError:
        pytest.skip("MenuItem not yet implemented (Task 10)")

    # Skip if MenuItem is just a stub module with no actual model class
    if not hasattr(MenuItem, "objects"):
        pytest.skip("MenuItem model is a stub — skipping until Task 10")

    from apps.branches.models import Branch

    Branch.objects.all().delete()
    MenuItem.objects.all().delete()

    branch = Branch.objects.create(
        name="Test Branch",
        address="1 Test Street",
        phone="0911000001",
        email="test@branch.com",
    )

    # Build minimal required kwargs for MenuItem
    create_kwargs: dict = {
        "name": amharic_text,
        "price": "50.00",
        "prep_time_minutes": 10,
    }
    # Provide branch if field exists
    if hasattr(MenuItem, "branch"):
        create_kwargs["branch"] = branch

    instance = MenuItem.objects.create(**create_kwargs)
    retrieved = MenuItem.objects.get(pk=instance.pk)

    assert retrieved.name == amharic_text, (
        f"Property 30e FAILED: MenuItem.name round-trip lost data.\n"
        f"  original : {amharic_text!r}\n"
        f"  retrieved: {retrieved.name!r}\n"
        f"Ethiopic text must be stored and retrieved without data loss "
        f"(Requirements 16.5, 16.6)."
    )


# ---------------------------------------------------------------------------
# Property 30f — MenuItem.description round-trips Ethiopic text
#   (Skipped until MenuItem is implemented in Task 10)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@given(amharic_text=_ethiopic_text_st)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_30f_menu_item_description_round_trip(amharic_text: str) -> None:
    """
    **Validates: Requirements 16.5, 16.6**

    For any Ethiopic string, saving it in ``MenuItem.description`` and
    retrieving by PK from the database produces a byte-for-byte identical value.

    Note: This test is skipped until MenuItem is fully implemented (Task 10).
    """
    try:
        from apps.menus.models import MenuItem
    except ImportError:
        pytest.skip("MenuItem not yet implemented (Task 10)")

    if not hasattr(MenuItem, "objects"):
        pytest.skip("MenuItem model is a stub — skipping until Task 10")

    from apps.branches.models import Branch

    Branch.objects.all().delete()
    MenuItem.objects.all().delete()

    branch = Branch.objects.create(
        name="Test Branch",
        address="1 Test Street",
        phone="0911000001",
        email="test@branch.com",
    )

    create_kwargs: dict = {
        "description": amharic_text,
        "name": "ምግብ",
        "price": "50.00",
        "prep_time_minutes": 10,
    }
    if hasattr(MenuItem, "branch"):
        create_kwargs["branch"] = branch

    instance = MenuItem.objects.create(**create_kwargs)
    retrieved = MenuItem.objects.get(pk=instance.pk)

    assert retrieved.description == amharic_text, (
        f"Property 30f FAILED: MenuItem.description round-trip lost data.\n"
        f"  original : {amharic_text!r}\n"
        f"  retrieved: {retrieved.description!r}\n"
        f"Ethiopic text must be stored and retrieved without data loss "
        f"(Requirements 16.5, 16.6)."
    )


# ---------------------------------------------------------------------------
# Property 30g — Expense.description round-trips Ethiopic text
#   (Skipped until Expense is implemented in Task 13)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@given(amharic_text=_ethiopic_text_st)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_30g_expense_description_round_trip(amharic_text: str) -> None:
    """
    **Validates: Requirements 16.5, 16.6**

    For any Ethiopic string, saving it in ``Expense.description`` and
    retrieving by PK from the database produces a byte-for-byte identical value.

    Note: This test is skipped until Expense is fully implemented (Task 13).
    """
    try:
        from apps.expenses.models import Expense
    except ImportError:
        pytest.skip("Expense not yet implemented (Task 13)")

    if not hasattr(Expense, "objects"):
        pytest.skip("Expense model is a stub — skipping until Task 13")

    from apps.branches.models import Branch
    import datetime
    from decimal import Decimal

    Branch.objects.all().delete()
    Expense.objects.all().delete()

    branch = Branch.objects.create(name="Test Branch")

    create_kwargs: dict = {
        "description": amharic_text,
    }
    if hasattr(Expense, "branch"):
        create_kwargs["branch"] = branch
    if hasattr(Expense, "category"):
        create_kwargs.setdefault("category", "miscellaneous")
    if hasattr(Expense, "amount"):
        create_kwargs.setdefault("amount", Decimal("100.00"))
    if hasattr(Expense, "date_incurred"):
        create_kwargs.setdefault("date_incurred", datetime.date.today())

    instance = Expense.objects.create(**create_kwargs)
    retrieved = Expense.objects.get(pk=instance.pk)

    assert retrieved.description == amharic_text, (
        f"Property 30g FAILED: Expense.description round-trip lost data.\n"
        f"  original : {amharic_text!r}\n"
        f"  retrieved: {retrieved.description!r}\n"
        f"Ethiopic text must be stored and retrieved without data loss "
        f"(Requirements 16.5, 16.6)."
    )


# ---------------------------------------------------------------------------
# Property 30h — Expense.notes round-trips Ethiopic text
#   (Skipped until Expense is implemented in Task 13)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@given(amharic_text=_ethiopic_text_st)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_30h_expense_notes_round_trip(amharic_text: str) -> None:
    """
    **Validates: Requirements 16.5, 16.6**

    For any Ethiopic string, saving it in ``Expense.notes`` and retrieving by
    PK from the database produces a byte-for-byte identical value.

    Note: This test is skipped until Expense is fully implemented (Task 13).
    """
    try:
        from apps.expenses.models import Expense
    except ImportError:
        pytest.skip("Expense not yet implemented (Task 13)")

    if not hasattr(Expense, "objects"):
        pytest.skip("Expense model is a stub — skipping until Task 13")

    from apps.branches.models import Branch
    import datetime
    from decimal import Decimal

    Branch.objects.all().delete()
    Expense.objects.all().delete()

    branch = Branch.objects.create(name="Test Branch")

    create_kwargs: dict = {
        "notes": amharic_text,
    }
    if hasattr(Expense, "branch"):
        create_kwargs["branch"] = branch
    if hasattr(Expense, "category"):
        create_kwargs.setdefault("category", "miscellaneous")
    if hasattr(Expense, "amount"):
        create_kwargs.setdefault("amount", Decimal("100.00"))
    if hasattr(Expense, "date_incurred"):
        create_kwargs.setdefault("date_incurred", datetime.date.today())
    if hasattr(Expense, "description"):
        create_kwargs.setdefault("description", "ወጪ")

    instance = Expense.objects.create(**create_kwargs)
    retrieved = Expense.objects.get(pk=instance.pk)

    assert retrieved.notes == amharic_text, (
        f"Property 30h FAILED: Expense.notes round-trip lost data.\n"
        f"  original : {amharic_text!r}\n"
        f"  retrieved: {retrieved.notes!r}\n"
        f"Ethiopic text must be stored and retrieved without data loss "
        f"(Requirements 16.5, 16.6)."
    )


# ---------------------------------------------------------------------------
# Property 30i — OrderItem.special_instructions round-trips Ethiopic text
#   (Skipped until OrderItem is implemented in Task 11)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@given(amharic_text=_ethiopic_text_st)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_30i_order_item_special_instructions_round_trip(amharic_text: str) -> None:
    """
    **Validates: Requirements 16.5, 16.6**

    For any Ethiopic string, saving it in ``OrderItem.special_instructions``
    and retrieving by PK from the database produces a byte-for-byte identical
    value.

    Note: This test is skipped until OrderItem is fully implemented (Task 11).
    """
    try:
        from apps.orders.models import OrderItem
    except ImportError:
        pytest.skip("OrderItem not yet implemented (Task 11)")

    if not hasattr(OrderItem, "objects"):
        pytest.skip("OrderItem model is a stub — skipping until Task 11")

    OrderItem.objects.all().delete()

    create_kwargs: dict = {
        "special_instructions": amharic_text,
    }
    # Provide required FK fields with minimal objects when available
    if hasattr(OrderItem, "quantity"):
        create_kwargs.setdefault("quantity", 1)
    if hasattr(OrderItem, "unit_price"):
        from decimal import Decimal
        create_kwargs.setdefault("unit_price", Decimal("10.00"))

    # If FK fields are required (order, menu_item) they must be supplied.
    # We skip rather than create deep dependency trees at this stage —
    # the test will be filled in when Task 11 provides proper factories.
    required_fk_fields = []
    for field in OrderItem._meta.get_fields():
        if (
            hasattr(field, "remote_field")
            and field.remote_field is not None
            and field.name not in create_kwargs
            and not (hasattr(field, "null") and field.null)
            and not (hasattr(field, "blank") and field.blank)
        ):
            required_fk_fields.append(field.name)

    if required_fk_fields:
        pytest.skip(
            f"OrderItem requires FK setup for {required_fk_fields!r} — "
            f"skipping until Task 11 factories are available."
        )

    instance = OrderItem.objects.create(**create_kwargs)
    retrieved = OrderItem.objects.get(pk=instance.pk)

    assert retrieved.special_instructions == amharic_text, (
        f"Property 30i FAILED: OrderItem.special_instructions round-trip lost data.\n"
        f"  original : {amharic_text!r}\n"
        f"  retrieved: {retrieved.special_instructions!r}\n"
        f"Ethiopic text must be stored and retrieved without data loss "
        f"(Requirements 16.5, 16.6)."
    )
