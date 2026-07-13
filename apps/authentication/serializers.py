"""
authentication/serializers.py

Serializers for the authentication flow:

  LoginSerializer              — validate email + password, enforce lockout
  PasswordResetRequestSerializer — validate email (no enumeration leak)
  PasswordResetConfirmSerializer — validate token + new_password
  TwoFactorVerifySerializer      — validate TOTP code against stored secret
  TwoFactorLoginSerializer       — validate TOTP code for pending-2FA session
"""

from django.contrib.auth import authenticate, get_user_model
from rest_framework import serializers


class LoginSerializer(serializers.Serializer):
    """
    Validates email + password credentials.

    Lockout check runs BEFORE password verification so that a locked account
    cannot be probed (Requirement 3.3).

    On success the validated_data['user'] contains the authenticated User.
    On failure raises ValidationError with code INVALID_CREDENTIALS or
    ACCOUNT_LOCKED so callers can return the right HTTP status code.
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        from apps.authentication.models import User

        email = attrs.get("email", "").lower().strip()
        password = attrs.get("password", "")

        # Look up user — use a try/except rather than filter() to keep the
        # code path constant regardless of whether the user exists.
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {"non_field_errors": "INVALID_CREDENTIALS"},
                code="INVALID_CREDENTIALS",
            )

        # Lockout check — must happen before authenticate() (Requirement 3.3)
        if user.is_locked:
            raise serializers.ValidationError(
                {"non_field_errors": "ACCOUNT_LOCKED"},
                code="ACCOUNT_LOCKED",
            )

        # Password verification via Django's authenticate()
        request = self.context.get("request")
        authenticated = authenticate(request=request, username=email, password=password)

        if not authenticated:
            # Increment failure counter; may lock the account
            user.record_failed_login()
            raise serializers.ValidationError(
                {"non_field_errors": "INVALID_CREDENTIALS"},
                code="INVALID_CREDENTIALS",
            )

        if not authenticated.is_active:
            raise serializers.ValidationError(
                {"non_field_errors": "ACCOUNT_LOCKED"},
                code="ACCOUNT_LOCKED",
            )

        # Success — reset failure counter
        authenticated.reset_login_attempts()
        attrs["user"] = authenticated
        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    """
    Accepts an email address for the password-reset request endpoint.
    We intentionally do not validate whether the user exists here; callers
    always return 200 to prevent user enumeration (Requirement 3.4).
    """

    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    Validates the reset token and new password.

    Error codes:
      INVALID_TOKEN  — token does not exist or is already used
      TOKEN_EXPIRED  — token exists but was issued more than 1 hour ago
    """

    token = serializers.UUIDField()
    new_password = serializers.CharField(
        write_only=True,
        min_length=8,
        trim_whitespace=False,
    )

    def validate_token(self, value):
        from apps.authentication.models import PasswordResetToken

        try:
            reset_token = PasswordResetToken.objects.select_related("user").get(
                token=value, is_used=False
            )
        except PasswordResetToken.DoesNotExist:
            raise serializers.ValidationError("INVALID_TOKEN", code="INVALID_TOKEN")

        if reset_token.is_expired:
            raise serializers.ValidationError("TOKEN_EXPIRED", code="TOKEN_EXPIRED")

        self._reset_token = reset_token
        return value

    def validate(self, attrs):
        # Attach the token object for use in the view
        attrs["reset_token"] = self._reset_token
        return attrs


class TwoFactorVerifySerializer(serializers.Serializer):
    """
    Validates a TOTP code against the current user's stored secret.
    Used by TwoFactorVerifyView (IsAuthenticated) for setup confirmation.
    """

    code = serializers.CharField(min_length=6, max_length=6)

    def validate_code(self, value):
        import pyotp

        user = self.context["request"].user
        if not user.totp_secret:
            raise serializers.ValidationError(
                "2FA is not configured for this account.", code="TOTP_NOT_CONFIGURED"
            )

        totp = pyotp.TOTP(user.totp_secret)
        if not totp.verify(value):
            raise serializers.ValidationError("INVALID_TOTP_CODE", code="INVALID_TOTP_CODE")

        return value


class TwoFactorChallengeSerializer(serializers.Serializer):
    """
    Validates a partial_token + TOTP code for the cache-based challenge flow.
    """

    partial_token = serializers.UUIDField()
    totp_code = serializers.CharField(min_length=6, max_length=6)

    def validate(self, attrs):
        import pyotp
        from django.core.cache import cache
        from apps.authentication.models import User

        cache_key = f"2fa_partial:{attrs['partial_token']}"
        user_id = cache.get(cache_key)

        if not user_id:
            raise serializers.ValidationError(
                {"non_field_errors": "INVALID_TOKEN"},
                code="INVALID_TOKEN",
            )

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {"non_field_errors": "INVALID_TOKEN"},
                code="INVALID_TOKEN",
            )

        totp = pyotp.TOTP(user.totp_secret)
        if not totp.verify(attrs["totp_code"]):
            raise serializers.ValidationError(
                {"non_field_errors": "INVALID_TOTP_CODE"},
                code="INVALID_TOTP_CODE",
            )

        attrs["user"] = user
        attrs["_cache_key"] = cache_key
        return attrs


class SignupSerializer(serializers.Serializer):
    """
    Validates and creates a new staff user account.

    Security model:
      * Only Branch_Manager, Receptionist, or Kitchen_Staff may be assigned
        (Super_Admin and Tenant_Owner are excluded — caller code must set those).
      * Email uniqueness is scoped to the tenant when a tenant context is
        available; otherwise falls back to global uniqueness.
      * Password is NOT set here — an invite email with a set-password link is
        generated after creation.
    """

    email = serializers.EmailField()
    role = serializers.ChoiceField(
        choices=[
            ("Branch_Manager", "Branch Manager"),
            ("Receptionist", "Receptionist"),
            ("Kitchen_Staff", "Kitchen Staff"),
        ]
    )
    branch_id = serializers.UUIDField(required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        self._tenant = kwargs.pop("tenant", None)
        self._requesting_user = kwargs.pop("requesting_user", None)
        super().__init__(*args, **kwargs)

    def validate_role(self, value):
        if value in ("Super_Admin", "Tenant_Owner"):
            raise serializers.ValidationError(
                "Cannot create users with Super_Admin or Tenant_Owner role.",
                code="ROLE_FORBIDDEN",
            )
        return value

    def validate_email(self, value):
        from apps.authentication.models import User

        value = value.lower().strip()
        qs = User.objects.all()
        if self._tenant is not None:
            qs = qs.filter(tenant=self._tenant)
        if qs.filter(email=value).exists():
            raise serializers.ValidationError(
                "A user with this email already exists.",
                code="EMAIL_EXISTS",
            )
        return value

    def create(self, validated_data):
        from apps.authentication.models import User

        branch_id = validated_data.pop("branch_id", None)
        user = User(
            email=validated_data["email"],
            role=validated_data["role"],
        )
        user.set_unusable_password()
        user.save()
        if branch_id and self._tenant is not None:
            user.branch_id = branch_id
            user.save(update_fields=["branch_id"])
        return user


class UserSerializer(serializers.ModelSerializer):
    """Serializer for reading and updating staff User records.

    The ``email`` field is read-only on updates (can only be set at creation).
    """

    branch_id = serializers.UUIDField(allow_null=True, required=False)

    class Meta:
        model = get_user_model()
        fields = [
            "id", "email", "role", "is_active", "branch_id",
            "created_at", "last_login",
        ]
        read_only_fields = ["id", "created_at", "last_login"]

    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        if self.instance is not None:
            extra_kwargs.setdefault("email", {})["read_only"] = True
        return extra_kwargs

    def create(self, validated_data):
        user_model = self.Meta.model
        user = user_model(
            email=user_model.objects.normalize_email(
                validated_data.pop("email")
            ),
            role=validated_data.pop("role", None),
            **validated_data,
        )
        user.set_unusable_password()
        user.save()
        return user


class UserDeactivateSerializer(serializers.Serializer):
    """Validates deactivation of a staff user."""

    reason = serializers.CharField(required=False, allow_blank=True)


class UserReassignSerializer(serializers.Serializer):
    """Validates reassignment of a user to a different branch."""

    branch_id = serializers.UUIDField()


class TwoFactorDisableSerializer(serializers.Serializer):
    """Validates the user's password before disabling 2FA."""

    password = serializers.CharField(required=True)

    def validate_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Incorrect password.", code="INCORRECT_PASSWORD")
        return value


class TwoFactorLoginSerializer(serializers.Serializer):
    """
    Validates a TOTP code for the pending-2FA login flow.
    Reads the pending user from the session key 'pending_2fa_user_id'.
    """

    code = serializers.CharField(min_length=6, max_length=6)

    def validate(self, attrs):
        import pyotp
        from apps.authentication.models import User

        request = self.context["request"]
        pending_user_id = request.session.get("pending_2fa_user_id")

        if not pending_user_id:
            raise serializers.ValidationError(
                {"non_field_errors": "No pending 2FA session found."},
                code="NO_PENDING_2FA",
            )

        try:
            user = User.objects.get(id=pending_user_id)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {"non_field_errors": "INVALID_CREDENTIALS"},
                code="INVALID_CREDENTIALS",
            )

        totp = pyotp.TOTP(user.totp_secret)
        if not totp.verify(attrs["code"]):
            raise serializers.ValidationError(
                {"non_field_errors": "INVALID_TOTP_CODE"},
                code="INVALID_TOTP_CODE",
            )

        attrs["user"] = user
        return attrs
