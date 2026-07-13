"""
whitelabel/serializers.py

TenantConfigSerializer — full serialization of TenantConfig for:
  - GET  /api/v1/tenant/config/  (read: logo/favicon as URL strings)
  - PATCH /api/v1/tenant/config/ (write: accept multipart file upload)

Requirements: 7.1, 7.2
"""

from rest_framework import serializers

from apps.whitelabel.models import TenantConfig


class TenantConfigSerializer(serializers.ModelSerializer):
    """
    Serializer for the TenantConfig singleton.

    Image fields (logo, favicon) behave differently depending on the HTTP method:
      - On GET the DRF ImageField renders the stored file's URL (read-only).
      - On PATCH a new file can be uploaded via multipart/form-data.

    All other fields are writable unless noted otherwise.
    """

    # Explicit URL representations for GET responses.
    # DRF's ImageField already returns the URL via field.to_representation(),
    # so no override is needed — the standard serializer.url() call works as long
    # as the storage backend implements .url().

    class Meta:
        model = TenantConfig
        fields = [
            # Branding
            "id",
            "restaurant_name",
            "logo",
            "primary_color",
            "secondary_color",
            "font_choice",
            "custom_domain",
            "favicon",
            "qr_design_template",
            "receipt_header",
            "receipt_footer",
            # Localisation
            "default_language",
            "currency",
            "currency_format",
            "timezone",
            "date_format",
            "time_format",
            # Financial / tax
            "tax_rate",
            "tax_label",
            "service_charge_pct",
            # Table formatting
            "table_number_prefix",
        ]
        # logo and favicon are FileField / ImageField — DRF will use the storage
        # backend's .url() method on read, and accept uploads on write.
        extra_kwargs = {
            "logo": {"required": False, "allow_null": True},
            "favicon": {"required": False, "allow_null": True},
        }
