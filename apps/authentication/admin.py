"""
authentication/admin.py

Registers User and PasswordResetToken with the Django admin site so that
platform administrators can manage accounts and reset-token records via
the standard admin interface.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import PasswordResetToken, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Custom admin view for the platform User model.

    Extends Django's built-in UserAdmin with the extra fields
    (role, branch, failed_login_count, locked_at, totp_secret).
    """

    # Columns shown in the change-list table
    list_display = (
        "email",
        "role",
        "branch",
        "is_active",
        "is_staff",
        "failed_login_count",
        "locked_at",
        "created_at",
    )
    list_filter = ("role", "is_active", "is_staff", "is_superuser")
    search_fields = ("email",)
    ordering = ("email",)

    # Fields shown on the detail / edit page
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Profile",
            {"fields": ("role", "branch")},
        ),
        (
            "Security",
            {
                "fields": (
                    "failed_login_count",
                    "locked_at",
                    "totp_secret",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login",)}),
    )

    # Fields shown on the add-user page
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "role", "password1", "password2"),
            },
        ),
    )

    # USERNAME_FIELD is email, not username
    USERNAME_FIELD_OVERRIDE = "email"


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    """
    Read-only admin view for password reset tokens.

    Tokens should not be edited via the admin; this registration provides
    visibility for debugging and support use-cases.
    """

    list_display = ("token", "user", "created_at", "is_used", "is_expired")
    list_filter = ("is_used",)
    search_fields = ("user__email",)
    ordering = ("-created_at",)
    readonly_fields = ("token", "user", "created_at", "is_used")

    def is_expired(self, obj) -> bool:
        return obj.is_expired

    is_expired.boolean = True
    is_expired.short_description = "Expired?"
