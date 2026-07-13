"""
whitelabel/admin.py — Admin registration for TenantConfig.
"""

from django.contrib import admin

from apps.whitelabel.models import TenantConfig


@admin.register(TenantConfig)
class TenantConfigAdmin(admin.ModelAdmin):
    list_display = ("restaurant_name", "default_language", "currency", "timezone")
    search_fields = ("restaurant_name", "custom_domain")
    fieldsets = (
        ("Branding", {
            "fields": (
                "restaurant_name", "logo", "favicon",
                "primary_color", "secondary_color",
                "font_choice", "custom_domain",
                "qr_design_template",
            ),
        }),
        ("Receipt Templates", {
            "fields": ("receipt_header", "receipt_footer"),
        }),
        ("Localisation", {
            "fields": (
                "default_language", "currency", "currency_format",
                "timezone", "date_format", "time_format",
            ),
        }),
        ("Financial / Tax", {
            "fields": ("tax_rate", "tax_label", "service_charge_pct"),
        }),
        ("Table Formatting", {
            "fields": ("table_number_prefix",),
        }),
    )
