"""
authentication/views.py

View classes for the authentication subsystem:

  LoginView                    POST /api/v1/auth/login/
  LogoutView                   POST /api/v1/auth/logout/
  SessionView                  GET  /api/v1/auth/session/
  PasswordResetRequestView     POST /api/v1/auth/password-reset/
  PasswordResetConfirmView     POST /api/v1/auth/password-reset/confirm/
  TwoFactorSetupView           POST /api/v1/auth/2fa/setup/
  TwoFactorVerifyView          POST /api/v1/auth/2fa/verify/
  TwoFactorLoginView           POST /api/v1/auth/2fa/login/
  UserViewSet                  POST /api/v1/auth/users/   (staff-account creation)

Rate limiting (django-ratelimit, 10 req/60s per IP) is applied to:
  LoginView, PasswordResetRequestView, PasswordResetConfirmView,
  TwoFactorLoginView

All error responses use the platform's standard error envelope:
  {"error": {"code": "...", "message": "..."}}
"""

import logging

import pyotp
from django.contrib.auth import get_user_model, login, logout
from django.core.mail import send_mail
from django.conf import settings as django_settings
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.exceptions import ResourceLimitExceeded as _BillingLimitExceeded
from apps.billing.services import BillingService
from shared.permissions import IsSuperAdminOrTenantOwner

from .serializers import (
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    SignupSerializer,
    TwoFactorChallengeSerializer,
    TwoFactorDisableSerializer,
    TwoFactorLoginSerializer,
    TwoFactorVerifySerializer,
    UserDeactivateSerializer,
    UserReassignSerializer,
    UserSerializer,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Audit helper — defensive import so auth still works before Task 6 migrations
# ---------------------------------------------------------------------------

try:
    from apps.audit.models import AuditLog as _AuditLog
    from apps.audit.decorators import _request_context as _audit_ctx
    _AUDIT_AVAILABLE = True
except Exception:
    _AUDIT_AVAILABLE = False
    _AuditLog = None  # type: ignore
    _audit_ctx = None  # type: ignore


def _write_auth_audit(
    action: str,
    user=None,
    request=None,
    status_val: str = "success",
    failure_reason: str = "",
) -> None:
    """
    Write a single AuditLog entry for an authentication event.

    Silently no-ops if AuditLog is not available (before Task 6 migrations).
    Requirements: 5.1
    """
    if not _AUDIT_AVAILABLE:
        return
    try:
        user_id = user.pk if user is not None else None
        user_role = getattr(user, "role", "") if user is not None else ""

        # Prefer context from AuditLogMiddleware thread-local
        ip_address = getattr(_audit_ctx, "ip_address", None) if _audit_ctx else None
        user_agent = getattr(_audit_ctx, "user_agent", "") if _audit_ctx else ""
        tenant_id = getattr(_audit_ctx, "tenant_id", None) if _audit_ctx else None

        # Fallback to request if middleware hasn't run
        if ip_address is None and request is not None:
            xff = request.META.get("HTTP_X_FORWARDED_FOR")
            ip_address = xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")
        if not user_agent and request is not None:
            user_agent = request.META.get("HTTP_USER_AGENT", "")
        if tenant_id is None and request is not None:
            tenant = getattr(request, "tenant", None)
            tenant_id = str(tenant.pk) if tenant else None

        _AuditLog.objects.create(
            tenant_id=tenant_id,
            branch_id=None,
            user_id=user_id,
            user_role=user_role,
            ip_address=ip_address or "0.0.0.0",
            user_agent=user_agent or "",
            action=action,
            resource_type="User",
            resource_id=user_id,
            old_value=None,
            new_value=None,
            status=status_val,
            failure_reason=failure_reason,
        )
    except Exception as exc:
        logger.debug("_write_auth_audit failed (action=%s): %s", action, exc)


# ---------------------------------------------------------------------------
# Rate-limit constants
# ---------------------------------------------------------------------------

_RATE = "10/m"  # 10 requests per 60-second window per IP


# ---------------------------------------------------------------------------
# Helper: build the standard error response body
# ---------------------------------------------------------------------------

def _error_response(code: str, message: str, http_status: int) -> Response:
    return Response(
        {"error": {"code": code, "message": message}},
        status=http_status,
    )


# ---------------------------------------------------------------------------
# Rate-limit mixin
# ---------------------------------------------------------------------------

class RateLimitMixin:
    """
    Mixin that applies IP-level rate limiting via django-ratelimit and
    formats the 429 response with the platform's standard error envelope.
    """

    _rate = _RATE

    def dispatch(self, request, *args, **kwargs):
        from django_ratelimit.core import is_ratelimited, ALL as RATELIMIT_ALL
        from django.http import JsonResponse as DjJsonResponse

        is_limited = is_ratelimited(
            request=request,
            group=self.__class__.__name__,
            key="ip",
            rate=self._rate,
            method=RATELIMIT_ALL,
            increment=True,
        )

        if is_limited:
            return DjJsonResponse(
                {
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": "Too many requests. Try again later.",
                        "details": {},
                    }
                },
                status=429,
            )

        return super().dispatch(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Login / Logout / Session
# ---------------------------------------------------------------------------

class LoginView(RateLimitMixin, APIView):
    """
    POST /api/v1/auth/login/

    Responses:
        200  {"user_id", "email", "role"}  — success (no 2FA)
        200  {"requires_2fa": true}        — 2FA required
        401  INVALID_CREDENTIALS
        403  ACCOUNT_LOCKED
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})

        if not serializer.is_valid():
            errors = serializer.errors.get("non_field_errors", [])
            code = str(errors[0]) if errors else "INVALID_CREDENTIALS"

            if code == "ACCOUNT_LOCKED":
                _write_auth_audit(
                    "USER_LOGIN_FAILED",
                    request=request,
                    status_val="failure",
                    failure_reason="ACCOUNT_LOCKED",
                )
                return _error_response(
                    "ACCOUNT_LOCKED",
                    "This account is locked due to too many failed login attempts.",
                    status.HTTP_403_FORBIDDEN,
                )

            _write_auth_audit(
                "USER_LOGIN_FAILED",
                request=request,
                status_val="failure",
                failure_reason="INVALID_CREDENTIALS",
            )
            return _error_response(
                "INVALID_CREDENTIALS",
                "Invalid email or password.",
                status.HTTP_401_UNAUTHORIZED,
            )

        user = serializer.validated_data["user"]

        if user.totp_secret:
            import uuid
            from django.core.cache import cache

            partial_token = uuid.uuid4()
            cache.set(
                f"2fa_partial:{partial_token}",
                str(user.id),
                timeout=300,
            )
            return Response(
                {"requires_2fa": True, "partial_token": str(partial_token)},
                status=status.HTTP_202_ACCEPTED,
            )

        login(request, user)
        _write_auth_audit("USER_LOGIN", user=user, request=request)
        return Response(
            {"user_id": str(user.id), "email": user.email, "role": user.role},
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    """POST /api/v1/auth/logout/ — Terminate the current session."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        logout(request)
        _write_auth_audit("USER_LOGOUT", user=user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionView(APIView):
    """GET /api/v1/auth/session/ — Return info about the current user."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response(
            {
                "user_id": str(user.id),
                "email": user.email,
                "role": user.role,
                "branch_id": str(user.branch_id) if user.branch_id else None,
            },
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

class PasswordResetRequestView(RateLimitMixin, APIView):
    """POST /api/v1/auth/password-reset/ — Initiate a password reset."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        from apps.authentication.models import PasswordResetToken, User

        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"].lower().strip()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response(
                {"detail": "If that email is registered, a reset link has been sent."},
                status=status.HTTP_200_OK,
            )

        PasswordResetToken.objects.filter(user=user, is_used=False).update(is_used=True)
        reset_token = PasswordResetToken.objects.create(user=user)

        try:
            reset_url = (
                f"{self._get_base_url(request)}/reset-password?token={reset_token.token}"
            )
            send_mail(
                subject="Reset your password",
                message=(
                    f"Click the link below to reset your password.\n\n{reset_url}\n\n"
                    "This link expires in 1 hour. If you did not request this, ignore this email."
                ),
                from_email=getattr(django_settings, "DEFAULT_FROM_EMAIL", "noreply@platform.local"),
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception:
            logger.exception("Failed to send password-reset email to %s", email)

        return Response(
            {"detail": "If that email is registered, a reset link has been sent."},
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _get_base_url(request) -> str:
        scheme = "https" if request.is_secure() else "http"
        return f"{scheme}://{request.get_host()}"


class PasswordResetConfirmView(RateLimitMixin, APIView):
    """POST /api/v1/auth/password-reset/confirm/ — Set a new password via token."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        from apps.authentication.models import unlock_account

        serializer = PasswordResetConfirmSerializer(data=request.data)

        if not serializer.is_valid():
            token_errors = serializer.errors.get("token", [])
            code = "INVALID_TOKEN"
            message = "The reset token is invalid or has already been used."
            for err in token_errors:
                if "TOKEN_EXPIRED" in str(err):
                    code = "TOKEN_EXPIRED"
                    message = "The reset token has expired. Please request a new one."
                    break
            return _error_response(code, message, status.HTTP_400_BAD_REQUEST)

        reset_token = serializer.validated_data["reset_token"]
        new_password = serializer.validated_data["new_password"]
        user = reset_token.user

        user.set_password(new_password)
        user.save(update_fields=["password"])
        reset_token.is_used = True
        reset_token.save(update_fields=["is_used"])
        unlock_account(user)

        _write_auth_audit("PASSWORD_RESET", user=user, request=request)

        return Response({"detail": "Password reset successful."}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# TOTP two-factor authentication
# ---------------------------------------------------------------------------

class TwoFactorSetupView(APIView):
    """POST /api/v1/auth/2fa/setup/ — Generate and store a new TOTP secret."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        import io, base64
        import qrcode

        user = request.user
        secret = pyotp.random_base32()
        user.totp_secret = secret
        user.save(update_fields=["totp_secret"])
        totp = pyotp.TOTP(secret)
        otpauth_uri = totp.provisioning_uri(name=user.email, issuer_name="RestaurantPlatform")

        qr_img = qrcode.make(otpauth_uri)
        buf = io.BytesIO()
        qr_img.save(buf, format="PNG")
        qr_data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

        return Response({
            "secret": secret,
            "otpauth_uri": otpauth_uri,
            "qr_data_url": qr_data_url,
        }, status=status.HTTP_200_OK)


class TwoFactorVerifyView(APIView):
    """POST /api/v1/auth/2fa/verify/ — Verify TOTP code after setup."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TwoFactorVerifySerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return _error_response(
                "INVALID_TOTP_CODE",
                "The TOTP code is invalid or has expired.",
                status.HTTP_400_BAD_REQUEST,
            )
        return Response({"verified": True}, status=status.HTTP_200_OK)


class TwoFactorDisableView(APIView):
    """POST /api/v1/auth/2fa/disable/ — Disable 2FA after password confirmation."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TwoFactorDisableSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            return _error_response(
                "INVALID_PASSWORD",
                "Incorrect password.",
                status.HTTP_400_BAD_REQUEST,
            )
        user = request.user
        user.totp_secret = ""
        user.save(update_fields=["totp_secret"])
        logger.info("2FA disabled for user %s", user.email)
        return Response({"disabled": True}, status=status.HTTP_200_OK)


class TwoFactorChallengeView(RateLimitMixin, APIView):
    """
    POST /api/v1/auth/2fa/challenge/

    Completes 2FA using a cache-backed partial_token (obtained from login).
    On success logs the user in and sets the session cookie.

    Responses:
        200  {"user_id", "email", "role"}  — login complete
        400  INVALID_TOKEN                 — token missing/expired
        400  INVALID_TOTP_CODE             — bad TOTP code
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = TwoFactorChallengeSerializer(
            data=request.data, context={"request": request}
        )

        if not serializer.is_valid():
            errors = serializer.errors.get("non_field_errors", [])
            code = str(errors[0]) if errors else "INVALID_TOTP_CODE"
            message = "The TOTP code is invalid or has expired."
            if code == "INVALID_TOKEN":
                message = "Invalid or expired token. Please log in again."
            return _error_response(code, message, status.HTTP_400_BAD_REQUEST)

        user = serializer.validated_data["user"]
        cache_key = serializer.validated_data["_cache_key"]

        from django.core.cache import cache
        cache.delete(cache_key)

        login(request, user)
        _write_auth_audit("USER_LOGIN", user=user, request=request)
        return Response(
            {"user_id": str(user.id), "email": user.email, "role": user.role},
            status=status.HTTP_200_OK,
        )


class TwoFactorLoginView(RateLimitMixin, APIView):
    """POST /api/v1/auth/2fa/login/ — Complete 2FA login with TOTP code."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = TwoFactorLoginSerializer(data=request.data, context={"request": request})

        if not serializer.is_valid():
            errors = serializer.errors.get("non_field_errors", [])
            code = str(errors[0]) if errors else "INVALID_TOTP_CODE"
            message = "The TOTP code is invalid or has expired."
            if code == "NO_PENDING_2FA":
                message = "No pending 2FA session found. Please log in first."
            return _error_response(code, message, status.HTTP_400_BAD_REQUEST)

        user = serializer.validated_data["user"]
        del request.session["pending_2fa_user_id"]
        login(request, user)
        _write_auth_audit("USER_LOGIN", user=user, request=request)
        return Response(
            {"user_id": str(user.id), "email": user.email, "role": user.role},
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Signup (self-registration)
# ---------------------------------------------------------------------------

class SignupView(RateLimitMixin, APIView):
    """
    POST /api/v1/auth/register/ — Create a new staff user account.

    Security model (Requirement 3.x):
      * Authenticated session required — caller must be Branch_Manager,
        Tenant_Owner, or Super_Admin (Receptionist/Kitchen_Staff denied).
      * Role assigned to created user is restricted to
        Branch_Manager / Receptionist / Kitchen_Staff (no Super_Admin).
      * Tenant resolved from middleware (request.tenant), NOT from request body.
      * Resource limit (plan.max_staff) enforced before creation.
      * Password is NOT set — invite email with set-password link is sent.
      * Audit log entry (USER_CREATED) written on success.
      * Rate-limited to 10 requests/min.
    """

    permission_classes = [IsAuthenticated]

    # Roles allowed to create staff accounts
    _ALLOWED_CREATOR_ROLES = frozenset({
        "Branch_Manager", "Tenant_Owner", "Super_Admin",
    })

    def post(self, request):
        if request.user.role not in self._ALLOWED_CREATOR_ROLES:
            return _error_response(
                "FORBIDDEN",
                "You do not have permission to create staff accounts.",
                status.HTTP_403_FORBIDDEN,
            )

        tenant = getattr(request, "tenant", None)

        # 1. Resource-limit check
        if tenant is not None:
            try:
                BillingService.check_resource_limit(tenant, "staff_accounts")
            except _BillingLimitExceeded as exc:
                raise ValidationError(
                    {
                        "detail": str(exc),
                        "resource_type": exc.resource_type,
                        "current_count": exc.current_count,
                        "limit": exc.limit,
                    }
                ) from exc

        # 2. Validate & create user (no password — unusable)
        serializer = SignupSerializer(
            data=request.data,
            tenant=tenant,
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # 3. Generate invite token & send email
        invite_token = self._send_invite(request, user)
        logger.info(
            "Staff account created: %s (role=%s, tenant=%s)",
            user.email, user.role, tenant,
        )

        # 4. Audit log
        _write_auth_audit("USER_CREATED", user=user, request=request)

        return Response(
            {
                "user_id": str(user.id),
                "email": user.email,
                "role": user.role,
                "invite_sent": invite_token is not None,
                "message": (
                    "Invite email sent. The user must set their password "
                    "before logging in."
                ),
            },
            status=status.HTTP_201_CREATED,
        )

    @staticmethod
    def _send_invite(request, user) -> str | None:
        """Generate a PasswordResetToken and send an invite email."""
        from apps.authentication.models import PasswordResetToken
        from django.core.mail import send_mail
        from django.conf import settings as django_settings

        try:
            PasswordResetToken.objects.filter(user=user, is_used=False).update(
                is_used=True
            )
            reset_token = PasswordResetToken.objects.create(user=user)

            scheme = "https" if request.is_secure() else "http"
            base_url = f"{scheme}://{request.get_host()}"
            set_url = (
                f"{base_url}/reset-password?token={reset_token.token}"
            )

            send_mail(
                subject="Set your password — Restaurant Platform",
                message=(
                    f"Hi {user.email},\n\n"
                    f"A staff account has been created for you.\n\n"
                    f"Click the link below to set your password:\n{set_url}\n\n"
                    f"This link expires in 1 hour.\n"
                    f"If you did not expect this, please ignore this email."
                ),
                from_email=getattr(
                    django_settings, "DEFAULT_FROM_EMAIL", "noreply@platform.local"
                ),
                recipient_list=[user.email],
                fail_silently=False,
            )
            return str(reset_token.token)
        except Exception:
            logger.exception(
                "Failed to send invite email to %s", user.email
            )
            return None


# ---------------------------------------------------------------------------
# User management (staff-account creation)
# ---------------------------------------------------------------------------

class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet for staff User management.

    Endpoints:
      GET    /api/v1/auth/users/          — list staff (tenant-scoped)
      POST   /api/v1/auth/users/          — create staff (with invite)
      PATCH  /api/v1/auth/users/{id}/     — update role / branch
      POST   /api/v1/auth/users/{id}/deactivate/  — soft-deactivate staff
      POST   /api/v1/auth/users/{id}/reassign/    — reassign branch

    Billing enforcement is applied on create.
    """

    serializer_class = UserSerializer
    permission_classes = [IsSuperAdminOrTenantOwner]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        """
        Return all User instances visible to the current tenant.

        In production (django-tenants), users live in tenant-specific schemas
        so the queryset is already scoped.  In test/fresh environments where
        tenant middleware isn't active, return all users.
        """
        return get_user_model().objects.all()

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if tenant is not None:
            try:
                BillingService.check_resource_limit(tenant, "staff_accounts")
            except _BillingLimitExceeded as exc:
                raise ValidationError(
                    {
                        "detail": str(exc),
                        "resource_type": exc.resource_type,
                        "current_count": exc.current_count,
                        "limit": exc.limit,
                    }
                ) from exc
        serializer.save()

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        """
        POST /api/v1/auth/users/{id}/deactivate/
        Soft-deactivate a staff user (is_active = False).
        """
        user = self.get_object()
        serializer = UserDeactivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user.is_active = False
        user.save(update_fields=["is_active"])
        _write_auth_audit(
            "USER_DEACTIVATED", user=user, request=request,
            status_val="success",
        )
        return Response(
            {"detail": f"User {user.email} deactivated."},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def reassign(self, request, pk=None):
        """
        POST /api/v1/auth/users/{id}/reassign/
        Reassign a staff user to a different branch.
        """
        user = self.get_object()
        serializer = UserReassignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_branch_id = str(user.branch_id) if user.branch_id else None
        user.branch_id = serializer.validated_data["branch_id"]
        user.save(update_fields=["branch_id"])
        _write_auth_audit(
            "USER_REASSIGNED", user=user, request=request,
            status_val="success",
        )
        return Response(
            {
                "detail": f"User {user.email} reassigned.",
                "branch_id": str(user.branch_id),
            },
            status=status.HTTP_200_OK,
        )
