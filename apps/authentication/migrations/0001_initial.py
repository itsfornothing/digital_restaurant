"""
Initial migration for apps.authentication.

Creates:
  - authentication_user
  - authentication_passwordresettoken
"""

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        # branches may not have its migration yet; use a soft dependency
        # by declaring it conditional on the app being in INSTALLED_APPS.
        # The FK is NULL=True so the column can be created without the
        # branches table existing yet.
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="User",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("password", models.CharField(max_length=128, verbose_name="password")),
                (
                    "last_login",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="last login"
                    ),
                ),
                (
                    "is_superuser",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Designates that this user has all permissions without "
                            "explicitly assigning them."
                        ),
                        verbose_name="superuser status",
                    ),
                ),
                (
                    "email",
                    models.EmailField(db_index=True, max_length=254, unique=True),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("Super_Admin", "Super Admin"),
                            ("Tenant_Owner", "Tenant Owner"),
                            ("Branch_Manager", "Branch Manager"),
                            ("Receptionist", "Receptionist"),
                            ("Kitchen_Staff", "Kitchen Staff"),
                            ("Customer", "Customer"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True),
                ),
                (
                    "is_staff",
                    models.BooleanField(default=False),
                ),
                (
                    "failed_login_count",
                    models.PositiveSmallIntegerField(default=0),
                ),
                (
                    "locked_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "totp_secret",
                    models.CharField(blank=True, default="", max_length=32),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "groups",
                    models.ManyToManyField(
                        blank=True,
                        help_text=(
                            "The groups this user belongs to. A user will get all "
                            "permissions granted to each of their groups."
                        ),
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.group",
                        verbose_name="groups",
                    ),
                ),
                (
                    "user_permissions",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Specific permissions for this user.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.permission",
                        verbose_name="user permissions",
                    ),
                ),
                # FK to branches.Branch added in a later migration once the
                # branches app migration runs.  We skip it here and handle it
                # via a data migration or AddField operation in task 10.
            ],
            options={
                "verbose_name": "user",
                "verbose_name_plural": "users",
            },
        ),
        migrations.CreateModel(
            name="PasswordResetToken",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "token",
                    models.UUIDField(
                        db_index=True, default=uuid.uuid4, editable=False, unique=True
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "is_used",
                    models.BooleanField(default=False),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="password_reset_tokens",
                        to="authentication.user",
                    ),
                ),
            ],
            options={
                "verbose_name": "password reset token",
                "verbose_name_plural": "password reset tokens",
            },
        ),
    ]
