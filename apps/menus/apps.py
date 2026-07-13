from django.apps import AppConfig


class MenusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.menus"
    verbose_name = "Menus"

    def ready(self):
        # Connect MenuItem post-save/post-delete signals for cache invalidation
        # (Task 20.2 — menu:branch:{branch_id} cache key with 30-second TTL).
        import apps.menus.signals  # noqa: F401
