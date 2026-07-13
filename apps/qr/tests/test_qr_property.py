"""
Property-Based Tests: QR Code Uniqueness Per Branch

# Feature: restaurant-platform, Property 26: QR Code Uniqueness Per Branch

Property 26: QR Code Uniqueness Per Branch

  For any Branch with N tables, all N QR tokens produced by
  QRService.generate_qr() are distinct — no two tables share a token.

Validates: Requirements 14.1

The test creates a Branch with N tables (N in [1, 20]), calls
QRService.generate_qr() for each table, and asserts that the set of resulting
tokens has the same cardinality as N (i.e., all tokens are unique).

Infrastructure calls (file storage, qrcode image rendering) are
mocked so the test remains a fast, isolated unit test focused exclusively on
the token-uniqueness invariant.

Strategy:
  - Generate N in [1, 20] and a matching list of distinct table numbers.
  - Create one Branch and N Table instances in the test database.
  - Patch default_storage.save and qrcode.QRCode.make_image to avoid real I/O.
  - Call QRService().generate_qr(table) for every table.
  - Assert len({qr.token for qr in results}) == N.
"""

import io
import uuid
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from apps.branches.models import Branch, Table
from apps.qr.models import QRCode
from apps.qr.services import QRService


# ---------------------------------------------------------------------------
# Hypothesis strategy: a list of N distinct table number strings
# ---------------------------------------------------------------------------

def _distinct_table_numbers(n: int) -> st.SearchStrategy:
    """Return a strategy producing exactly *n* distinct table number strings."""
    return st.lists(
        st.from_regex(r"[1-9][0-9]?", fullmatch=True),  # "1"–"99"
        min_size=n,
        max_size=n,
        unique=True,
    )


# ---------------------------------------------------------------------------
# Mocking helpers
# ---------------------------------------------------------------------------

class _FakePilImage:
    """Minimal PIL image stub that writes empty bytes when save() is called."""

    def save(self, buffer, format=None, **kwargs):
        buffer.write(b"FAKE_PNG")
        buffer.seek(0)


def _patch_storage_save():
    """
    Patch default_storage.save so it never writes to disk.

    Returns the path to patch and a side_effect callable that echoes back
    the object_name as the 'stored name'.
    """
    return patch(
        "apps.qr.services.default_storage.save",
        side_effect=lambda name, content: name,
    )


def _patch_storage_url():
    """Patch default_storage.url to return a predictable test URL."""
    return patch(
        "apps.qr.services.default_storage.url",
        side_effect=lambda name: f"https://test-storage.example.com/{name}",
    )


def _patch_qrcode_make_image():
    """
    Patch qrcode.QRCode.make_image to return a lightweight fake PIL image
    that avoids running the real image-generation code.
    """
    return patch(
        "apps.qr.services.qrcode.QRCode.make_image",
        return_value=_FakePilImage(),
    )


def _patch_tenant_subdomain():
    """Patch _get_tenant_subdomain to return a fixed value in test context."""
    return patch(
        "apps.qr.services._get_tenant_subdomain",
        return_value="test-tenant",
    )


# ---------------------------------------------------------------------------
# Property 26 — QR Code Uniqueness Per Branch
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(n=st.integers(min_value=1, max_value=20))
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_property_26_qr_tokens_unique_per_branch(n: int) -> None:
    """
    **Validates: Requirements 14.1**

    For any Branch with N tables (N ∈ [1, 20]), calling QRService.generate_qr()
    once per table must produce N QRCode records whose tokens are ALL distinct.

    No two tables in the same branch should share a QR token.

    The test:
      1. Creates a fresh Branch and N Table instances.
      2. Patches R2 upload and qrcode image rendering (infrastructure stubs).
      3. Calls QRService().generate_qr(table) for each table.
      4. Asserts the number of distinct tokens equals N.
    """
    # -----------------------------------------------------------------
    # Arrange: create Branch and N Tables with unique table numbers
    # -----------------------------------------------------------------
    branch = Branch.objects.create(
        name=f"Test Branch {uuid.uuid4().hex[:8]}",
        address="1 Test Street, Addis Ababa",
        phone="0900000000",
        email=f"branch-{uuid.uuid4().hex[:8]}@test.com",
    )

    # Use a sequential naming scheme to guarantee uniqueness without needing
    # Hypothesis to draw distinct strings (avoids flaky shrinking on collisions).
    tables = [
        Table.objects.create(
            branch=branch,
            number=str(i + 1),
            seat_count=2,
        )
        for i in range(n)
    ]

    service = QRService()
    generated_qr_codes = []

    # -----------------------------------------------------------------
    # Act: generate a QR code for each table, with infra stubbed out
    # -----------------------------------------------------------------
    with _patch_storage_save(), _patch_storage_url(), _patch_qrcode_make_image(), _patch_tenant_subdomain():
        for table in tables:
            qr = service.generate_qr(table)
            generated_qr_codes.append(qr)

    # -----------------------------------------------------------------
    # Assert: all N tokens must be distinct
    # -----------------------------------------------------------------
    assert len(generated_qr_codes) == n, (
        f"Expected {n} QRCode records, got {len(generated_qr_codes)}"
    )

    tokens = [qr.token for qr in generated_qr_codes]
    unique_tokens = set(tokens)

    assert len(unique_tokens) == n, (
        f"Expected {n} distinct QR tokens for {n} tables in branch {branch.pk}, "
        f"but only {len(unique_tokens)} were unique.  "
        f"Duplicate token(s): "
        f"{[t for t in tokens if tokens.count(t) > 1]}"
    )

    # -----------------------------------------------------------------
    # Additional assertions: each QRCode is active and DB-persisted
    # -----------------------------------------------------------------
    for qr in generated_qr_codes:
        assert qr.is_active, (
            f"QRCode {qr.pk} (token={qr.token}) must be active immediately after generation"
        )
        assert QRCode.objects.filter(pk=qr.pk).exists(), (
            f"QRCode {qr.pk} must be persisted in the database"
        )

    # -----------------------------------------------------------------
    # Teardown: remove branch (cascades to Tables and QRCodes)
    # -----------------------------------------------------------------
    branch.delete()


# ---------------------------------------------------------------------------
# Property 27 — QR Regeneration Invalidates Prior Codes
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(n_prior=st.integers(min_value=0, max_value=5))
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_property_27_qr_regeneration_invalidates_prior_codes(n_prior: int) -> None:
    """
    **Validates: Requirements 14.3**

    For any Table with any number of prior QR codes (0–5), calling
    QRService.generate_qr() ONCE must:

      1. Deactivate all previously active codes — no active prior codes remain.
      2. Return exactly one new active QRCode with a fresh token.
      3. Old token rejected — validate_qr(old_token) raises QRCodeInvalid for
         every previously active token.
      4. New token accepted — validate_qr(new_token) returns (branch, table)
         without raising.

    Infrastructure (file storage, qrcode image rendering, tenant subdomain) is fully
    mocked so the test runs as a fast, isolated unit test.
    """
    # -----------------------------------------------------------------
    # Arrange: create Branch and one Table
    # -----------------------------------------------------------------
    branch = Branch.objects.create(
        name=f"Test Branch {uuid.uuid4().hex[:8]}",
        address="1 Test Street, Addis Ababa",
        phone="0900000000",
        email=f"branch-{uuid.uuid4().hex[:8]}@test.com",
    )
    table = Table.objects.create(
        branch=branch,
        number="1",
        seat_count=4,
    )

    # -----------------------------------------------------------------
    # Arrange: pre-create n_prior active QRCode records for the table
    # -----------------------------------------------------------------
    prior_tokens = []
    for _ in range(n_prior):
        prior_qr = QRCode.objects.create(
            table=table,
            token=uuid.uuid4(),
            is_active=True,
            image_url="",
        )
        prior_tokens.append(prior_qr.token)

    service = QRService()

    # -----------------------------------------------------------------
    # Act: regenerate the QR code with all infra stubbed out
    # -----------------------------------------------------------------
    with (
        _patch_storage_save(),
        _patch_storage_url(),
        _patch_qrcode_make_image(),
        _patch_tenant_subdomain(),
    ):
        new_qr = service.generate_qr(table)

    new_token = new_qr.token

    # -----------------------------------------------------------------
    # Assert 1: No previously active codes remain active
    # -----------------------------------------------------------------
    still_active_prior = QRCode.objects.filter(
        table=table,
        is_active=True,
    ).exclude(pk=new_qr.pk)

    assert not still_active_prior.exists(), (
        f"Expected all prior QRCodes to be deactivated after regeneration, "
        f"but found {still_active_prior.count()} still active for table {table.pk}."
    )

    # -----------------------------------------------------------------
    # Assert 2: Exactly one new active QRCode exists with a fresh token
    # -----------------------------------------------------------------
    assert new_qr.is_active, (
        f"Newly generated QRCode {new_qr.pk} must have is_active=True."
    )
    assert new_token not in prior_tokens, (
        f"New QR token {new_token} must be different from all prior tokens."
    )
    active_count = QRCode.objects.filter(table=table, is_active=True).count()
    assert active_count == 1, (
        f"Expected exactly 1 active QRCode for table {table.pk} after regeneration, "
        f"but found {active_count}."
    )

    # -----------------------------------------------------------------
    # Assert 3: Old tokens are rejected by validate_qr
    # -----------------------------------------------------------------
    from apps.qr.exceptions import QRCodeInvalid

    for old_token in prior_tokens:
        try:
            service.validate_qr(old_token)
            assert False, (
                f"Expected QRCodeInvalid for deactivated token {old_token}, "
                f"but validate_qr() returned successfully."
            )
        except QRCodeInvalid:
            pass  # Expected — prior tokens must be rejected

    # -----------------------------------------------------------------
    # Assert 4: New token is accepted by validate_qr
    # -----------------------------------------------------------------
    result = service.validate_qr(new_token)

    assert result.table and result.table.pk == table.pk, (
        f"validate_qr returned table {result.table.pk if result.table else None}, expected {table.pk}."
    )
    assert result.branch.pk == branch.pk, (
        f"validate_qr returned branch {result.branch.pk}, expected {branch.pk}."
    )

    # -----------------------------------------------------------------
    # Teardown: remove branch (cascades to Table and QRCodes)
    # -----------------------------------------------------------------
    branch.delete()
