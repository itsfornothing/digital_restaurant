"""
whitelabel/models.py

TenantConfig model — per-tenant branding and localization settings.

Each tenant has exactly one TenantConfig record which controls:
  - Visual branding: restaurant name, logo, colors, font, favicon
  - Domain routing: custom domain mapping
  - QR & receipt templates: qr_design_template, receipt header/footer
  - Locale settings: default language, currency, timezone, date/time formats
  - Financial settings: tax rate/label, service charge percentage
  - Table formatting: table number prefix

Requirements: 7.1, 7.3
"""

from decimal import Decimal

from django.db import models

from shared.storage import R2Storage


class TenantConfig(models.Model):
    """
    Per-tenant white-label configuration.

    Stores all branding, localization, and operational settings that
    Tenant_Owners can customise through the API.  There is one record per
    tenant schema; the API always operates on a singleton-style
    retrieve / partial_update pattern.
    """

    # ---------------------------------------------------------------
    # Branding
    # ---------------------------------------------------------------

    restaurant_name = models.CharField(max_length=200)
    logo = models.ImageField(
        storage=R2Storage(),
        null=True,
        blank=True,
        upload_to="logos/",
    )
    primary_color = models.CharField(
        max_length=7,
        help_text="Hex colour code, e.g. #8B3A2A",
    )
    secondary_color = models.CharField(
        max_length=7,
        help_text="Hex colour code, e.g. #5D7061",
    )
    font_choice = models.CharField(max_length=50, default="default")
    custom_domain = models.CharField(max_length=200, blank=True)
    favicon = models.ImageField(
        storage=R2Storage(),
        null=True,
        blank=True,
        upload_to="favicons/",
    )
    qr_design_template = models.CharField(max_length=50, default="default")
    receipt_header = models.TextField(blank=True)
    receipt_footer = models.TextField(blank=True)

    # ---------------------------------------------------------------
    # Localisation
    # ---------------------------------------------------------------

    default_language = models.CharField(max_length=10, default="en")
    currency = models.CharField(max_length=3, default="ETB")
    currency_format = models.CharField(max_length=20, default="{symbol}{amount}")
    timezone = models.CharField(max_length=50, default="Africa/Addis_Ababa")
    date_format = models.CharField(max_length=30, default="%d/%m/%Y")
    time_format = models.CharField(max_length=30, default="%H:%M")

    # ---------------------------------------------------------------
    # Financial / tax
    # ---------------------------------------------------------------

    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("15.00"),
    )
    tax_label = models.CharField(max_length=30, default="VAT")
    service_charge_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    # ---------------------------------------------------------------
    # Table formatting
    # ---------------------------------------------------------------

    table_number_prefix = models.CharField(max_length=10, blank=True)

    # ---------------------------------------------------------------
    # Meta / dunder
    # ---------------------------------------------------------------

    class Meta:
        verbose_name = "Tenant Configuration"

    def __str__(self) -> str:
        return self.restaurant_name
