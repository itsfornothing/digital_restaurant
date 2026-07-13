from django.apps import AppConfig


class WhitelabelConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.whitelabel"
    verbose_name = "White Label"

    def ready(self):
        # Connect TenantConfig post-save signal for cache invalidation
        # (Task 20.2 — tenant_config:{schema} cache key with 5-minute TTL).
        import apps.whitelabel.signals  # noqa: F401
