"""
tests/test_authentication.py

Unit tests for the authentication system covering:
  - User model creation and lockout logic
  - PasswordResetToken model helpers
  - LoginView: success, invalid credentials, account lockout
  - LogoutView
  - SessionView
  - PasswordResetRequestView / PasswordResetConfirmView
  - TwoFactorSetupView / TwoFactorVerifyView / TwoFactorLoginView
  - RateLimitMixin (via LoginView)

Tests run against SQLite in-memory via config.settings.testing.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pyotp
import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def make_user(db):
    """Factory for creating test users."""
    def _make(email="user@example.com", password="StrongPass123!", role="Receptionist", **kw):
        return User.objects.create_user(email=email, password=password, role=role, **kw)
    return _make


# ---------------------------------------------------------------------------
# User model tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUserModel:
    def test_create_user_sets_email_and_role(self, make_user):
        user = make_user()
        assert user.email == "user@example.com"
        assert user.role == "Receptionist"

    def test_create_user_hashes_password(self, make_user):
        user = make_user(password="plaintextpass")
        assert user.password != "plaintextpass"
        assert user.check_password("plaintextpass")

    def test_create_superuser_sets_flags(self, db):
        user = User.objects.create_superuser(
            email="admin@example.com", password="AdminPass123!"
        )
        assert user.is_staff is True
        assert user.is_superuser is True
        assert user.role == "Super_Admin"

    def test_create_user_requires_email(self, db):
        with pytest.raises(ValueError, match="Email"):
            User.objects.create_user(email="", password="pass", role="Receptionist")

    def test_create_user_requires_role(self, db):
        with pytest.raises(ValueError, match="Role"):
            User.objects.create_user(email="x@x.com", password="pass", role="")

    def test_is_locked_false_initially(self, make_user):
        user = make_user()
        assert user.is_locked is False

    def test_record_failed_login_increments_counter(self, make_user):
        user = make_user()
        user.record_failed_login()
        user.refresh_from_db()
        assert user.failed_login_count == 1
        assert user.locked_at is None

    def test_account_locked_after_5_failures(self, make_user):
        user = make_user()
        for _ in range(5):
            user.record_failed_login()
        user.refresh_from_db()
        assert user.failed_login_count == 5
        assert user.locked_at is not None
        assert user.is_locked is True

    def test_reset_login_attempts_clears_lockout(self, make_user):
        user = make_user()
        for _ in range(5):
            user.record_failed_login()
        user.reset_login_attempts()
        user.refresh_from_db()
        assert user.failed_login_count == 0
        assert user.locked_at is None
        assert user.is_locked is False


# ---------------------------------------------------------------------------
# PasswordResetToken tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPasswordResetToken:
    def test_token_not_expired_when_fresh(self, make_user):
        from apps.authentication.models import PasswordResetToken
        user = make_user()
        token = PasswordResetToken.objects.create(user=user)
        assert token.is_expired is False

    def test_token_expired_after_1_hour(self, make_user):
        from apps.authentication.models import PasswordResetToken
        user = make_user()
        token = PasswordResetToken.objects.create(user=user)
        # Backdate created_at by 61 minutes
        PasswordResetToken.objects.filter(pk=token.pk).update(
            created_at=timezone.now() - timedelta(minutes=61)
        )
        token.refresh_from_db()
        assert token.is_expired is True

    def test_token_uuid_unique(self, make_user):
        from apps.authentication.models import PasswordResetToken
        user = make_user()
        t1 = PasswordResetToken.objects.create(user=user)
        t2 = PasswordResetToken.objects.create(user=user)
        assert t1.token != t2.token


# ---------------------------------------------------------------------------
# unlock_account helper
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_unlock_account_helper(make_user):
    from apps.authentication.models import unlock_account
    user = make_user()
    for _ in range(5):
        user.record_failed_login()
    assert user.is_locked
    unlock_account(user)
    user.refresh_from_db()
    assert not user.is_locked
    assert user.failed_login_count == 0


# ---------------------------------------------------------------------------
# LoginView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLoginView:
    url = "/api/v1/auth/login/"

    def test_successful_login(self, api_client, make_user):
        make_user(email="login@example.com", password="Pass1234!")
        resp = api_client.post(
            self.url, {"email": "login@example.com", "password": "Pass1234!"}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["email"] == "login@example.com"
        assert "user_id" in resp.data
        assert "role" in resp.data

    def test_invalid_password_returns_401(self, api_client, make_user):
        make_user(email="login@example.com", password="Pass1234!")
        resp = api_client.post(
            self.url, {"email": "login@example.com", "password": "WrongPass!"}, format="json"
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED
        assert resp.data["error"]["code"] == "INVALID_CREDENTIALS"

    def test_nonexistent_email_returns_401(self, api_client, db):
        resp = api_client.post(
            self.url, {"email": "ghost@example.com", "password": "Pass1234!"}, format="json"
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_locked_account_returns_403(self, api_client, make_user):
        user = make_user(email="locked@example.com", password="Pass1234!")
        for _ in range(5):
            user.record_failed_login()
        resp = api_client.post(
            self.url, {"email": "locked@example.com", "password": "Pass1234!"}, format="json"
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert resp.data["error"]["code"] == "ACCOUNT_LOCKED"

    def test_failed_login_increments_counter(self, api_client, make_user):
        user = make_user(email="cnt@example.com", password="Pass1234!")
        api_client.post(
            self.url, {"email": "cnt@example.com", "password": "WRONG"}, format="json"
        )
        user.refresh_from_db()
        assert user.failed_login_count == 1

    def test_successful_login_resets_failure_count(self, api_client, make_user):
        user = make_user(email="reset@example.com", password="Pass1234!")
        user.failed_login_count = 3
        user.save(update_fields=["failed_login_count"])
        api_client.post(
            self.url, {"email": "reset@example.com", "password": "Pass1234!"}, format="json"
        )
        user.refresh_from_db()
        assert user.failed_login_count == 0

    def test_login_with_totp_returns_requires_2fa(self, api_client, make_user):
        user = make_user(email="totp@example.com", password="Pass1234!")
        user.totp_secret = pyotp.random_base32()
        user.save(update_fields=["totp_secret"])
        resp = api_client.post(
            self.url, {"email": "totp@example.com", "password": "Pass1234!"}, format="json"
        )
        assert resp.status_code == status.HTTP_202_ACCEPTED
        assert resp.data.get("requires_2fa") is True
        assert "partial_token" in resp.data
        assert "user_id" not in resp.data


# ---------------------------------------------------------------------------
# LogoutView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLogoutView:
    url = "/api/v1/auth/logout/"

    def test_logout_returns_204(self, api_client, make_user):
        user = make_user()
        api_client.force_authenticate(user=user)
        resp = api_client.post(self.url)
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_logout_requires_authentication(self, api_client, db):
        resp = api_client.post(self.url)
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# SessionView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSessionView:
    url = "/api/v1/auth/session/"

    def test_session_returns_user_info(self, api_client, make_user):
        user = make_user()
        api_client.force_authenticate(user=user)
        resp = api_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["email"] == user.email
        assert resp.data["role"] == user.role

    def test_session_requires_auth(self, api_client, db):
        resp = api_client.get(self.url)
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# PasswordResetRequestView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPasswordResetRequestView:
    url = "/api/v1/auth/password-reset/"

    def test_always_returns_200_for_unknown_email(self, api_client, db):
        resp = api_client.post(
            self.url, {"email": "nobody@example.com"}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_creates_token_for_known_email(self, api_client, make_user):
        from apps.authentication.models import PasswordResetToken
        make_user(email="known@example.com")
        with patch("apps.authentication.views.send_mail"):
            resp = api_client.post(
                self.url, {"email": "known@example.com"}, format="json"
            )
        assert resp.status_code == status.HTTP_200_OK
        assert PasswordResetToken.objects.filter(
            user__email="known@example.com", is_used=False
        ).exists()

    def test_invalidates_prior_tokens(self, api_client, make_user):
        from apps.authentication.models import PasswordResetToken
        user = make_user(email="known@example.com")
        old_token = PasswordResetToken.objects.create(user=user)
        with patch("apps.authentication.views.send_mail"):
            api_client.post(
                self.url, {"email": "known@example.com"}, format="json"
            )
        old_token.refresh_from_db()
        assert old_token.is_used is True

    def test_email_send_failure_still_returns_200(self, api_client, make_user):
        make_user(email="mail@example.com")
        with patch(
            "apps.authentication.views.send_mail",
            side_effect=Exception("SMTP error"),
        ):
            resp = api_client.post(
                self.url, {"email": "mail@example.com"}, format="json"
            )
        assert resp.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# PasswordResetConfirmView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPasswordResetConfirmView:
    url = "/api/v1/auth/password-reset/confirm/"

    def _create_token(self, user):
        from apps.authentication.models import PasswordResetToken
        return PasswordResetToken.objects.create(user=user)

    def test_valid_token_resets_password(self, api_client, make_user):
        user = make_user(email="rp@example.com", password="OldPass123!")
        token = self._create_token(user)
        resp = api_client.post(
            self.url,
            {"token": str(token.token), "new_password": "NewPass456!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.check_password("NewPass456!")

    def test_valid_token_marks_as_used(self, api_client, make_user):
        user = make_user(email="rp@example.com")
        token = self._create_token(user)
        api_client.post(
            self.url,
            {"token": str(token.token), "new_password": "NewPass456!"},
            format="json",
        )
        token.refresh_from_db()
        assert token.is_used is True

    def test_valid_token_clears_lockout(self, api_client, make_user):
        user = make_user(email="rp@example.com")
        for _ in range(5):
            user.record_failed_login()
        token = self._create_token(user)
        api_client.post(
            self.url,
            {"token": str(token.token), "new_password": "NewPass456!"},
            format="json",
        )
        user.refresh_from_db()
        assert not user.is_locked
        assert user.failed_login_count == 0

    def test_invalid_token_returns_400(self, api_client, db):
        resp = api_client.post(
            self.url,
            {"token": str(uuid.uuid4()), "new_password": "NewPass456!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data["error"]["code"] == "INVALID_TOKEN"

    def test_used_token_returns_400(self, api_client, make_user):
        from apps.authentication.models import PasswordResetToken
        user = make_user(email="rp@example.com")
        token = PasswordResetToken.objects.create(user=user, is_used=True)
        resp = api_client.post(
            self.url,
            {"token": str(token.token), "new_password": "NewPass456!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_expired_token_returns_400_with_TOKEN_EXPIRED(self, api_client, make_user):
        from apps.authentication.models import PasswordResetToken
        user = make_user(email="rp@example.com")
        token = PasswordResetToken.objects.create(user=user)
        PasswordResetToken.objects.filter(pk=token.pk).update(
            created_at=timezone.now() - timedelta(minutes=65)
        )
        resp = api_client.post(
            self.url,
            {"token": str(token.token), "new_password": "NewPass456!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data["error"]["code"] == "TOKEN_EXPIRED"


# ---------------------------------------------------------------------------
# TwoFactorSetupView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTwoFactorSetupView:
    url = "/api/v1/auth/2fa/setup/"

    def test_setup_returns_secret_and_uri(self, api_client, make_user):
        user = make_user()
        api_client.force_authenticate(user=user)
        resp = api_client.post(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert "secret" in resp.data
        assert "otpauth_uri" in resp.data
        assert "RestaurantPlatform" in resp.data["otpauth_uri"]

    def test_setup_saves_secret_on_user(self, api_client, make_user):
        user = make_user()
        api_client.force_authenticate(user=user)
        api_client.post(self.url)
        user.refresh_from_db()
        assert user.totp_secret != ""


# ---------------------------------------------------------------------------
# TwoFactorVerifyView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTwoFactorVerifyView:
    url = "/api/v1/auth/2fa/verify/"

    def test_valid_code_returns_verified(self, api_client, make_user):
        user = make_user()
        secret = pyotp.random_base32()
        user.totp_secret = secret
        user.save(update_fields=["totp_secret"])
        api_client.force_authenticate(user=user)
        code = pyotp.TOTP(secret).now()
        resp = api_client.post(self.url, {"code": code}, format="json")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["verified"] is True

    def test_invalid_code_returns_400(self, api_client, make_user):
        user = make_user()
        user.totp_secret = pyotp.random_base32()
        user.save(update_fields=["totp_secret"])
        api_client.force_authenticate(user=user)
        resp = api_client.post(self.url, {"code": "000000"}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data["error"]["code"] == "INVALID_TOTP_CODE"


# ---------------------------------------------------------------------------
# TwoFactorLoginView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTwoFactorLoginView:
    login_url = "/api/v1/auth/login/"
    two_fa_url = "/api/v1/auth/2fa/login/"

    def _login_and_get_pending_client(self, make_user):
        """Perform the first factor and return the client with pending_2fa session."""
        client = APIClient()
        secret = pyotp.random_base32()
        user = make_user(email="2fa@example.com", password="Pass1234!")
        user.totp_secret = secret
        user.save(update_fields=["totp_secret"])
        client.post(
            self.login_url,
            {"email": "2fa@example.com", "password": "Pass1234!"},
            format="json",
        )
        return client, user, secret

    def test_valid_totp_code_completes_login(self, make_user, db):
        client, user, secret = self._login_and_get_pending_client(make_user)
        code = pyotp.TOTP(secret).now()
        resp = client.post(self.two_fa_url, {"code": code}, format="json")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["email"] == "2fa@example.com"

    def test_invalid_code_returns_400(self, make_user, db):
        client, user, secret = self._login_and_get_pending_client(make_user)
        resp = client.post(self.two_fa_url, {"code": "000000"}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data["error"]["code"] == "INVALID_TOTP_CODE"

    def test_no_pending_session_returns_400(self, api_client, db):
        resp = api_client.post(self.two_fa_url, {"code": "123456"}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# TwoFactorChallengeView (cache-based)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTwoFactorChallengeView:
    login_url = "/api/v1/auth/login/"
    challenge_url = "/api/v1/auth/2fa/challenge/"

    def _do_first_factor(self, make_user, email="2fa@example.com", password="Pass1234!"):
        """Return (client, user, secret, partial_token) after first-factor login."""
        import pyotp
        secret = pyotp.random_base32()
        user = make_user(email=email, password=password)
        user.totp_secret = secret
        user.save(update_fields=["totp_secret"])

        client = APIClient()
        resp = client.post(
            self.login_url,
            {"email": email, "password": password},
            format="json",
        )
        assert resp.status_code == status.HTTP_202_ACCEPTED
        assert resp.data["requires_2fa"] is True
        partial_token = resp.data["partial_token"]
        return client, user, secret, partial_token

    def test_valid_totp_completes_login(self, make_user, db):
        client, user, secret, partial_token = self._do_first_factor(make_user)
        code = pyotp.TOTP(secret).now()
        resp = client.post(
            self.challenge_url,
            {"partial_token": partial_token, "totp_code": code},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["email"] == "2fa@example.com"
        assert resp.data["role"] == user.role

    def test_challenge_establishes_session(self, make_user, db):
        """After challenge, the client can access authenticated endpoints."""
        client, user, secret, partial_token = self._do_first_factor(make_user)
        code = pyotp.TOTP(secret).now()
        client.post(
            self.challenge_url,
            {"partial_token": partial_token, "totp_code": code},
            format="json",
        )
        resp = client.get("/api/v1/auth/session/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["email"] == "2fa@example.com"

    def test_invalid_totp_code_returns_400(self, make_user, db):
        client, user, secret, partial_token = self._do_first_factor(make_user)
        resp = client.post(
            self.challenge_url,
            {"partial_token": partial_token, "totp_code": "000000"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data["error"]["code"] == "INVALID_TOTP_CODE"

    def test_invalid_partial_token_returns_400(self, api_client, db):
        resp = api_client.post(
            self.challenge_url,
            {"partial_token": str(uuid.uuid4()), "totp_code": "123456"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data["error"]["code"] == "INVALID_TOKEN"

    def test_token_consumed_after_use(self, make_user, db):
        """A partial_token cannot be used twice."""
        client, user, secret, partial_token = self._do_first_factor(make_user)
        code = pyotp.TOTP(secret).now()
        resp = client.post(
            self.challenge_url,
            {"partial_token": partial_token, "totp_code": code},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        # Second attempt should fail
        resp2 = client.post(
            self.challenge_url,
            {"partial_token": partial_token, "totp_code": code},
            format="json",
        )
        assert resp2.status_code == status.HTTP_400_BAD_REQUEST
        assert resp2.data["error"]["code"] == "INVALID_TOKEN"
