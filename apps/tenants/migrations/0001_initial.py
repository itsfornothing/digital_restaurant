"""
Initial migration for apps.tenants.

Creates the Tenant and Domain tables in the public (shared) PostgreSQL schema.

Tenant inherits from django_tenants.TenantMixin which provides:
  - schema_name: VARCHAR(63) UNIQUE NOT NULL

Domain inherits from django_tenants.DomainMixin which provides:
  - domain: VARCHAR(253) UNIQUE NOT NULL
  - tenant: FK → tenants_tenant
  - is_primary: BOOLEAN NOT NULL
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        # django_tenants itself has no migrations to depend on here
    ]

    operations = [
        migrations.CreateModel(
            name="Tenant",
            fields=[
                # django_tenants TenantMixin adds schema_name as the implicit
                # "primary" identifier; we use Django's default BigAutoField id.
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                # Provided by TenantMixin — the PostgreSQL schema name
                (
                    "schema_name",
                    models.CharField(
                        db_index=True,
                        max_length=63,
                        unique=True,
                        verbose_name="Schema name",
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                ("slug", models.SlugField(unique=True)),
                ("is_active", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Tenant",
                "verbose_name_plural": "Tenants",
            },
        ),
        migrations.CreateModel(
            name="Domain",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                # Provided by DomainMixin
                (
                    "domain",
                    models.CharField(
                        db_index=True,
                        max_length=253,
                        unique=True,
                        verbose_name="Domain",
                    ),
                ),
                ("is_primary", models.BooleanField(default=True, db_index=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="domains",
                        to="tenants.tenant",
                        verbose_name="Tenant",
                    ),
                ),
            ],
            options={
                "verbose_name": "Domain",
                "verbose_name_plural": "Domains",
            },
        ),
    ]
