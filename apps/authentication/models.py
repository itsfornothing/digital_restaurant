"""
authentication/models.py

User model extending AbstractBaseUser + PermissionsMixin, with:
  - UUID primary key
  - Email-based authentication
  - Role choices (6 roles)
  - Branch FK (lazy reference to avoid circular import)
  - Account lockout fields (failed_login_count, locked_at)
  - TOTP secret field
  - PasswordResetToken model for the password reset flow
"""

import uuid

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Role constants
# ---------------------------------------------------------------------------

class UserRole(models.TextChoices):
    SUPER_ADMIN = "Super_Admin", "Super Admin"
    TENANT_OWNER = "Tenant_Owner", "Tenant Owner"
    BRANCH_MANAGER = "Branch_Manager", "Branch Manager"
    RECEPTIONIST = "Receptionist", "Receptionist"
    KITCHEN_STAFF = "Kitchen_Staff", "Kitchen Staff"
    CUSTOMER = "Customer", "Customer"


# ---------------------------------------------------------------------------
# Custom user manager
# ---------------------------------------------------------------------------

class UserManager(BaseUserManager):
    """
    Manager providing create_user() and create_superuser() factory methods.
    Email is normalised (lower-cased domain) before storage.
    """

    def create_user(self, email: str, password: str, role: str, **extra_fields):
        """
        Create and persist a regular user.

        Args:
            email:        The user's email address (used as USERNAME_FIELD).
            password:     Plaintext password — hashed by set_password().
            role:         One of the UserRole choices.
            **extra_fields: Any additional User field values.

        Returns:
            Saved User instance.
        """
        if not email:
            raise ValueError("The Email field must be set")
        if not role:
            raise ValueError("The Role field must be set")

        email = self.normalize_email(email)
        user = self.model(email=email, role=role, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str, **extra_fields):
        """
        Create and persist a Super_Admin user with all staff/superuser flags set.
        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(
            email=email,
            password=password,
            role=UserRole.SUPER_ADMIN,
            **extra_fields,
        )


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------

class User(AbstractBaseUser, PermissionsMixin):
    """
    Platform-wide user model.

    Replaces Django's default User via AUTH_USER_MODEL = 'authentication.User'.
    Lives in each tenant's schema (TENANT_APP).

    Authentication is email-based.  Passwords are hashed with Argon2id
    (configured in settings PASSWORD_HASHERS).

    Account lockout:
        After 5 consecutive failed login attempts failed_login_count reaches 5
        and locked_at is set to the current UTC timestamp.  The lockout check
        in LoginSerializer runs before password verification so that an attacker
        cannot probe the password even after lockout.

    TOTP:
        totp_secret stores the base32 secret used by pyotp.  An empty string
        means 2FA is not configured for this user.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    email = models.EmailField(
        unique=True,
        db_index=True,
    )
    role = models.CharField(
        max_length=30,
        choices=UserRole.choices,
    )
    # Lazy FK — branches app is a fellow TENANT_APP; using a string reference
    # avoids circular import issues at app-load time.
    branch = models.ForeignKey(
        "branches.Branch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staff",
    )

    # Standard Django staff / active flags used by the admin and permission system
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    # Account lockout
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_at = models.DateTimeField(null=True, blank=True)

    # Two-factor authentication (pyotp base32 secret; empty = 2FA disabled)
    totp_secret = models.CharField(max_length=32, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["role"]

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self) -> str:
        return f"{self.email} ({self.role})"

    # ------------------------------------------------------------------
    # Lockout helpers
    # ------------------------------------------------------------------

    @property
    def is_locked(self) -> bool:
        """Return True if the account is currently locked."""
        return self.locked_at is not None

    def record_failed_login(self) -> None:
        """
        Increment the failed-login counter and lock the account once the
        threshold (5) is reached.  Saves the instance.
        """
        self.failed_login_count += 1
        if self.failed_login_count >= 5:
            self.locked_at = timezone.now()
        self.save(update_fields=["failed_login_count", "locked_at"])

    def reset_login_attempts(self) -> None:
        """
        Clear the failed-login counter and remove the lockout timestamp.
        Called on successful authentication.  Saves the instance.
        """
        self.failed_login_count = 0
        self.locked_at = None
        self.save(update_fields=["failed_login_count", "locked_at"])

    def save(self, *args, **kwargs):
        old_role = None
        if self.pk:
            try:
                old_role = User.objects.get(pk=self.pk).role
            except User.DoesNotExist:
                pass
        super().save(*args, **kwargs)
        if old_role is not None and old_role != self.role:
            self._log_role_assignment(old_role, self.role)

    def _log_role_assignment(self, old_role: str, new_role: str) -> None:
        try:
            from apps.audit.decorators import _get_context_attr
            from apps.audit.models import AuditLog
            AuditLog.objects.create(
                action="ROLE_ASSIGNED",
                resource_type="User",
                resource_id=self.pk,
                old_value={"role": old_role},
                new_value={"role": new_role},
                user_id=_get_context_attr("user_id"),
                user_role=_get_context_attr("user_role", ""),
                ip_address=_get_context_attr("ip_address") or "0.0.0.0",
                user_agent=_get_context_attr("user_agent", ""),
                status="success",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Password reset token model
# ---------------------------------------------------------------------------

class PasswordResetToken(models.Model):
    """
    Single-use, time-limited token for the password reset flow.

    On each new reset request all prior tokens for the same user are marked
    is_used=True before the new token is issued, so only the most-recent
    token is ever valid (Requirement 3.4).

    A token is considered expired if created_at is older than 1 hour.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        "authentication.User",
        on_delete=models.CASCADE,
        related_name="password_reset_tokens",
    )
    token = models.UUIDField(
        unique=True,
        default=uuid.uuid4,
        editable=False,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    class Meta:
        verbose_name = "password reset token"
        verbose_name_plural = "password reset tokens"

    def __str__(self) -> str:
        return f"PasswordResetToken(user={self.user_id}, used={self.is_used})"

    @property
    def is_expired(self) -> bool:
        """Return True if the token was created more than 1 hour ago."""
        from datetime import timedelta
        return timezone.now() > self.created_at + timedelta(hours=1)


# ---------------------------------------------------------------------------
# Account unlock helper (used by admin actions and password reset confirm)
# ---------------------------------------------------------------------------

def unlock_account(user: User) -> None:
    """
    Reset the account lockout fields on *user*.

    This is used by:
      - PasswordResetConfirmView (after a successful password reset)
      - Admin tooling
    """
    user.failed_login_count = 0
    user.locked_at = None
    user.save(update_fields=["failed_login_count", "locked_at"])
