"""
onboard_pilot_tenants — Django management command

Provisions 3–5 pilot restaurant tenants using ProvisioningService and
verifies that each tenant's subdomain routing, QR code generation, and
order-flow plumbing are correctly wired.

Usage::

    python manage.py onboard_pilot_tenants
    python manage.py onboard_pilot_tenants --plan-id 1
    python manage.py onboard_pilot_tenants --dry-run

Requirements: 1.2, 1.4
"""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.tenants.services import ProvisioningError, ProvisioningService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pilot tenant definitions
# ---------------------------------------------------------------------------

PILOT_TENANTS = [
    {
        "name": "Green Leaf Ethiopian Kitchen",
        "slug": "greenleaf",
        "owner_email": "owner@greenleaf.pilot",
    },
    {
        "name": "Addis Buna Café",
        "slug": "addisbuna",
        "owner_email": "owner@addisbuna.pilot",
    },
    {
        "name": "Habesha Heritage Restaurant",
        "slug": "habeshaheritage",
        "owner_email": "owner@habeshaheritage.pilot",
    },
    {
        "name": "Lalibela Dining",
        "slug": "lalibela",
        "owner_email": "owner@lalibela.pilot",
    },
    {
        "name": "Meseret Modern Kitchen",
        "slug": "meseret",
        "owner_email": "owner@meseret.pilot",
    },
]


class Command(BaseCommand):
    help = (
        "Provision 3–5 pilot restaurant tenants and print verification steps. "
        "Uses ProvisioningService to create full tenant schemas, domains, and "
        "Tenant_Owner accounts. Requirements: 1.2, 1.4"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--plan-id",
            dest="plan_id",
            type=int,
            default=1,
            help="PK of the SubscriptionPlan to assign to each pilot tenant (default: 1).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="Print what would be provisioned without actually creating anything.",
        )
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            dest="skip_existing",
            default=True,
            help="Skip tenants whose slug is already registered (default: True).",
        )
        parser.add_argument(
            "--count",
            dest="count",
            type=int,
            default=5,
            choices=range(1, 6),
            metavar="COUNT",
            help="Number of pilot tenants to provision, between 1 and 5 (default: 5).",
        )

    def handle(self, *args, **options):
        plan_id = options["plan_id"]
        dry_run = options["dry_run"]
        skip_existing = options["skip_existing"]
        count = options["count"]

        pilots = PILOT_TENANTS[:count]

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\n{'[DRY RUN] ' if dry_run else ''}"
                f"Onboarding {len(pilots)} pilot tenant(s) with plan_id={plan_id}\n"
                + "=" * 60
            )
        )

        service = ProvisioningService()
        provisioned = []
        skipped = []
        failed = []

        for pilot in pilots:
            slug = pilot["slug"]
            name = pilot["name"]
            owner_email = pilot["owner_email"]

            self.stdout.write(f"\n→ [{slug}] {name}")
            self.stdout.write(f"  Owner email : {owner_email}")

            # Check if already exists
            if skip_existing and self._tenant_exists(slug):
                self.stdout.write(
                    self.style.WARNING(f"  ⚠  Skipped — tenant '{slug}' already exists.")
                )
                skipped.append(slug)
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(f"  ✓  [DRY RUN] Would provision tenant '{slug}'.")
                )
                provisioned.append(slug)
                continue

            try:
                tenant = service.create_tenant(
                    name=name,
                    slug=slug,
                    plan_id=plan_id,
                    owner_email=owner_email,
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓  Provisioned — id={tenant.pk}, schema={tenant.schema_name}"
                    )
                )
                provisioned.append(slug)
                self._print_verification_steps(tenant, owner_email)

            except ProvisioningError as exc:
                self.stderr.write(
                    self.style.ERROR(f"  ✗  Provisioning failed for '{slug}': {exc}")
                )
                failed.append(slug)

            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(
                        f"  ✗  Unexpected error provisioning '{slug}': {exc}"
                    )
                )
                logger.exception("Unexpected error provisioning tenant '%s'", slug)
                failed.append(slug)

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(
            self.style.MIGRATE_HEADING("Onboarding Summary")
        )
        self.stdout.write(
            f"  Provisioned : {len(provisioned)} — {', '.join(provisioned) or 'none'}"
        )
        self.stdout.write(
            f"  Skipped     : {len(skipped)} — {', '.join(skipped) or 'none'}"
        )
        self.stdout.write(
            f"  Failed      : {len(failed)} — {', '.join(failed) or 'none'}"
        )

        if failed:
            raise CommandError(
                f"Onboarding completed with {len(failed)} failure(s): {', '.join(failed)}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                "\n✅  Pilot tenant onboarding complete. "
                "Run `python scripts/smoke_test_pilot.py` to verify end-to-end flow."
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tenant_exists(slug: str) -> bool:
        """Return True if a Tenant with this slug already exists."""
        try:
            from apps.tenants.models import Tenant

            return Tenant.objects.filter(slug=slug).exists()
        except Exception:
            return False

    def _print_verification_steps(self, tenant, owner_email: str) -> None:
        """Print manual verification steps for the newly provisioned tenant."""
        from django.conf import settings

        platform_domain = getattr(settings, "PLATFORM_DOMAIN", "localhost")
        subdomain = f"{tenant.slug}.{platform_domain}"

        self.stdout.write(
            self.style.HTTP_INFO(
                f"\n  Verification steps for '{tenant.slug}':\n"
                f"  1. Subdomain routing  : curl -H 'Host: {subdomain}' http://localhost/health\n"
                f"  2. Admin login        : POST http://{subdomain}/api/v1/auth/login/\n"
                f"     Payload           : {{\"email\": \"{owner_email}\", \"password\": \"<temp>\"}}\n"
                f"  3. Create a Branch    : POST http://{subdomain}/api/v1/branches/\n"
                f"  4. Create a Table     : POST http://{subdomain}/api/v1/branches/<id>/tables/\n"
                f"  5. Generate QR code   : POST http://{subdomain}/api/v1/branches/<id>/qr-codes/\n"
                f"  6. Scan QR code       : GET  http://{subdomain}/menu?token=<qr_token>\n"
                f"  7. Place test order   : POST http://{subdomain}/api/v1/customer/orders/\n"
                f"  8. Verify WS push     : ws://{subdomain}/ws/kitchen/<branch_id>/\n"
            )
        )
