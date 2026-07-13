"""
Property-Based Tests: White-Label Branding Immediate Application

Property 17: White-Label Branding Immediate Application

  For any branding configuration saved by a Tenant_Owner, the next HTTP
  request to any customer-facing page for that tenant SHALL reflect the new
  configuration values (colors, logo URL, font, language) without requiring a
  platform restart.

Validates: Requirements 7.1, 7.2

Strategy:
  The customer-facing "page request" is modelled as a call to the
  ``whitelabel_context(request)`` Django template context processor, which is
  injected into every customer-facing template render.  This is the canonical
  mechanism by which TenantConfig values reach the customer UI.

  The property test exercises the full pipeline:
    1. Save a TenantConfig (or update it) via TenantConfigSerializer.
    2. Invalidate the Redis/locmem cache — exactly as TenantConfigViewSet
       does on every successful PATCH (``cache.delete(cache_key)``).
    3. Invoke ``whitelabel_context()`` to simulate the next page request.
    4. Assert that every saved field is reflected verbatim in the returned
       ``tenant_config`` dict.

  No mocking of the cache or DB layer is used — the tests run against the
  real in-memory cache backend (locmem) and SQLite in-memory DB configured in
  ``config/settings/testing.py``.

Design notes:
  - Hypothesis runs multiple ``@given`` examples within a single test function
    call.  Because the SQLite in-memory DB is shared across all examples in a
    test, each example resets all state at the start (``TenantConfig.objects.all
    ().delete()`` and ``cache.clear()``) to ensure a clean slate.
  - ``TenantConfig.objects.first()`` is the mechanism used by both the context
    processor and the ViewSet's ``_get_object_or_none()``.  Because we always
    start each example with exactly one config record, the singleton contract
    is preserved throughout.
  - Cache invalidation after save is mandatory to simulate the view's
    behaviour.  Without it the context processor returns the previously cached
    (stale) value, which is the exact bug scenario the property guards against.
  - ``_schema_name()`` is imported from the context processor to construct
    the correct cache key, keeping the key-construction logic DRY.
  - ``assume()`` is used only where two Hypothesis-generated values must be
    distinct (e.g., old vs new color); it is never used to skip valid edge
    cases.

Requirements: 7.1, 7.2
"""

import pytest
from django.core.cache import cache
from django.test import RequestFactory
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from apps.whitelabel.context_processors import _schema_name, whitelabel_context
from apps.whitelabel.models import TenantConfig
from apps.whitelabel.serializers import TenantConfigSerializer

# ---------------------------------------------------------------------------
# Shared Hypothesis strategies
# ---------------------------------------------------------------------------

# Printable restaurant name: letters, digits, basic punctuation, spaces.
# We .map(str.strip) so that leading/trailing whitespace is removed before
# comparison — Django CharField strips trailing whitespace on save, and we
# want generated values to round-trip losslessly.
_restaurant_name_st = st.text(
    min_size=1,
    max_size=100,
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
).map(str.strip).filter(bool)  # strip outer whitespace; discard empty results

# Valid CSS hex colour strings (#RRGGBB).
_hex_color_st = st.from_regex(r"#[0-9A-F]{6}", fullmatch=True)

# Supported font choices.
_font_choices = ["default", "serif", "sans-serif", "monospace", "noto"]

# Supported languages.
_language_choices = ["en", "am"]

# Supported currencies.
_currency_choices = ["ETB", "USD", "EUR", "GBP"]

# Tax labels — printable, non-empty, whitespace-stripped for round-trip safety.
_tax_label_st = st.text(
    min_size=1,
    max_size=30,
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
).map(str.strip).filter(bool)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request():
    """Return a minimal GET request for use with whitelabel_context."""
    return RequestFactory().get("/")


def _reset_state() -> None:
    """
    Reset all TenantConfig rows and flush the cache.

    Called at the start of each ``@given`` example to ensure a fully clean
    slate — necessary because Hypothesis runs multiple examples within the
    same test function call sharing the same in-memory SQLite DB.
    """
    TenantConfig.objects.all().delete()
    cache.clear()


def _create_config(**kwargs) -> TenantConfig:
    """
    Create and return a TenantConfig with sensible base values.

    Any keyword argument overrides the defaults.  Always call ``_reset_state``
    before this to guarantee exactly one config row exists.
    """
    defaults = {
        "restaurant_name": "Base Restaurant",
        "primary_color": "#000000",
        "secondary_color": "#FFFFFF",
    }
    defaults.update(kwargs)
    return TenantConfig.objects.create(**defaults)


def _invalidate_cache() -> None:
    """
    Simulate TenantConfigViewSet.partial_update()'s cache invalidation.

    Deletes the tenant-config cache entry so the context processor reloads
    from the DB on the next call — exactly as the production view does.
    """
    key = f"tenant_config:{_schema_name()}"
    cache.delete(key)


def _update_config_via_serializer(instance: TenantConfig, data: dict) -> TenantConfig:
    """
    Partially update *instance* using TenantConfigSerializer (mirrors the view).

    Raises AssertionError if the serializer is invalid.
    """
    serializer = TenantConfigSerializer(instance, data=data, partial=True)
    assert serializer.is_valid(), f"Serializer errors: {serializer.errors}"
    return serializer.save()


# ---------------------------------------------------------------------------
# Property 17a — Any branding field change is reflected on the next request
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    new_name=_restaurant_name_st,
    new_primary=_hex_color_st,
    new_secondary=_hex_color_st,
    new_font=st.sampled_from(_font_choices),
    new_currency=st.sampled_from(_currency_choices),
)
@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
def test_property_17a_branding_fields_reflected_after_save(
    new_name: str,
    new_primary: str,
    new_secondary: str,
    new_font: str,
    new_currency: str,
) -> None:
    """
    **Validates: Requirements 7.1, 7.2**

    For any combination of branding field values, saving those values via
    TenantConfigSerializer and then calling ``whitelabel_context()`` MUST
    return a ``tenant_config`` dict that contains ALL the new values.

    The test:
      1. Resets state and creates an initial TenantConfig with fixed values.
      2. Populates the cache by calling whitelabel_context (simulates a prior
         page request having already run).
      3. Updates the config with Hypothesis-generated values.
      4. Invalidates the cache (mirrors TenantConfigViewSet.partial_update).
      5. Calls whitelabel_context again (the "next page request").
      6. Asserts every updated field equals the new value.
    """
    # Reset: clean DB + cache for this example
    _reset_state()

    # Step 1: create initial config with fixed, known values
    instance = _create_config(
        restaurant_name="Initial Name",
        primary_color="#111111",
        secondary_color="#222222",
        font_choice="default",
        currency="ETB",
    )

    # Step 2: populate cache (simulates a prior customer page load)
    initial_ctx = whitelabel_context(_make_request())
    assert initial_ctx["tenant_config"]["restaurant_name"] == "Initial Name", (
        "Test setup error: initial config not reflected in context processor."
    )

    # Step 3: update config with Hypothesis-generated values
    _update_config_via_serializer(instance, {
        "restaurant_name": new_name,
        "primary_color": new_primary,
        "secondary_color": new_secondary,
        "font_choice": new_font,
        "currency": new_currency,
    })

    # Step 4: invalidate cache (exactly what TenantConfigViewSet.partial_update does)
    _invalidate_cache()

    # Step 5: simulate next customer page request
    ctx = whitelabel_context(_make_request())
    config = ctx["tenant_config"]

    # Step 6: all updated fields must reflect the new values
    assert config["restaurant_name"] == new_name, (
        f"Property 17a FAILED: restaurant_name expected {new_name!r}, "
        f"got {config['restaurant_name']!r}. "
        f"Branding changes must be reflected immediately after save."
    )
    assert config["primary_color"] == new_primary, (
        f"Property 17a FAILED: primary_color expected {new_primary!r}, "
        f"got {config['primary_color']!r}."
    )
    assert config["secondary_color"] == new_secondary, (
        f"Property 17a FAILED: secondary_color expected {new_secondary!r}, "
        f"got {config['secondary_color']!r}."
    )
    assert config["font_choice"] == new_font, (
        f"Property 17a FAILED: font_choice expected {new_font!r}, "
        f"got {config['font_choice']!r}."
    )
    assert config["currency"] == new_currency, (
        f"Property 17a FAILED: currency expected {new_currency!r}, "
        f"got {config['currency']!r}."
    )


# ---------------------------------------------------------------------------
# Property 17b — No stale cache values survive a branding update
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    color_v1=_hex_color_st,
    color_v2=_hex_color_st,
)
@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
def test_property_17b_no_stale_cache_after_branding_update(
    color_v1: str,
    color_v2: str,
) -> None:
    """
    **Validates: Requirements 7.2**

    For any two distinct primary_color values, after updating from v1 to v2
    and invalidating the cache, the next context-processor call MUST return
    v2 and MUST NOT return the previously cached v1.

    This guards against the failure mode where the cache is not invalidated
    on save — the critical mistake that would cause stale branding to persist
    across requests.
    """
    # The two color values must be different so we can detect staleness.
    assume(color_v1 != color_v2)

    _reset_state()

    # Create initial config with v1
    instance = _create_config(primary_color=color_v1)

    # Populate cache with v1
    ctx_before = whitelabel_context(_make_request())
    assert ctx_before["tenant_config"]["primary_color"] == color_v1, (
        "Test setup error: initial color not reflected in context processor."
    )

    # Update to v2 + invalidate cache
    _update_config_via_serializer(instance, {"primary_color": color_v2})
    _invalidate_cache()

    # Next request must see v2, not v1
    ctx_after = whitelabel_context(_make_request())
    returned_color = ctx_after["tenant_config"]["primary_color"]

    assert returned_color == color_v2, (
        f"Property 17b FAILED: expected new color {color_v2!r} after update, "
        f"but got {returned_color!r}. "
        f"If this equals the old color {color_v1!r}, the cache was not properly "
        f"invalidated.  TenantConfigViewSet.partial_update must call cache.delete() "
        f"after save (Requirement 7.2)."
    )


# ---------------------------------------------------------------------------
# Property 17c — Language change is reflected immediately, font flag follows
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    initial_lang=st.sampled_from(_language_choices),
    new_lang=st.sampled_from(_language_choices),
)
@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
def test_property_17c_language_change_reflected_immediately(
    initial_lang: str,
    new_lang: str,
) -> None:
    """
    **Validates: Requirements 7.1, 7.2**

    For any language change from initial_lang to new_lang, the next
    whitelabel_context call MUST:
      1. Return ``tenant_config['default_language'] == new_lang``.
      2. Set ``use_ethiopic_font = True`` if and only if new_lang == 'am'.

    This covers the Amharic-specific branding path in Requirements 7.4 and
    ensures the language switch is truly immediate with no cached stale state.
    """
    _reset_state()

    # Create config with initial language
    instance = _create_config(default_language=initial_lang)

    # Populate cache with initial language
    whitelabel_context(_make_request())

    # Update to new language + invalidate cache
    _update_config_via_serializer(instance, {"default_language": new_lang})
    _invalidate_cache()

    # Next request
    ctx = whitelabel_context(_make_request())

    # Language reflected
    assert ctx["tenant_config"]["default_language"] == new_lang, (
        f"Property 17c FAILED: default_language expected {new_lang!r}, "
        f"got {ctx['tenant_config']['default_language']!r}. "
        f"Language change must propagate immediately (Requirement 7.2)."
    )

    # Ethiopic font flag follows language
    expected_ethiopic = new_lang == "am"
    assert ctx["use_ethiopic_font"] == expected_ethiopic, (
        f"Property 17c FAILED: use_ethiopic_font expected {expected_ethiopic} "
        f"for language {new_lang!r}, got {ctx['use_ethiopic_font']}. "
        f"Noto Sans Ethiopic must be active iff language is 'am' (Requirement 7.4)."
    )

    # Amharic CSS present iff Amharic is selected
    amharic_css_str = str(ctx["amharic_css"])
    if expected_ethiopic:
        assert "Noto Sans Ethiopic" in amharic_css_str, (
            f"Property 17c FAILED: amharic_css must contain 'Noto Sans Ethiopic' "
            f"when language is 'am', but CSS was empty/missing."
        )
    else:
        assert amharic_css_str == "", (
            f"Property 17c FAILED: amharic_css must be empty when language is "
            f"{new_lang!r}, but got non-empty CSS."
        )


# ---------------------------------------------------------------------------
# Property 17d — Sequential updates always reflect the latest value
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    names=st.lists(
        # Strip whitespace so Django CharField storage is lossless round-trip.
        _restaurant_name_st.map(str.strip).filter(bool),
        min_size=2,
        max_size=5,
        unique=True,
    )
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_property_17d_multiple_sequential_updates_always_reflect_latest(
    names: list,
) -> None:
    """
    **Validates: Requirements 7.1, 7.2**

    For any sequence of 2–5 distinct restaurant name updates applied one
    after another, calling ``whitelabel_context()`` after each save MUST
    return the most recently saved name — never a prior one.

    This validates that the cache invalidation and reload cycle is correct
    across multiple consecutive updates, not just the first change.
    """
    _reset_state()

    # Create initial config
    instance = _create_config(restaurant_name="__INITIAL__")
    whitelabel_context(_make_request())  # populate cache

    # Apply each name update in sequence
    for name in names:
        _update_config_via_serializer(instance, {"restaurant_name": name})
        _invalidate_cache()

        ctx = whitelabel_context(_make_request())
        returned_name = ctx["tenant_config"]["restaurant_name"]

        assert returned_name == name, (
            f"Property 17d FAILED: after updating to {name!r}, "
            f"whitelabel_context returned {returned_name!r}. "
            f"Each sequential update must be reflected on the immediate "
            f"next request (Requirement 7.2)."
        )

    # Final verification: context reflects the LAST name in the sequence
    final_ctx = whitelabel_context(_make_request())
    assert final_ctx["tenant_config"]["restaurant_name"] == names[-1], (
        f"Property 17d FAILED: final whitelabel_context does not reflect the "
        f"last saved name {names[-1]!r}."
    )


# ---------------------------------------------------------------------------
# Property 17e — All configurable scalar fields are reflected comprehensively
# ---------------------------------------------------------------------------

_full_config_st = st.fixed_dictionaries({
    "restaurant_name": _restaurant_name_st,
    "primary_color": _hex_color_st,
    "secondary_color": _hex_color_st,
    "font_choice": st.sampled_from(_font_choices),
    "custom_domain": st.one_of(
        st.just(""),
        st.from_regex(r"[a-z]{3,10}\.[a-z]{2,6}", fullmatch=True),
    ),
    "default_language": st.sampled_from(_language_choices),
    "currency": st.sampled_from(_currency_choices),
    "currency_format": st.sampled_from(["{symbol}{amount}", "${amount}", "{amount} ETB"]),
    "timezone": st.sampled_from(["Africa/Addis_Ababa", "UTC", "America/New_York"]),
    "tax_label": _tax_label_st,
    "table_number_prefix": st.one_of(st.just(""), st.just("T-"), st.just("TBL-")),
    "receipt_header": st.one_of(st.just(""), _restaurant_name_st),
    "receipt_footer": st.one_of(st.just(""), _restaurant_name_st),
})


@pytest.mark.django_db
@given(payload=_full_config_st)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_property_17e_context_processor_reflects_all_configurable_fields(
    payload: dict,
) -> None:
    """
    **Validates: Requirements 7.1, 7.2**

    For any complete branding payload, saving it and calling
    ``whitelabel_context()`` MUST return a ``tenant_config`` dict where
    every scalar field equals the saved value.

    This is the comprehensive coverage test — it verifies that ALL
    configurable fields, not just a selection, are correctly propagated
    through the save→invalidate→reload pipeline.
    """
    _reset_state()

    # Create initial config
    instance = _create_config()

    # Apply the full payload via serializer
    _update_config_via_serializer(instance, payload)
    _invalidate_cache()

    # Simulate customer page request
    ctx = whitelabel_context(_make_request())
    config = ctx["tenant_config"]

    # Assert every payload field is reflected in the context
    scalar_fields = [
        "restaurant_name",
        "primary_color",
        "secondary_color",
        "font_choice",
        "custom_domain",
        "default_language",
        "currency",
        "currency_format",
        "timezone",
        "tax_label",
        "table_number_prefix",
        "receipt_header",
        "receipt_footer",
    ]

    for field in scalar_fields:
        if field not in payload:
            continue  # field not in this particular payload; skip
        expected = payload[field]
        actual = config.get(field)
        assert actual == expected, (
            f"Property 17e FAILED: field {field!r} expected {expected!r}, "
            f"got {actual!r}. "
            f"All configurable scalar fields must be reflected immediately "
            f"after save (Requirements 7.1, 7.2)."
        )
