"""
Bootstrap a fresh deployment — creates first tenant, domain, admin user, and
default branch/table/QR code.  Idempotent (skips if Tenant exists).
"""

import os

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Bootstrap a fresh deployment with initial tenant, admin, and defaults."

    def handle(self, *args, **options):
        from apps.authentication.models import User
        from apps.billing.models import SubscriptionPlan
        from apps.tenants.models import Tenant, Domain

        if Tenant.objects.exists():
            self.stdout.write("Already bootstrapped — skipping.")
            return

        self.stdout.write("Bootstrapping fresh deployment …")

        # 1. Create subscription plan (shared/public schema)
        plan, _ = SubscriptionPlan.objects.get_or_create(
            name="Default Plan",
            defaults={
                "max_branches": 10,
                "max_menu_items": 100,
                "max_staff_accounts": 20,
                "feature_flags": {
                    "white_label": True,
                    "advanced_analytics": True,
                    "custom_domain": True,
                },
                "price_etb": "0.00",
            },
        )
        self.stdout.write(f"  Plan: {plan.name}")

        # 2. Create tenant using ProvisioningService
        from apps.tenants.services import ProvisioningService

        service = ProvisioningService()
        from django.utils.crypto import get_random_string
        slug = "restaurant-" + get_random_string(6).lower()

        tenant = service.create_tenant(
            name="My Restaurant",
            slug=slug,
            plan_id=plan.pk,
            owner_email="admin@demo.localhost",
        )
        self.stdout.write(f"  Tenant: {tenant.name} (schema={tenant.schema_name})")

        # 3. Override domain with Render hostname (or localhost)
        render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "localhost")
        # Update the primary domain — replace whatever create_tenant set
        domain = Domain.objects.get(tenant=tenant, is_primary=True)
        domain.domain = render_host
        domain.save(update_fields=["domain"])
        self.stdout.write(f"  Domain set to: {domain.domain}")

        # Also create a localhost domain for dev
        Domain.objects.get_or_create(
            domain="localhost",
            tenant=tenant,
            defaults={"is_primary": False},
        )

        # 4. Create a Super_Admin in public schema
        connection.set_schema_to_public()
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@demo.localhost")
        admin_password = os.environ.get("ADMIN_PASSWORD", "admin1234")

        admin, created = User.objects.get_or_create(
            email=admin_email,
            defaults={
                "role": "Super_Admin",
                "is_active": True,
                "is_staff": True,
            },
        )
        if created:
            admin.set_password(admin_password)
            admin.save()
            self.stdout.write(f"  Super_Admin: {admin_email} / {admin_password}")
        else:
            self.stdout.write(f"  Super_Admin: {admin_email} (already exists)")

        # 5. Set known password for the tenant owner too
        connection.set_tenant(tenant)
        owner = User.objects.filter(role="Tenant_Owner").first()
        if owner:
            owner.set_password("admin1234")
            owner.save(update_fields=["password"])
            self.stdout.write(f"  Tenant_Owner password set: admin@demo.localhost / admin1234")

        # 6. Create default branch, tables, QR codes in tenant context
        from apps.branches.models import Branch
        branch, _ = Branch.objects.get_or_create(
            name="Main Branch",
            defaults={
                "address": "Bole Road",
                "phone": "+251-911-000000",
                "email": "info@myrestaurant.com",
                "is_active": True,
            },
        )
        self.stdout.write(f"  Branch: {branch.name}")

        from apps.branches.models import Table
        from apps.qr.models import QRCode
        for num in ("1", "2", "3"):
            tbl, _ = Table.objects.get_or_create(
                branch=branch, number=num,
                defaults={"seat_count": 6 if num == "2" else 4},
            )
            if num in ("1", "3"):
                QRCode.objects.get_or_create(table=tbl, defaults={"is_active": True})
        self.stdout.write("  Tables #1, #2, #3 created (QR codes for #1 and #3)")

        from apps.branches.models import Room
        Room.objects.get_or_create(
            branch=branch, name="VIP Room",
            defaults={"capacity": 10},
        )

        # Reset to public schema
        connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Bootstrap complete."))
        self.stdout.write(f"  Staff login: {admin_email} / {admin_password}")
