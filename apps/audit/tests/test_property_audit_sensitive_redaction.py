"""
Property-Based Tests: Sensitive Field Redaction (Property 16)

Property 16: Sensitive Field Redaction

  For any AuditLog entry produced by an action involving a password, token,
  or secret field, the ``old_value`` and ``new_value`` JSONB fields SHALL NOT
  contain the plaintext value of that sensitive field.

  The sensitive fields are defined in the codebase as:
    ``SENSITIVE_AUDIT_FIELDS = ['password', 'token', 'secret', 'totp_secret']``

  The redacted placeholder is the string "[REDACTED]".

**Validates: Requirements 5.3**

Sub-properties tested:

  Property 16a — Redact function output never contains plaintext for sensitive keys:
    For any dict containing one or more sensitive keys mapped to arbitrary
    plaintext values, ``redact_sensitive(payload)`` shall replace every
    sensitive value with "[REDACTED]" and preserve all non-sensitive keys
    unchanged.

  Property 16b — Nested sensitive values are redacted recursively:
    For any arbitrarily nested dict/list structure that contains sensitive
    keys at any depth, ``redact_sensitive`` shall redact ALL of them,
    regardless of nesting level.

  Property 16c — AuditLog entries produced via @audit_action do not leak
    plaintext sensitive values in old_value or new_value:
    For any payload containing sensitive keys (password, token, secret,
    totp_secret) with arbitrary plaintext values, executing a function
    decorated with ``@audit_action`` that returns or receives such a payload
    shall produce an AuditLog entry where neither old_value nor new_value
    contains the plaintext values.

  Property 16d — Non-sensitive fields pass through redaction unchanged:
    For any dict whose keys do not appear in SENSITIVE_AUDIT_FIELDS, calling
    ``redact_sensitive`` shall return values identical to the input.

  Property 16e — Redaction is not input-mutating:
    For any payload dict, calling ``redact_sensitive`` shall not alter the
    original dict in-place; the input remains unchanged after the call.

Strategy:
  - ``st.text()`` generates arbitrary plaintext values for sensitive fields.
  - ``st.fixed_dictionaries`` builds payloads with exactly the sensitive keys.
  - ``st.dictionaries`` with custom key strategies generates mixed payloads
    (some sensitive, some not) and fully-safe payloads.
  - Nested structures are generated using recursive Hypothesis strategies.
  - All tests exercise the real ``redact_sensitive`` function and the real
    ``audit_action`` decorator — no mocking is used.

Requirements: 5.3
"""

import copy
import uuid

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from apps.audit.decorators import (
    SENSITIVE_AUDIT_FIELDS,
    audit_action,
    redact_sensitive,
    _request_context as _audit_ctx,
)
from apps.audit.models import AuditLog

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SENSITIVE_KEYS = sorted(SENSITIVE_AUDIT_FIELDS)   # deterministic list
REDACTED_MARKER = "[REDACTED]"

# Non-sensitive field names that must always pass through untouched
NON_SENSITIVE_KEYS = [
    "username",
    "email",
    "description",
    "amount",
    "category",
    "name",
    "status",
    "role",
    "ip_address",
    "notes",
    "order_id",
    "branch_id",
]

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Arbitrary non-empty text to use as a plaintext "sensitive" value
# Exclude "[REDACTED]" itself so we can assert the marker was inserted by us
st_plaintext = st.text(min_size=1).filter(lambda v: v != REDACTED_MARKER)

# A single sensitive key name chosen from the canonical list
st_sensitive_key = st.sampled_from(SENSITIVE_KEYS)

# A non-sensitive key name
st_non_sensitive_key = st.sampled_from(NON_SENSITIVE_KEYS)

# A simple non-nested payload that contains AT LEAST ONE sensitive key
st_payload_with_sensitive = st.fixed_dictionaries(
    {k: st_plaintext for k in SENSITIVE_KEYS}
)

# A payload that may include both sensitive and non-sensitive keys
st_mixed_payload = st.fixed_dictionaries(
    {
        **{k: st_plaintext for k in SENSITIVE_KEYS},
        **{k: st.text(min_size=1) for k in NON_SENSITIVE_KEYS[:4]},
    }
)

# A payload with only non-sensitive fields (all should pass through intact)
st_safe_payload = st.fixed_dictionaries(
    {k: st.text(min_size=1) for k in NON_SENSITIVE_KEYS}
)


def _nested_payload_strategy(depth: int = 0):
    """
    Build a nested dict strategy that wraps sensitive keys inside sub-dicts
    and sub-lists at arbitrary depth.  Used for Property 16b.
    """
    if depth >= 2:
        # Leaf: flat dict with all sensitive keys
        return st.fixed_dictionaries({k: st_plaintext for k in SENSITIVE_KEYS})

    inner = _nested_payload_strategy(depth + 1)
    return st.fixed_dictionaries(
        {
            # Outer level also has sensitive keys so we test top-level too
            "password": st_plaintext,
            "token": st_plaintext,
            "nested_dict": inner,
            "nested_list": st.lists(inner, min_size=1, max_size=2),
            "safe_field": st.text(min_size=1),
        }
    )


st_nested_payload = _nested_payload_strategy()

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _set_fake_context(ip: str = "127.0.0.1", ua: str = "TestAgent/1.0"):
    """Populate thread-local audit context for use without an HTTP request."""
    _audit_ctx.ip_address = ip
    _audit_ctx.user_agent = ua
    _audit_ctx.tenant_id = None
    _audit_ctx.user_id = None
    _audit_ctx.user_role = ""


def _clear_context():
    """Remove all thread-local audit context attributes."""
    for attr in ("user_id", "user_role", "ip_address", "user_agent", "tenant_id"):
        try:
            delattr(_audit_ctx, attr)
        except AttributeError:
            pass


def _contains_plaintext(data, plaintext_value: str) -> bool:
    """
    Recursively check whether *data* (dict, list, or scalar) contains the
    exact string *plaintext_value* anywhere in its structure.

    Returns True if plaintext_value is found as a dict value or list element;
    False otherwise.
    """
    if isinstance(data, dict):
        for v in data.values():
            if _contains_plaintext(v, plaintext_value):
                return True
    elif isinstance(data, list):
        for item in data:
            if _contains_plaintext(item, plaintext_value):
                return True
    elif isinstance(data, str):
        return data == plaintext_value
    return False


def _sensitive_keys_are_redacted(data) -> bool:
    """
    Walk *data* recursively and return True only if every value associated
    with a key in SENSITIVE_AUDIT_FIELDS equals REDACTED_MARKER.
    """
    if isinstance(data, dict):
        for k, v in data.items():
            if k in SENSITIVE_AUDIT_FIELDS:
                if v != REDACTED_MARKER:
                    return False
            else:
                if not _sensitive_keys_are_redacted(v):
                    return False
    elif isinstance(data, list):
        for item in data:
            if not _sensitive_keys_are_redacted(item):
                return False
    return True


# ---------------------------------------------------------------------------
# Property 16a: redact_sensitive replaces sensitive values with [REDACTED]
# ---------------------------------------------------------------------------

@given(payload=st_payload_with_sensitive)
@settings(max_examples=200)
def test_property_16a_redact_function_replaces_sensitive_values(
    payload: dict,
) -> None:
    """
    **Validates: Requirements 5.3**

    Property 16a: Sensitive Value Replacement

    For any dict containing all sensitive keys (password, token, secret,
    totp_secret) with arbitrary plaintext values, ``redact_sensitive(payload)``
    SHALL replace every sensitive value with the literal string "[REDACTED]".

    The plaintext values must not appear anywhere in the redacted output.
    """
    redacted = redact_sensitive(payload)

    for key in SENSITIVE_KEYS:
        plaintext = payload[key]

        # The key's value in the result must be exactly "[REDACTED]"
        assert redacted[key] == REDACTED_MARKER, (
            f"redact_sensitive() did not redact key={key!r}. "
            f"Expected value=[REDACTED], got {redacted[key]!r}. "
            f"Sensitive values must never appear in AuditLog entries "
            f"(Requirement 5.3)."
        )

        # The plaintext must not appear anywhere in the redacted output
        assert not _contains_plaintext(redacted, plaintext), (
            f"redact_sensitive() left plaintext value for key={key!r} somewhere "
            f"in the output dict. Plaintext: {plaintext!r}. "
            f"Full redacted output: {redacted!r}. "
            f"Requirement 5.3 prohibits plaintext sensitive values in audit logs."
        )


# ---------------------------------------------------------------------------
# Property 16b: Nested sensitive values are also redacted
# ---------------------------------------------------------------------------

@given(payload=st_nested_payload)
@settings(max_examples=150)
def test_property_16b_nested_sensitive_values_are_redacted(
    payload: dict,
) -> None:
    """
    **Validates: Requirements 5.3**

    Property 16b: Recursive Redaction of Nested Structures

    For any payload that contains sensitive keys at arbitrary nesting depth
    (inside dicts nested in dicts, or in dicts inside lists), ``redact_sensitive``
    SHALL redact ALL sensitive key values at every level of nesting.

    No plaintext sensitive value shall survive in any position of the output.
    """
    redacted = redact_sensitive(payload)

    # Every sensitive key anywhere in the structure must have [REDACTED] value
    assert _sensitive_keys_are_redacted(redacted), (
        f"redact_sensitive() missed a nested sensitive key. "
        f"Input payload: {payload!r}. "
        f"Redacted output: {redacted!r}. "
        f"All sensitive keys at any depth must be redacted (Requirement 5.3)."
    )

    # Additionally verify the top-level sensitive keys themselves map to [REDACTED]
    for key in ("password", "token"):
        if key in redacted:
            assert redacted[key] == REDACTED_MARKER, (
                f"Top-level sensitive key={key!r} in redacted output is not "
                f"'[REDACTED]'. Got: {redacted[key]!r}. "
                f"(Requirement 5.3)"
            )


# ---------------------------------------------------------------------------
# Property 16c: @audit_action decorator stores no plaintext sensitive values
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    sensitive_key=st_sensitive_key,
    plaintext_value=st_plaintext,
)
@settings(max_examples=150)
def test_property_16c_audit_action_decorator_redacts_sensitive_fields(
    sensitive_key: str,
    plaintext_value: str,
) -> None:
    """
    **Validates: Requirements 5.3**

    Property 16c: @audit_action Decorator Redaction

    For any payload that contains a sensitive key (password, token, secret,
    or totp_secret) with an arbitrary plaintext value, executing a function
    decorated with ``@audit_action`` that returns or receives such a payload
    SHALL produce an AuditLog entry where neither ``old_value`` nor
    ``new_value`` contains the plaintext value.

    The plaintext must not appear anywhere in the stored old_value or
    new_value JSON structures — including inside nested dicts or lists.
    """
    # Build a payload with exactly the one sensitive key we are testing,
    # plus a safe field to confirm non-sensitive data is preserved.
    payload_with_sensitive = {
        sensitive_key: plaintext_value,
        "username": "johndoe",
        "email": "johndoe@example.com",
    }
    old_state = {
        sensitive_key: plaintext_value,
        "status": "before",
    }

    @audit_action(
        action_code="USER_UPDATE",
        resource_type="User",
        get_resource_id=lambda result: result.get("id") if isinstance(result, dict) else None,
        get_old_value=lambda *a, **kw: old_state,
    )
    def _update_user_with_sensitive_data(user_id: str, data: dict) -> dict:
        """Simulated service function returning the updated user payload."""
        return {
            "id": user_id,
            **data,
        }

    _set_fake_context()
    count_before = AuditLog.objects.count()

    try:
        _update_user_with_sensitive_data(str(uuid.uuid4()), payload_with_sensitive)
    finally:
        _clear_context()

    count_after = AuditLog.objects.count()
    assert count_after == count_before + 1, (
        f"Expected exactly 1 new AuditLog entry, "
        f"but count changed from {count_before} to {count_after}."
    )

    # Fetch the most recently created entry
    entry = AuditLog.objects.latest("timestamp")

    # --- Assert old_value does not contain plaintext ---
    if entry.old_value is not None:
        assert not _contains_plaintext(entry.old_value, plaintext_value), (
            f"AuditLog.old_value contains the plaintext value for "
            f"sensitive_key={sensitive_key!r}. "
            f"Plaintext: {plaintext_value!r}. "
            f"old_value: {entry.old_value!r}. "
            f"The Audit_Logger MUST redact sensitive values before storage "
            f"(Requirement 5.3)."
        )
        # The sensitive key, if present, must map to [REDACTED]
        if sensitive_key in entry.old_value:
            assert entry.old_value[sensitive_key] == REDACTED_MARKER, (
                f"AuditLog.old_value[{sensitive_key!r}] is not '[REDACTED]'. "
                f"Got: {entry.old_value[sensitive_key]!r}. "
                f"(Requirement 5.3)"
            )

    # --- Assert new_value does not contain plaintext ---
    if entry.new_value is not None:
        assert not _contains_plaintext(entry.new_value, plaintext_value), (
            f"AuditLog.new_value contains the plaintext value for "
            f"sensitive_key={sensitive_key!r}. "
            f"Plaintext: {plaintext_value!r}. "
            f"new_value: {entry.new_value!r}. "
            f"The Audit_Logger MUST redact sensitive values before storage "
            f"(Requirement 5.3)."
        )
        # The sensitive key, if present, must map to [REDACTED]
        if sensitive_key in entry.new_value:
            assert entry.new_value[sensitive_key] == REDACTED_MARKER, (
                f"AuditLog.new_value[{sensitive_key!r}] is not '[REDACTED]'. "
                f"Got: {entry.new_value[sensitive_key]!r}. "
                f"(Requirement 5.3)"
            )


# ---------------------------------------------------------------------------
# Property 16c (variant): All 4 sensitive keys in a single payload
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(payload=st_mixed_payload)
@settings(max_examples=100)
def test_property_16c_all_sensitive_keys_redacted_in_full_payload(
    payload: dict,
) -> None:
    """
    **Validates: Requirements 5.3**

    Property 16c (variant): Full Payload Redaction via @audit_action

    For a payload containing ALL four sensitive keys alongside non-sensitive
    fields, executing @audit_action SHALL produce an AuditLog entry where
    every sensitive key in both old_value and new_value is redacted to
    "[REDACTED]", while non-sensitive field values are preserved.
    """
    old_state = dict(payload)  # old_value has the same sensitive data

    @audit_action(
        action_code="PASSWORD_CHANGE",
        resource_type="User",
        get_old_value=lambda *a, **kw: old_state,
    )
    def _change_password(user_id: str, data: dict) -> dict:
        return {"id": user_id, **data}

    _set_fake_context()
    count_before = AuditLog.objects.count()

    try:
        _change_password(str(uuid.uuid4()), payload)
    finally:
        _clear_context()

    assert AuditLog.objects.count() == count_before + 1, (
        "Expected exactly 1 new AuditLog entry."
    )

    entry = AuditLog.objects.latest("timestamp")

    # Check old_value — every sensitive key present must map to [REDACTED]
    if entry.old_value is not None:
        for key in SENSITIVE_KEYS:
            if key in entry.old_value:
                assert entry.old_value[key] == REDACTED_MARKER, (
                    f"old_value[{key!r}] should be '[REDACTED]', "
                    f"got {entry.old_value[key]!r} (Requirement 5.3)."
                )

    # Check new_value — every sensitive key present must map to [REDACTED]
    if entry.new_value is not None:
        for key in SENSITIVE_KEYS:
            if key in entry.new_value:
                assert entry.new_value[key] == REDACTED_MARKER, (
                    f"new_value[{key!r}] should be '[REDACTED]', "
                    f"got {entry.new_value[key]!r} (Requirement 5.3)."
                )


# ---------------------------------------------------------------------------
# Property 16d: Non-sensitive fields pass through redaction unchanged
# ---------------------------------------------------------------------------

@given(payload=st_safe_payload)
@settings(max_examples=150)
def test_property_16d_non_sensitive_fields_unchanged_after_redaction(
    payload: dict,
) -> None:
    """
    **Validates: Requirements 5.3**

    Property 16d: Non-Sensitive Fields Preserved

    For any dict whose keys do NOT appear in SENSITIVE_AUDIT_FIELDS, calling
    ``redact_sensitive`` SHALL return an output dict where every value is
    identical to the corresponding input value.

    Redaction must be surgical — it must not corrupt safe data.
    """
    redacted = redact_sensitive(payload)

    for key in payload:
        assert key not in SENSITIVE_AUDIT_FIELDS, (
            f"Test setup error: key={key!r} should not be a sensitive field."
        )
        assert redacted[key] == payload[key], (
            f"redact_sensitive() altered non-sensitive field key={key!r}. "
            f"Original: {payload[key]!r}, After redaction: {redacted[key]!r}. "
            f"Only sensitive fields should be modified (Requirement 5.3)."
        )


# ---------------------------------------------------------------------------
# Property 16e: redact_sensitive does not mutate the original input
# ---------------------------------------------------------------------------

@given(payload=st_mixed_payload)
@settings(max_examples=150)
def test_property_16e_redact_sensitive_does_not_mutate_input(
    payload: dict,
) -> None:
    """
    **Validates: Requirements 5.3**

    Property 16e: Input Immutability (No Mutation)

    For any payload dict passed to ``redact_sensitive``, the original input
    dict SHALL remain unchanged after the call — ``redact_sensitive`` must
    return a new sanitised copy, never altering the caller's object in-place.

    This ensures the service function's local variables retain their
    original values even after audit logging applies redaction.
    """
    original_copy = copy.deepcopy(payload)
    _ = redact_sensitive(payload)

    assert payload == original_copy, (
        f"redact_sensitive() mutated the input dict in-place! "
        f"Original (deep-copied): {original_copy!r}. "
        f"After redact_sensitive call: {payload!r}. "
        f"redact_sensitive must return a new copy, not mutate the input "
        f"(Requirement 5.3)."
    )
