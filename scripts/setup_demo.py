#!/usr/bin/env python3
"""
setup_demo.py — One-shot demo environment setup.

Run inside the web container:
    python scripts/setup_demo.py

What it does:
  1. Creates a default SubscriptionPlan (if none exist)
  2. Provisions a demo tenant with domain "localhost"
  3. Creates a superuser for the Django admin
  4. Creates a demo Branch + Table inside the tenant
  5. Generates a QR code for Table 1
  6. Prints the URLs to visit

Requirements: 1.2, 1.4
"""

import os
import sys
import django

# Ensure manage.py's directory is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

import datetime
from django.db import connection, transaction

print("\n" + "=" * 60)
print("  Restaurant Platform — Demo Setup")
print("=" * 60 + "\n")


# ── Step 1: Create a SubscriptionPlan ────────────────────────────
print("→ Step 1: SubscriptionPlan")
from apps.billing.models import SubscriptionPlan

plan, created = SubscriptionPlan.objects.get_or_create(
    name="Demo Plan",
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
print(f"  {'Created' if created else 'Exists'}: SubscriptionPlan id={plan.pk} '{plan.name}'")


# ── Step 2: Provision demo tenant on localhost ────────────────────
print("\n→ Step 2: Demo Tenant")
from apps.tenants.models import Tenant, Domain
from apps.tenants.services import ProvisioningService, TenantAlreadyExists

DEMO_SLUG = "demo"
DEMO_OWNER_EMAIL = "owner@demo.localhost"

existing_tenant = Tenant.objects.filter(slug=DEMO_SLUG).first()
if existing_tenant:
    print(f"  Exists: Tenant '{DEMO_SLUG}' (id={existing_tenant.pk})")
    tenant = existing_tenant
else:
    print(f"  Provisioning new tenant '{DEMO_SLUG}'...")
    service = ProvisioningService()
    try:
        tenant = service.create_tenant(
            name="Demo Restaurant",
            slug=DEMO_SLUG,
            plan_id=plan.pk,
            owner_email=DEMO_OWNER_EMAIL,
        )
        print(f"  Created: Tenant id={tenant.pk}, schema={tenant.schema_name}")
    except TenantAlreadyExists:
        tenant = Tenant.objects.get(slug=DEMO_SLUG)
        print(f"  Already exists: Tenant id={tenant.pk}")
    except Exception as e:
        # If provisioning failed mid-way, the Tenant record may now exist
        tenant = Tenant.objects.filter(slug=DEMO_SLUG).first()
        if tenant:
            print(f"  Recovered partial tenant id={tenant.pk}: {e}")
            # Ensure it's active
            if not tenant.is_active:
                tenant.is_active = True
                tenant.save(update_fields=["is_active"])
        else:
            raise


# Make sure "localhost" domain points to the demo tenant
localhost_domain = Domain.objects.filter(domain="localhost").first()
if not localhost_domain:
    Domain.objects.create(domain="localhost", tenant=tenant, is_primary=False)
    print("  Added domain: localhost → demo tenant")
else:
    if localhost_domain.tenant_id != tenant.pk:
        localhost_domain.tenant = tenant
        localhost_domain.save()
        print(f"  Updated domain 'localhost' to point to demo tenant")
    else:
        print("  Domain 'localhost' already → demo tenant")


# ── Step 3: Superuser ────────────────────────────────────────────
print("\n→ Step 3: Django Superuser (public schema)")

# Ensure we're in the public schema using raw SQL
with connection.cursor() as cursor:
    cursor.execute("SET search_path TO public")

from apps.authentication.models import User

SUPERUSER_EMAIL = "admin@demo.localhost"
SUPERUSER_PASSWORD = "admin1234"

if User.objects.filter(email=SUPERUSER_EMAIL).exists():
    print(f"  Exists: superuser {SUPERUSER_EMAIL}")
    superuser = User.objects.get(email=SUPERUSER_EMAIL)
else:
    superuser = User.objects.create_superuser(
        email=SUPERUSER_EMAIL,
        password=SUPERUSER_PASSWORD,
    )
    print(f"  Created: superuser {SUPERUSER_EMAIL} / {SUPERUSER_PASSWORD}")


# ── Step 4: Branch + Table inside tenant schema ──────────────────
print("\n→ Step 4: Demo Branch + Table (tenant schema)")
connection.set_tenant(tenant)

from apps.branches.models import Branch, Table

branch, b_created = Branch.objects.get_or_create(
    name="Main Branch",
    defaults={
        "address": "123 Bole Road, Addis Ababa",
        "phone": "+251911000001",
        "email": "branch@demo.localhost",
        "opening_hours": {
            "monday": {"open": "08:00", "close": "22:00"},
            "tuesday": {"open": "08:00", "close": "22:00"},
            "wednesday": {"open": "08:00", "close": "22:00"},
            "thursday": {"open": "08:00", "close": "22:00"},
            "friday": {"open": "08:00", "close": "23:00"},
            "saturday": {"open": "09:00", "close": "23:00"},
            "sunday": {"open": "09:00", "close": "21:00"},
        },
        "timezone": "Africa/Addis_Ababa",
        "currency": "ETB",
    },
)
print(f"  {'Created' if b_created else 'Exists'}: Branch '{branch.name}' id={branch.pk}")

table, t_created = Table.objects.get_or_create(
    branch=branch,
    number="1",
    defaults={"seat_count": 4},
)
print(f"  {'Created' if t_created else 'Exists'}: Table #{table.number} id={table.pk}")


# ── Step 5: QR Code ──────────────────────────────────────────────
print("\n→ Step 5: QR Code for Table 1")
from apps.qr.models import QRCode

qr_code = QRCode.objects.filter(table=table, is_active=True).first()
if not qr_code:
    qr_code = QRCode.objects.create(table=table, is_active=True)
    print(f"  Created: QR code token={qr_code.token}")
else:
    print(f"  Exists: QR code token={qr_code.token}")


# ── Step 6: Sample Menu Items ────────────────────────────────────
print("\n→ Step 6: Sample Menu Items")
from apps.menus.models import MenuItem, Category

cat_mains, _ = Category.objects.get_or_create(name="Main Course", branch=branch)
cat_drinks, _ = Category.objects.get_or_create(name="Beverages", branch=branch)

sample_items = [
    {
        "name": "Doro Wat",
        "description": "Traditional Ethiopian chicken stew in spicy berbere sauce, served with injera.",
        "price": "185.00",
        "prep_time_minutes": 25,
        "status": "available",
        "dietary_tags": ["halal", "high_protein", "spicy"],
        "category": cat_mains,
    },
    {
        "name": "Tibs",
        "description": "Sautéed beef or lamb with onions, tomatoes, and rosemary.",
        "price": "210.00",
        "prep_time_minutes": 20,
        "status": "available",
        "dietary_tags": ["halal", "high_protein"],
        "category": cat_mains,
    },
    {
        "name": "Shiro",
        "description": "Creamy chickpea stew, a vegetarian Ethiopian classic.",
        "price": "120.00",
        "prep_time_minutes": 15,
        "status": "available",
        "dietary_tags": ["vegetarian", "vegan", "halal"],
        "category": cat_mains,
    },
    {
        "name": "Tej",
        "description": "Traditional Ethiopian honey wine (mead).",
        "price": "80.00",
        "prep_time_minutes": 2,
        "status": "available",
        "dietary_tags": ["vegan"],
        "category": cat_drinks,
    },
    {
        "name": "Buna (Ethiopian Coffee)",
        "description": "Freshly brewed traditional Ethiopian coffee ceremony style.",
        "price": "45.00",
        "prep_time_minutes": 5,
        "status": "available",
        "dietary_tags": ["vegan", "halal"],
        "category": cat_drinks,
    },
]

created_count = 0
for item_data in sample_items:
    cat = item_data.pop("category")
    item, created = MenuItem.objects.get_or_create(
        branch=branch,
        name=item_data["name"],
        defaults={**item_data},
    )
    item.categories.add(cat)
    if created:
        created_count += 1

print(f"  {created_count} new menu items created ({len(sample_items) - created_count} already exist)")


# ── Step 7: Restore public schema + print summary ────────────────
with connection.cursor() as cursor:
    cursor.execute("SET search_path TO public")

print("\n" + "=" * 60)
print("  ✅  Demo setup complete!")
print("=" * 60)
print(f"""
🌐 App URLs (open in your browser):
   Customer Menu  →  http://localhost:8000/customer/menu/
                     (after scanning QR or using URL below)

   QR Scan URL    →  http://localhost:8000/qr/scan/{qr_code.token}/
                     (simulates scanning the QR code with a phone)

   Django Admin   →  http://localhost:8000/admin/
   Admin login    →  {SUPERUSER_EMAIL}  /  {SUPERUSER_PASSWORD}

   API Docs       →  http://localhost:8000/api/docs/

   Tenant Owner   →  {DEMO_OWNER_EMAIL}
                     (use password reset to set a password)

📋 Quick API test:
   curl http://localhost:8000/qr/scan/{qr_code.token}/
   # Then: curl http://localhost:8000/customer/menu/ -H 'Cookie: <sessionid>'
""")
