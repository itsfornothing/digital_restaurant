"""
apps/qr/tests/test_qr_api.py

API-level tests for the QR code management endpoints (Task 15.2).

Endpoints under test:
  GET    /api/v1/branches/{branch_pk}/qr-codes/       — list QR codes
  POST   /api/v1/branches/{branch_pk}/qr-codes/       — generate QR for a table
  POST   /api/v1/qr-codes/{pk}/regenerate/            — regenerate (invalidates prior)

Test cases:
  TC-QR01: GET /api/v1/branches/{id}/qr-codes/ as Branch_Manager → 200, own branch only
  TC-QR02: POST /api/v1/branches/{id}/qr-codes/ with valid table_id → 201, QRCode created
  TC-QR03: POST with missing table_id → 400
  TC-QR04: POST with table_id belonging to a different branch → 400
  TC-QR05: POST /api/v1/qr-codes/{id}/regenerate/ → 201, old code deactivated
  TC-QR06: Non-Branch_Manager (Receptionist) → 403 on POST
  TC-QR07: Branch_Manager for branch A accessing branch B's QR codes → 403
  TC-QR08: Regenerate with non-existent QR code pk → 404
  TC-QR09: Unauthenticated request → 401

Requirements: 14.1, 14.3
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

from apps.branches.models import Branch, Table
from apps.qr.models import QRCode

# Ensure the views module is imported so patch("apps.qr.views.QRService") resolves.
import apps.qr.views  # noqa: F401

User = get_user_model()


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def branch_qr_codes_url(branch_pk):
    return f"/api/v1/branches/{branch_pk}/qr-codes/"


def qr_regenerate_url(qr_pk):
    return f"/api/v1/qr-codes/{qr_pk}/regenerate/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def branch(db):
    return Branch.objects.create(
        name="Main Branch",
        address="123 Bole Road, Addis Ababa",
        phone="0911000001",
        email="main@restaurant.com",
    )


@pytest.fixture
def other_branch(db):
    return Branch.objects.create(
        name="Other Branch",
        address="456 Mexico Square, Addis Ababa",
        phone="0911000002",
        email="other@restaurant.com",
    )


@pytest.fixture
def table(db, branch):
    return Table.objects.create(
        branch=branch,
        number="1",
        seat_count=4,
    )


@pytest.fixture
def other_table(db, other_branch):
    return Table.objects.create(
        branch=other_branch,
        number="1",
        seat_count=2,
    )


@pytest.fixture
def branch_manager(db, branch):
    return User.objects.create_user(
        email="manager@restaurant.com",
        password="Pass1234!",
        role="Branch_Manager",
        branch=branch,
    )


@pytest.fixture
def other_branch_manager(db, other_branch):
    return User.objects.create_user(
        email="other.manager@restaurant.com",
        password="Pass1234!",
        role="Branch_Manager",
        branch=other_branch,
    )


@pytest.fixture
def receptionist(db, branch):
    return User.objects.create_user(
        email="receptionist@restaurant.com",
        password="Pass1234!",
        role="Receptionist",
        branch=branch,
    )


@pytest.fixture
def existing_qr_code(db, table):
    """An active QRCode already exists for the table."""
    return QRCode.objects.create(
        table=table,
        token=uuid.uuid4(),
        is_active=True,
        image_url="https://r2.example.com/qr-codes/old.png",
    )


# ---------------------------------------------------------------------------
# Mock QRService to avoid R2 upload in tests
# ---------------------------------------------------------------------------

def _make_qr_service_mock(table, image_url="https://r2.example.com/qr-codes/test.png"):
    """
    Return a mock QRService that creates a real QRCode in the DB but skips
    the R2 upload step.
    """
    def fake_generate_qr(t):
        # Deactivate prior codes (mirrors real service behaviour)
        QRCode.objects.filter(table=t, is_active=True).update(is_active=False)
        return QRCode.objects.create(
            table=t,
            token=uuid.uuid4(),
            is_active=True,
            image_url=image_url,
        )

    mock = MagicMock()
    mock.generate_qr.side_effect = fake_generate_qr
    return mock


# ---------------------------------------------------------------------------
# TC-QR01: GET qr-codes list — Branch_Manager can list own branch's codes
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_list_qr_codes_branch_manager(api_client, branch_manager, branch, table):
    """TC-QR01: Branch_Manager gets 200 with QR codes scoped to their branch."""
    # Create two QR codes for the table
    qr1 = QRCode.objects.create(
        table=table, token=uuid.uuid4(), is_active=False, image_url=""
    )
    qr2 = QRCode.objects.create(
        table=table, token=uuid.uuid4(), is_active=True, image_url=""
    )

    api_client.force_authenticate(user=branch_manager)
    response = api_client.get(branch_qr_codes_url(branch.pk))

    assert response.status_code == status.HTTP_200_OK
    returned_ids = {item["id"] for item in response.data}
    assert str(qr1.pk) in returned_ids
    assert str(qr2.pk) in returned_ids


@pytest.mark.django_db
def test_list_qr_codes_empty_branch(api_client, branch_manager, branch):
    """GET returns empty list when no QR codes exist for the branch."""
    api_client.force_authenticate(user=branch_manager)
    response = api_client.get(branch_qr_codes_url(branch.pk))

    assert response.status_code == status.HTTP_200_OK
    assert response.data == []


# ---------------------------------------------------------------------------
# TC-QR02: POST — generate QR code for a valid table
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_generate_qr_code_success(api_client, branch_manager, branch, table):
    """TC-QR02: POST with valid table_id returns 201 with a new QRCode."""
    with patch("apps.qr.views.QRService") as MockQRService:
        mock_instance = _make_qr_service_mock(table)
        MockQRService.return_value = mock_instance

        api_client.force_authenticate(user=branch_manager)
        payload = {"table_id": str(table.pk)}
        response = api_client.post(branch_qr_codes_url(branch.pk), payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert "id" in response.data
    assert response.data["is_active"] is True
    assert response.data["table_id"] == str(table.pk)
    assert response.data["image_url"] == "https://r2.example.com/qr-codes/test.png"

    # Verify QRCode was persisted in the database
    assert QRCode.objects.filter(pk=response.data["id"]).exists()


# ---------------------------------------------------------------------------
# TC-QR03: POST without table_id → 400
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_generate_qr_missing_table_id(api_client, branch_manager, branch):
    """TC-QR03: POST without table_id field returns 400."""
    api_client.force_authenticate(user=branch_manager)
    response = api_client.post(branch_qr_codes_url(branch.pk), {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "table_id" in str(response.data)


# ---------------------------------------------------------------------------
# TC-QR04: POST with table from a different branch → 400
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_generate_qr_table_wrong_branch(api_client, branch_manager, branch, other_table):
    """TC-QR04: POST with table_id belonging to another branch returns 400."""
    api_client.force_authenticate(user=branch_manager)
    payload = {"table_id": str(other_table.pk)}
    response = api_client.post(branch_qr_codes_url(branch.pk), payload, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# TC-QR05: POST /qr-codes/{id}/regenerate/ — old code deactivated, new one returned
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_regenerate_qr_code(api_client, branch_manager, branch, table, existing_qr_code):
    """TC-QR05: Regenerate deactivates old QRCode and returns a new active one."""
    old_token = existing_qr_code.token

    with patch("apps.qr.views.QRService") as MockQRService:
        mock_instance = _make_qr_service_mock(table, image_url="https://r2.example.com/qr-codes/new.png")
        MockQRService.return_value = mock_instance

        api_client.force_authenticate(user=branch_manager)
        response = api_client.post(qr_regenerate_url(existing_qr_code.pk))

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["is_active"] is True
    # New code has a different token than the old one
    assert response.data["token"] != str(old_token)
    assert response.data["image_url"] == "https://r2.example.com/qr-codes/new.png"

    # Old QRCode should be deactivated
    existing_qr_code.refresh_from_db()
    assert existing_qr_code.is_active is False


# ---------------------------------------------------------------------------
# TC-QR06: Non-Branch_Manager (Receptionist) → 403 on POST
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_generate_qr_non_manager_forbidden(api_client, receptionist, branch, table):
    """TC-QR06: Receptionist cannot POST to generate a QR code → 403."""
    api_client.force_authenticate(user=receptionist)
    payload = {"table_id": str(table.pk)}
    response = api_client.post(branch_qr_codes_url(branch.pk), payload, format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_list_qr_non_manager_forbidden(api_client, receptionist, branch):
    """Receptionist cannot GET QR codes list → 403."""
    api_client.force_authenticate(user=receptionist)
    response = api_client.get(branch_qr_codes_url(branch.pk))

    assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# TC-QR07: Branch_Manager for branch A accessing branch B's QR codes → 403
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_list_qr_cross_branch_forbidden(api_client, branch_manager, other_branch):
    """TC-QR07: Branch_Manager for branch A cannot access branch B's QR codes → 403."""
    api_client.force_authenticate(user=branch_manager)
    response = api_client.get(branch_qr_codes_url(other_branch.pk))

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_generate_qr_cross_branch_forbidden(api_client, branch_manager, other_branch, other_table):
    """Branch_Manager cannot generate QR for another branch's table → 403."""
    api_client.force_authenticate(user=branch_manager)
    payload = {"table_id": str(other_table.pk)}
    response = api_client.post(branch_qr_codes_url(other_branch.pk), payload, format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# TC-QR08: Regenerate with non-existent QR code pk → 404
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_regenerate_nonexistent_qr_code(api_client, branch_manager):
    """TC-QR08: Regenerate with a UUID that doesn't exist → 404."""
    api_client.force_authenticate(user=branch_manager)
    nonexistent_pk = uuid.uuid4()
    response = api_client.post(qr_regenerate_url(nonexistent_pk))

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# TC-QR09: Unauthenticated request → 401 or 403
# ---------------------------------------------------------------------------
# DRF returns 401 when a WWW-Authenticate header is present (Token/Basic auth),
# or 403 when session-only authentication is used and no credentials are supplied.
# The testing settings use SessionAuthentication, so unauthenticated requests
# receive 403 Forbidden.  We accept either 401 or 403 here to handle both
# session-auth (403) and token-auth (401) configurations.

@pytest.mark.django_db
def test_list_qr_unauthenticated(api_client, branch):
    """TC-QR09: Unauthenticated GET is rejected (401 or 403)."""
    response = api_client.get(branch_qr_codes_url(branch.pk))
    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


@pytest.mark.django_db
def test_generate_qr_unauthenticated(api_client, branch, table):
    """Unauthenticated POST is rejected (401 or 403)."""
    payload = {"table_id": str(table.pk)}
    response = api_client.post(branch_qr_codes_url(branch.pk), payload, format="json")
    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


@pytest.mark.django_db
def test_regenerate_unauthenticated(api_client, existing_qr_code):
    """Unauthenticated regenerate is rejected (401 or 403)."""
    response = api_client.post(qr_regenerate_url(existing_qr_code.pk))
    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


# ---------------------------------------------------------------------------
# Serializer response shape validation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_qr_code_response_shape(api_client, branch_manager, branch, table):
    """Generated QRCode response includes all expected fields."""
    with patch("apps.qr.views.QRService") as MockQRService:
        mock_instance = _make_qr_service_mock(table)
        MockQRService.return_value = mock_instance

        api_client.force_authenticate(user=branch_manager)
        payload = {"table_id": str(table.pk)}
        response = api_client.post(branch_qr_codes_url(branch.pk), payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    data = response.data
    expected_fields = {"id", "table_id", "token", "is_active", "image_url", "created_at"}
    assert expected_fields.issubset(set(data.keys()))


# ---------------------------------------------------------------------------
# Regeneration cross-branch scope check
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_regenerate_cross_branch_forbidden(api_client, branch_manager, other_branch, other_table):
    """Branch_Manager cannot regenerate a QR code belonging to another branch."""
    # Create a QR code for other_branch's table
    qr = QRCode.objects.create(
        table=other_table,
        token=uuid.uuid4(),
        is_active=True,
        image_url="",
    )

    api_client.force_authenticate(user=branch_manager)
    response = api_client.post(qr_regenerate_url(qr.pk))

    assert response.status_code == status.HTTP_403_FORBIDDEN


# ===========================================================================
# Task 15.5 — QR code API tests (TC-Q01, TC-Q02, TC-API13, TC-API14)
# Requirements: 14.1, 14.3, 14.4
# ===========================================================================

# ---------------------------------------------------------------------------
# Additional URL helpers (customer-facing)
# ---------------------------------------------------------------------------

CUSTOMER_SESSION_URL = "/api/v1/customer/session/"
CUSTOMER_MENU_URL = "/api/v1/customer/menu/"


def customer_session_url():
    return CUSTOMER_SESSION_URL


def customer_menu_url():
    return CUSTOMER_MENU_URL


# ---------------------------------------------------------------------------
# Additional fixtures for customer / menu tests
# ---------------------------------------------------------------------------

@pytest.fixture
def other_branch_for_scope(db):
    """A second branch used to verify menu scoping in TC-Q01."""
    return Branch.objects.create(
        name="Other Restaurant Branch",
        address="789 Piassa, Addis Ababa",
        phone="0911000099",
        email="other2@restaurant.com",
    )


@pytest.fixture
def menu_item_in_branch(db, branch):
    """An active, non-archived MenuItem belonging to the primary branch."""
    from apps.menus.models import MenuItem
    return MenuItem.objects.create(
        branch=branch,
        name="Doro Wat",
        description="Spiced chicken stew",
        price="150.00",
        prep_time_minutes=40,
        status="available",
        is_archived=False,
        dietary_tags=["halal"],
    )


@pytest.fixture
def menu_item_in_other_branch(db, other_branch):
    """An active MenuItem belonging to a *different* branch (should not appear in TC-Q01)."""
    from apps.menus.models import MenuItem
    return MenuItem.objects.create(
        branch=other_branch,
        name="Kitfo",
        description="Ethiopian beef tartare",
        price="200.00",
        prep_time_minutes=10,
        status="available",
        is_archived=False,
        dietary_tags=[],
    )


@pytest.fixture
def active_qr_code(db, table):
    """An active QRCode for the primary branch's table (no R2 upload needed in tests)."""
    return QRCode.objects.create(
        table=table,
        token=uuid.uuid4(),
        is_active=True,
        image_url="https://r2.example.com/qr-codes/active.png",
    )


def _init_customer_session(api_client, token):
    """
    Helper: POST /api/v1/customer/session/ with the given QR token.

    Uses the session-aware test client (requests library doesn't maintain
    the Django session automatically), so we call through the API client
    and return the response.  The session cookie is preserved on api_client
    for subsequent requests.
    """
    return api_client.post(
        customer_session_url(),
        {"token": str(token)},
        format="json",
    )


# ---------------------------------------------------------------------------
# TC-Q01: GET /api/v1/customer/menu/ via valid QR session
#         Menu loads scoped to the correct branch; no other branch items appear.
#         Requirements: 14.1, 14.2
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_tc_q01_customer_menu_scoped_to_branch(
    api_client,
    table,
    active_qr_code,
    menu_item_in_branch,
    menu_item_in_other_branch,
):
    """
    TC-Q01: GET /api/v1/customer/menu/ via valid QR session returns menu items
    scoped to the session's branch; items from other branches do not appear.

    Requirements: 14.1, 14.2
    """
    # Step 1: initialise a customer session by scanning the valid QR code
    session_resp = _init_customer_session(api_client, active_qr_code.token)
    assert session_resp.status_code == status.HTTP_200_OK, (
        f"Session creation must succeed with active token, got {session_resp.status_code}: "
        f"{session_resp.data}"
    )
    assert session_resp.data["branch_id"] == str(active_qr_code.table.branch_id)

    # Step 2: GET the customer menu
    menu_resp = api_client.get(customer_menu_url())
    assert menu_resp.status_code == status.HTTP_200_OK, (
        f"Customer menu must return 200 with active session, got {menu_resp.status_code}: "
        f"{menu_resp.data}"
    )

    returned_ids = {item["id"] for item in menu_resp.data}

    # Item from the session's branch MUST appear
    assert str(menu_item_in_branch.id) in returned_ids, (
        "Menu item from the session's branch must be present in the customer menu"
    )

    # Item from a DIFFERENT branch MUST NOT appear (scope enforcement)
    assert str(menu_item_in_other_branch.id) not in returned_ids, (
        "Menu item from another branch must NOT appear in the customer menu (scope violation)"
    )

    # Each returned item must include required display fields (Req 14.5)
    for item in menu_resp.data:
        assert "id" in item
        assert "name" in item
        assert "price" in item
        assert "branch_id" in item
        # Items returned must belong only to the session branch
        assert item["branch_id"] == str(active_qr_code.table.branch_id), (
            f"Item {item['id']} belongs to branch {item['branch_id']} "
            f"but session branch is {active_qr_code.table.branch_id}"
        )


# ---------------------------------------------------------------------------
# TC-Q02: Regenerate QR code; old token → QR_CODE_INVALID error
#         Requirements: 14.3, 14.4
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_tc_q02_old_token_invalid_after_regenerate(
    api_client,
    branch_manager,
    branch,
    table,
    active_qr_code,
):
    """
    TC-Q02: After regenerating a QR code (which deactivates all prior codes),
    using the OLD token to create a customer session returns QR_CODE_INVALID.

    Requirements: 14.3, 14.4
    """
    old_token = active_qr_code.token

    # Step 1: Branch_Manager regenerates the QR code
    with patch("apps.qr.views.QRService") as MockQRService:
        mock_instance = _make_qr_service_mock(table, image_url="https://r2.example.com/new.png")
        MockQRService.return_value = mock_instance

        api_client.force_authenticate(user=branch_manager)
        regen_resp = api_client.post(qr_regenerate_url(active_qr_code.pk))

    assert regen_resp.status_code == status.HTTP_201_CREATED, (
        f"Regeneration must succeed, got {regen_resp.status_code}: {regen_resp.data}"
    )

    # Confirm the old code is now inactive
    active_qr_code.refresh_from_db()
    assert active_qr_code.is_active is False, "Old QRCode must be deactivated after regeneration"

    # Step 2: Unauthenticate to simulate a customer (no staff session)
    api_client.force_authenticate(user=None)

    # Step 3: Attempt to create a customer session with the OLD (invalidated) token
    session_resp = api_client.post(
        customer_session_url(),
        {"token": str(old_token)},
        format="json",
    )

    # Must be 404 or 410 with a user-friendly error (Req 14.4)
    assert session_resp.status_code in (
        status.HTTP_404_NOT_FOUND,
        status.HTTP_410_GONE,
    ), (
        f"Using an invalidated QR token must return 404 or 410, "
        f"got {session_resp.status_code}: {session_resp.data}"
    )

    # Response body must carry the QR_CODE_INVALID error code (user-friendly, Req 14.4)
    response_text = str(session_resp.data)
    assert "QR_CODE_INVALID" in response_text or "invalid" in response_text.lower(), (
        f"Response must indicate QR code is invalid, got: {response_text}"
    )

    # Must NOT expose a stack trace
    assert "Traceback" not in response_text, "Response must not contain a stack trace"
    assert "Exception" not in response_text, "Response must not expose exception details"


# ---------------------------------------------------------------------------
# TC-API13: GET /api/v1/branches/{id}/qr-codes/ for branch with active QR code
#           Returns 200 with the QRCode record including a non-empty image_url.
#           Requirements: 14.1, 14.3
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_tc_api13_list_qr_codes_returns_image_url(
    api_client,
    branch_manager,
    branch,
    table,
):
    """
    TC-API13: GET /api/v1/branches/{id}/qr-codes/ for a branch with an active
    QR code returns 200, with the QRCode record present and image_url non-empty.

    Requirements: 14.1, 14.3
    """
    # Setup: create an active QRCode with a concrete image_url
    active_code = QRCode.objects.create(
        table=table,
        token=uuid.uuid4(),
        is_active=True,
        image_url="https://r2.example.com/qr-codes/branch-active.png",
    )

    api_client.force_authenticate(user=branch_manager)
    response = api_client.get(branch_qr_codes_url(branch.pk))

    assert response.status_code == status.HTTP_200_OK, (
        f"GET /api/v1/branches/{{id}}/qr-codes/ must return 200, "
        f"got {response.status_code}: {response.data}"
    )

    # The active QR code must appear in the list
    returned_ids = {item["id"] for item in response.data}
    assert str(active_code.pk) in returned_ids, (
        "Active QRCode must be present in the list response"
    )

    # Locate the record and verify image_url is present and non-empty
    qr_record = next(item for item in response.data if item["id"] == str(active_code.pk))
    assert "image_url" in qr_record, "Response must include the image_url field"
    assert qr_record["image_url"], (
        "image_url must be non-empty for an active QR code (Req 14.1)"
    )
    assert qr_record["image_url"] == active_code.image_url, (
        "Returned image_url must match the stored value"
    )
    assert qr_record["is_active"] is True, "The returned QRCode must be marked active"


# ---------------------------------------------------------------------------
# TC-API14: POST /api/v1/customer/session/ with an invalidated token
#           Returns 404 or 410 with user-friendly message (no stack trace).
#           Requirements: 14.4
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_tc_api14_session_with_invalidated_token(
    api_client,
    table,
):
    """
    TC-API14: POST /api/v1/customer/session/ with a QR token that has been
    explicitly deactivated (is_active=False) returns 404 or 410 with a
    user-friendly error message and no stack trace.

    Requirements: 14.4
    """
    # Setup: create a QRCode and immediately deactivate it directly
    invalidated_code = QRCode.objects.create(
        table=table,
        token=uuid.uuid4(),
        is_active=True,
        image_url="https://r2.example.com/qr-codes/will-be-invalidated.png",
    )
    # Directly set is_active=False (simulates manual invalidation)
    invalidated_code.is_active = False
    invalidated_code.save(update_fields=["is_active"])

    # Confirm deactivated
    invalidated_code.refresh_from_db()
    assert invalidated_code.is_active is False, "Pre-condition: QRCode must be inactive"

    # Action: POST to customer session with the invalidated token
    response = api_client.post(
        customer_session_url(),
        {"token": str(invalidated_code.token)},
        format="json",
    )

    # Requirement 14.4: 404 or 410 status
    assert response.status_code in (
        status.HTTP_404_NOT_FOUND,
        status.HTTP_410_GONE,
    ), (
        f"Invalidated token must return 404 or 410, "
        f"got {response.status_code}: {response.data}"
    )

    # Response body must contain a user-friendly message (Req 14.4)
    response_text = str(response.data)

    # Must have a meaningful error code or message in the body
    assert response.data, "Response body must not be empty"
    has_error_detail = (
        "detail" in response.data
        or "code" in response.data
        or "message" in response.data
    )
    assert has_error_detail, (
        f"Response must contain a user-friendly error message, got: {response.data}"
    )

    # Must NOT expose a raw stack trace (Req 14.4)
    assert "Traceback" not in response_text, (
        "Response must not expose a Python stack trace to the customer"
    )
    assert "Exception" not in response_text, (
        "Response must not expose raw exception class names to the customer"
    )
