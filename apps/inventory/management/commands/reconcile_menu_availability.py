"""
Management command to reconcile MenuItem availability against inventory.

Usage:
    python manage.py reconcile_menu_availability

Scans all tenants and branches, updating MenuItem status based on current
inventory stock levels.  Intended to be run on a schedule (e.g. cron)
and complements the Celery Beat periodic task of the same name.

Note: the Celery Beat task runs inside a tenant context; this command
iterates all tenants explicitly.
"""

from django.core.management.base import BaseCommand
from django.db import connection

from apps.inventory.tasks import reconcile_menu_availability


class Command(BaseCommand):
    help = "Reconcile MenuItem availability against current inventory stock levels."

    def handle(self, *args, **options):
        from apps.tenants.models import Tenant

        tenants = list(Tenant.objects.all())
        if not tenants:
            self.stdout.write("No tenants found.")
            return

        for tenant in tenants:
            connection.set_tenant(tenant)
            self.stdout.write(f"  Reconcile [{tenant.schema_name}] … ", ending="")
            try:
                reconcile_menu_availability()
                self.stdout.write(self.style.SUCCESS("OK"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"ERROR: {e}"))

        connection.set_schema_to_public()
        self.stdout.write(self.style.SUCCESS("Done."))
