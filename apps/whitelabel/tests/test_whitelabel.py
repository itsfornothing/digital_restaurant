"""
apps/whitelabel/tests/test_whitelabel.py

Unit tests for the white-label configuration module.

Tests cover:
  - TenantConfig model: field defaults, __str__, Meta.verbose_name
  - TenantConfigSerializer: serializes all required fields
  - whitelabel_context processor: returns correct keys, handles missing config,
    activates Ethiopic font for 'am' language
  - TenantConfigViewSet URL wiring: tenant/config/ resolves correctly
"""

import pytest
from decimal import Decimal

from django.test import TestCase, RequestFactory
from django.core.cache import cache
from django.urls import reverse, resolve

from apps.whitelabel.models import TenantConfig
from apps.whitelabel.serializers import TenantConfigSerializer
from apps.whitelabel.context_processors import whitelabel_context


class TenantConfigModelTests(TestCase):
    """Tests for TenantConfig model fields, defaults, and Meta."""

    def _create_config(self, **overrides):
        """Helper to create a minimal valid TenantConfig."""
        defaults = {
            "restaurant_name": "Test Restaurant",
            "primary_color": "#3B82F6",
            "secondary_color": "#F59E0B",
        }
        defaults.update(overrides)
        return TenantConfig.objects.create(**defaults)

    def test_str_returns_restaurant_name(self):
        config = self._create_config(restaurant_name="Green Leaf Bistro")
        assert str(config) == "Green Leaf Bistro"

    def test_verbose_name(self):
        assert TenantConfig._meta.verbose_name == "Tenant Configuration"

    def test_default_font_choice(self):
        config = self._create_config()
        assert config.font_choice == "default"

    def test_default_language(self):
        config = self._create_config()
        assert config.default_language == "en"

    def test_default_currency(self):
        config = self._create_config()
        assert config.currency == "ETB"

    def test_default_timezone(self):
        config = self._create_config()
        assert config.timezone == "Africa/Addis_Ababa"

    def test_default_tax_rate(self):
        config = self._create_config()
        assert config.tax_rate == Decimal("15.00")

    def test_default_tax_label(self):
        config = self._create_config()
        assert config.tax_label == "VAT"

    def test_default_service_charge_pct(self):
        config = self._create_config()
        assert config.service_charge_pct == Decimal("0.00")

    def test_default_qr_design_template(self):
        config = self._create_config()
        assert config.qr_design_template == "default"

    def test_default_currency_format(self):
        config = self._create_config()
        assert config.currency_format == "{symbol}{amount}"

    def test_default_date_format(self):
        config = self._create_config()
        assert config.date_format == "%d/%m/%Y"

    def test_default_time_format(self):
        config = self._create_config()
        assert config.time_format == "%H:%M"

    def test_blank_fields(self):
        """custom_domain, receipt_header, receipt_footer, table_number_prefix may be blank."""
        config = self._create_config()
        assert config.custom_domain == ""
        assert config.receipt_header == ""
        assert config.receipt_footer == ""
        assert config.table_number_prefix == ""

    def test_nullable_image_fields(self):
        """logo and favicon are nullable/blank."""
        config = self._create_config()
        # When no file is provided the field is falsy
        assert not config.logo
        assert not config.favicon

    def test_all_required_fields_stored_correctly(self):
        config = self._create_config(
            restaurant_name="Habesha Kitchen",
            primary_color="#FF0000",
            secondary_color="#00FF00",
            font_choice="serif",
            custom_domain="habesha.example.com",
            qr_design_template="ornate",
            receipt_header="Welcome to Habesha Kitchen!",
            receipt_footer="Thank you for dining with us.",
            default_language="am",
            currency="USD",
            currency_format="${amount}",
            timezone="America/New_York",
            date_format="%Y-%m-%d",
            time_format="%I:%M %p",
            tax_rate=Decimal("8.75"),
            tax_label="Sales Tax",
            service_charge_pct=Decimal("10.00"),
            table_number_prefix="T-",
        )
        refreshed = TenantConfig.objects.get(pk=config.pk)
        assert refreshed.restaurant_name == "Habesha Kitchen"
        assert refreshed.primary_color == "#FF0000"
        assert refreshed.secondary_color == "#00FF00"
        assert refreshed.font_choice == "serif"
        assert refreshed.custom_domain == "habesha.example.com"
        assert refreshed.qr_design_template == "ornate"
        assert refreshed.receipt_header == "Welcome to Habesha Kitchen!"
        assert refreshed.receipt_footer == "Thank you for dining with us."
        assert refreshed.default_language == "am"
        assert refreshed.currency == "USD"
        assert refreshed.currency_format == "${amount}"
        assert refreshed.timezone == "America/New_York"
        assert refreshed.date_format == "%Y-%m-%d"
        assert refreshed.time_format == "%I:%M %p"
        assert refreshed.tax_rate == Decimal("8.75")
        assert refreshed.tax_label == "Sales Tax"
        assert refreshed.service_charge_pct == Decimal("10.00")
        assert refreshed.table_number_prefix == "T-"


class TenantConfigSerializerTests(TestCase):
    """Tests for TenantConfigSerializer field coverage."""

    REQUIRED_FIELDS = {
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
        "default_language",
        "currency",
        "currency_format",
        "timezone",
        "date_format",
        "time_format",
        "tax_rate",
        "tax_label",
        "service_charge_pct",
        "table_number_prefix",
    }

    def _make_config(self):
        return TenantConfig.objects.create(
            restaurant_name="Serializer Test",
            primary_color="#111111",
            secondary_color="#222222",
        )

    def test_serializer_contains_all_required_fields(self):
        config = self._make_config()
        serializer = TenantConfigSerializer(config)
        assert self.REQUIRED_FIELDS == set(serializer.fields.keys())

    def test_serializer_output_restaurant_name(self):
        config = self._make_config()
        data = TenantConfigSerializer(config).data
        assert data["restaurant_name"] == "Serializer Test"

    def test_serializer_output_decimal_as_string(self):
        """Decimal fields are serialized as string representations."""
        config = self._make_config()
        data = TenantConfigSerializer(config).data
        # DRF DecimalField returns a string representation
        assert str(Decimal("15.00")) in str(data["tax_rate"])

    def test_serializer_valid_partial_update(self):
        config = self._make_config()
        serializer = TenantConfigSerializer(
            config,
            data={"primary_color": "#AABBCC"},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.primary_color == "#AABBCC"
        assert updated.restaurant_name == "Serializer Test"  # unchanged


class WhitelabelContextProcessorTests(TestCase):
    """Tests for the whitelabel_context template context processor."""

    def setUp(self):
        self.factory = RequestFactory()
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _make_request(self):
        return self.factory.get("/")

    def test_returns_tenant_config_key(self):
        result = whitelabel_context(self._make_request())
        assert "tenant_config" in result

    def test_returns_use_ethiopic_font_key(self):
        result = whitelabel_context(self._make_request())
        assert "use_ethiopic_font" in result

    def test_returns_amharic_css_key(self):
        result = whitelabel_context(self._make_request())
        assert "amharic_css" in result

    def test_safe_defaults_when_no_config(self):
        """No TenantConfig in DB → processor returns safe defaults without crashing."""
        result = whitelabel_context(self._make_request())
        assert result["tenant_config"]["currency"] == "ETB"
        assert result["tenant_config"]["default_language"] == "en"
        assert result["use_ethiopic_font"] is False
        assert str(result["amharic_css"]) == ""

    def test_ethiopic_font_activated_for_am_language(self):
        TenantConfig.objects.create(
            restaurant_name="Amharic Test",
            primary_color="#000000",
            secondary_color="#FFFFFF",
            default_language="am",
        )
        result = whitelabel_context(self._make_request())
        assert result["use_ethiopic_font"] is True
        assert "Noto Sans Ethiopic" in str(result["amharic_css"])

    def test_ethiopic_font_not_activated_for_en_language(self):
        TenantConfig.objects.create(
            restaurant_name="English Test",
            primary_color="#000000",
            secondary_color="#FFFFFF",
            default_language="en",
        )
        result = whitelabel_context(self._make_request())
        assert result["use_ethiopic_font"] is False
        assert str(result["amharic_css"]) == ""

    def test_config_loaded_from_db(self):
        TenantConfig.objects.create(
            restaurant_name="DB Load Test",
            primary_color="#123456",
            secondary_color="#654321",
        )
        result = whitelabel_context(self._make_request())
        assert result["tenant_config"]["restaurant_name"] == "DB Load Test"
        assert result["tenant_config"]["primary_color"] == "#123456"

    def test_config_cached_in_redis(self):
        """After first request, cache should be populated (locmem cache in tests)."""
        TenantConfig.objects.create(
            restaurant_name="Cache Test",
            primary_color="#AAAAAA",
            secondary_color="#BBBBBB",
        )
        # First call populates cache
        whitelabel_context(self._make_request())
        # Verify cache entry exists
        from apps.whitelabel.context_processors import _schema_name
        key = f"tenant_config:{_schema_name()}"
        cached = cache.get(key)
        assert cached is not None
        assert cached["restaurant_name"] == "Cache Test"

    def test_cache_returns_existing_entry(self):
        """If cache already has an entry, it should be returned without DB query."""
        from apps.whitelabel.context_processors import _schema_name
        key = f"tenant_config:{_schema_name()}"
        pre_populated = {
            "restaurant_name": "Cached Restaurant",
            "primary_color": "#CCCCCC",
            "secondary_color": "#DDDDDD",
            "default_language": "en",
            "currency": "ETB",
            "currency_format": "{symbol}{amount}",
            "timezone": "Africa/Addis_Ababa",
            "date_format": "%d/%m/%Y",
            "time_format": "%H:%M",
            "tax_rate": "15.00",
            "tax_label": "VAT",
            "service_charge_pct": "0.00",
            "table_number_prefix": "",
            "font_choice": "default",
            "custom_domain": "",
            "favicon": None,
            "logo": None,
            "qr_design_template": "default",
            "receipt_header": "",
            "receipt_footer": "",
            "id": None,
        }
        cache.set(key, pre_populated, 3600)
        result = whitelabel_context(self._make_request())
        assert result["tenant_config"]["restaurant_name"] == "Cached Restaurant"


class TenantConfigURLTests(TestCase):
    """Tests that the URL route for tenant/config/ resolves correctly."""

    def test_tenant_config_url_resolves(self):
        url = "/api/v1/tenant/config/"
        resolved = resolve(url)
        assert "tenant-config" == resolved.url_name
