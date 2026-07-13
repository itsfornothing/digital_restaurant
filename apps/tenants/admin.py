"""
tenants/admin.py — Register Tenant and Domain models in the Django admin.
"""

from django.contrib import admin
from django_tenants.admin import TenantAdminMixin

from .models import Domain, Tenant


@admin.register(Tenant)
class TenantAdmin(TenantAdminMixin, admin.ModelAdmin):
    """
    Admin for the Tenant model.

    TenantAdminMixin ensures the admin panel handles schema-switching correctly
    when inspecting tenant records from the public schema.
    """

    list_display = ("name", "slug", "schema_name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "schema_name")
    readonly_fields = ("schema_name", "created_at")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("-created_at",)


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    """Admin for the Domain model (hostname → Tenant mappings)."""

    list_display = ("domain", "tenant", "is_primary")
    list_filter = ("is_primary",)
    search_fields = ("domain", "tenant__name", "tenant__slug")
    raw_id_fields = ("tenant",)
    ordering = ("domain",)
