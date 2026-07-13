"""
Property-Based Tests: Password Hashing Non-Reversibility

Property 6: For any plaintext password string, the stored hash shall:
  (a) not equal the plaintext string,
  (b) verify correctly with argon2.verify(hash, plaintext), and
  (c) differ from hashing the same plaintext a second time (per-hash salting).

Validates: Requirements 3.2

These tests exercise argon2-cffi's PasswordHasher directly — the same
underlying library used by Django's Argon2PasswordHasher backend.  This
approach keeps the tests fast and infrastructure-free (no database needed)
while proving the hashing contract that Requirement 3.2 demands.
"""

import pytest
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Shared PasswordHasher instance
# ---------------------------------------------------------------------------
# Use reduced parameters for faster test execution while still exercising the
# full Argon2id code path.  The default time_cost=2 is sufficient for PBT.
ph = PasswordHasher()


# ---------------------------------------------------------------------------
# Property 6a — Hash ≠ Plaintext
# ---------------------------------------------------------------------------

@given(st.text(min_size=1, max_size=100))
@settings(max_examples=200, deadline=None)
def test_property_6a_hash_does_not_equal_plaintext(password: str):
    """
    **Validates: Requirements 3.2**

    For any non-empty password string, the Argon2id hash produced by
    PasswordHasher.hash() must not equal the plaintext string.
    """
    hashed = ph.hash(password)
    assert hashed != password, (
        f"Hash must not equal plaintext. "
        f"Got hash={hashed!r} for password={password!r}"
    )


# ---------------------------------------------------------------------------
# Property 6b — Hash Verifies Correctly
# ---------------------------------------------------------------------------

@given(st.text(min_size=1, max_size=100))
@settings(max_examples=200, deadline=None)
def test_property_6b_hash_verifies_correctly(password: str):
    """
    **Validates: Requirements 3.2**

    For any non-empty password string, argon2.PasswordHasher.verify(hash, plaintext)
    must return True when the hash was produced from that plaintext.
    """
    hashed = ph.hash(password)
    # verify() returns True on success; raises VerifyMismatchError on failure
    result = ph.verify(hashed, password)
    assert result is True, (
        f"Verification failed for password={password!r}"
    )


@given(st.text(min_size=1, max_size=100), st.text(min_size=1, max_size=100))
@settings(max_examples=100, deadline=None)
def test_property_6b_wrong_password_does_not_verify(password: str, wrong_password: str):
    """
    **Validates: Requirements 3.2**

    A hash produced for one password must NOT verify successfully against a
    different password.  This confirms the verification function is not trivially
    accepting all inputs.
    """
    # Only test when the two passwords are distinct
    if password == wrong_password:
        return

    hashed = ph.hash(password)
    verified = False
    try:
        ph.verify(hashed, wrong_password)
        verified = True
    except VerifyMismatchError:
        verified = False

    assert not verified, (
        f"Hash for {password!r} must not verify with wrong password {wrong_password!r}"
    )


# ---------------------------------------------------------------------------
# Property 6c — Two Hashes of the Same Input Differ (per-hash salting)
# ---------------------------------------------------------------------------

@given(st.text(min_size=1, max_size=100))
@settings(max_examples=200, deadline=None)
def test_property_6c_same_password_produces_different_hashes(password: str):
    """
    **Validates: Requirements 3.2**

    Hashing the same plaintext password twice must produce two distinct hash
    strings.  This demonstrates that Argon2id uses a unique random salt for
    each hash operation, making pre-computation attacks infeasible.
    """
    hash1 = ph.hash(password)
    hash2 = ph.hash(password)
    assert hash1 != hash2, (
        f"Two hashes of the same password must differ (salting). "
        f"Got identical hashes for password={password!r}: {hash1!r}"
    )
