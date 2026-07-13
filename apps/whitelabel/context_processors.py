"""
whitelabel/context_processors.py

Injects TenantConfig fields into every template render for the current tenant.

Behaviour:
  1. Check Redis for a cached config dict under key ``tenant_config:{schema}``.
  2. On cache miss, fetch the TenantConfig record from the DB and store it in
     Redis for 3600 seconds.
  3. If no TenantConfig exists (e.g. tenant not yet configured), safe defaults
     are returned so templates never crash on missing attributes.
  4. When ``default_language == 'am'``, inject ``use_ethiopic_font=True`` and
     a ``amharic_css`` string containing the Noto Sans Ethiopic @font-face and
     body rules so templates can include it in a <style> tag.

Cache key pattern: ``tenant_config:{connection.schema_name}``
Cache timeout: 3600 seconds (1 hour)

Requirements: 7.2, 7.4
"""

import logging

from django.core.cache import cache
from django.utils.safestring import mark_safe

logger = logging.getLogger(__name__)

# CSS applied when Amharic is the active language.
# Loads Noto Sans Ethiopic from Google Fonts CDN and sets it as the body font.
_ETHIOPIC_CSS = mark_safe(
    """
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Ethiopic:wght@400;500;600;700&display=swap');
body, body * {
    font-family: 'Noto Sans Ethiopic', sans-serif !important;
    line-height: 1.8;
}
""".strip()
)

# Safe default config used when TenantConfig does not exist yet.
_DEFAULT_CONFIG: dict = {
    "id": None,
    "restaurant_name": "",
    "logo": None,
    "primary_color": "#8B3A2A",
    "secondary_color": "#5D7061",
    "font_choice": "default",
    "custom_domain": "",
    "favicon": None,
    "qr_design_template": "default",
    "receipt_header": "",
    "receipt_footer": "",
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
}


def _schema_name() -> str:
    """Return the current tenant schema name, falling back to 'public'."""
    try:
        from django.db import connection
        return getattr(connection, "schema_name", "public") or "public"
    except Exception:
        return "public"


def _config_to_dict(config_obj) -> dict:
    """
    Serialise a TenantConfig instance to a plain dict suitable for caching
    and template use.

    Image fields are converted to their URL strings (or None if not set).
    Decimal fields are converted to str so that JSON serialisation in the
    cache backend works without custom encoders.
    """
    def _image_url(field_file):
        try:
            return field_file.url if field_file else None
        except Exception:
            return None

    return {
        "id": config_obj.pk,
        "restaurant_name": config_obj.restaurant_name,
        "logo": _image_url(config_obj.logo),
        "primary_color": config_obj.primary_color,
        "secondary_color": config_obj.secondary_color,
        "font_choice": config_obj.font_choice,
        "custom_domain": config_obj.custom_domain,
        "favicon": _image_url(config_obj.favicon),
        "qr_design_template": config_obj.qr_design_template,
        "receipt_header": config_obj.receipt_header,
        "receipt_footer": config_obj.receipt_footer,
        "default_language": config_obj.default_language,
        "currency": config_obj.currency,
        "currency_format": config_obj.currency_format,
        "timezone": config_obj.timezone,
        "date_format": config_obj.date_format,
        "time_format": config_obj.time_format,
        "tax_rate": str(config_obj.tax_rate),
        "tax_label": config_obj.tax_label,
        "service_charge_pct": str(config_obj.service_charge_pct),
        "table_number_prefix": config_obj.table_number_prefix,
    }


def whitelabel_context(request) -> dict:
    """
    Django template context processor.

    Returns a dict with:
        tenant_config     — full TenantConfig as a plain dict
        use_ethiopic_font — True when default_language == 'am'
        amharic_css       — safe CSS string for Noto Sans Ethiopic (empty string
                            when Amharic is not active)

    Loaded from Redis on cache hit; fetched from DB and cached on miss.
    Handles TenantConfig.DoesNotExist and any DB/cache errors gracefully —
    always returns the safe-defaults dict so templates never crash.
    """
    cache_key = f"tenant_config:{_schema_name()}"
    config_dict: dict | None = None

    # --- Try cache first ---
    try:
        config_dict = cache.get(cache_key)
    except Exception:
        logger.warning("whitelabel_context: Redis cache get failed for key %s", cache_key)

    # --- Cache miss: load from DB ---
    if config_dict is None:
        try:
            from apps.whitelabel.models import TenantConfig  # noqa: avoid circular at module level

            obj = TenantConfig.objects.first()
            if obj is not None:
                config_dict = _config_to_dict(obj)
                try:
                    cache.set(cache_key, config_dict, timeout=3600)
                except Exception:
                    logger.warning(
                        "whitelabel_context: Redis cache set failed for key %s", cache_key
                    )
            else:
                config_dict = dict(_DEFAULT_CONFIG)
        except Exception:
            logger.warning(
                "whitelabel_context: DB fetch failed for TenantConfig", exc_info=True
            )
            config_dict = dict(_DEFAULT_CONFIG)

    # --- Amharic font injection ---
    use_ethiopic_font: bool = config_dict.get("default_language") == "am"
    # Also expose use_amharic as an alias — some templates use this variable
    # name (e.g. customer/menu.html context uses use_amharic from the
    # template context processor — Requirement 7.4, 16.4).
    use_amharic: bool = use_ethiopic_font
    amharic_css = _ETHIOPIC_CSS if use_ethiopic_font else mark_safe("")

    return {
        "tenant_config": config_dict,
        "use_ethiopic_font": use_ethiopic_font,
        "use_amharic": use_amharic,
        "amharic_css": amharic_css,
    }
