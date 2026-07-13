#!/usr/bin/env python3
"""
reseed.py — Wipe all data except admin@demo.localhost, then seed fresh test data.

Run:
    docker compose exec web python scripts/reseed.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django
django.setup()

import uuid
from datetime import date, timedelta
from decimal import Decimal
from django.db import connection
from django.utils import timezone

ADMIN_EMAIL = "admin@demo.localhost"
ADMIN_PASSWORD = "admin1234"

# ── Helper: run raw SQL on a specific schema ──────────────────────────────

def set_schema(schema_name: str):
    with connection.cursor() as c:
        c.execute("SET search_path TO %s", [schema_name])


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 — CLEANUP
# ═══════════════════════════════════════════════════════════════════════════

print("=" * 60)
print("  PHASE 1: Cleanup (keeping only admin@demo.localhost)")
print("=" * 60)

set_schema("public")

from apps.authentication.models import User
from apps.tenants.models import Tenant, Domain
from apps.billing.models import SubscriptionPlan, TenantSubscription

# 1. Delete non-admin users
deleted_users, _ = User.objects.exclude(email=ADMIN_EMAIL).delete()
print(f"  Deleted {deleted_users} non-admin user(s)")

# 2. Ensure admin user exists and is active
admin, created = User.objects.get_or_create(
    email=ADMIN_EMAIL,
    defaults={"role": "Super_Admin", "is_active": True, "is_staff": True, "is_superuser": True},
)
if created:
    admin.set_password(ADMIN_PASSWORD)
    admin.save()
    print(f"  Created admin user {ADMIN_EMAIL}")
else:
    print(f"  Admin user {ADMIN_EMAIL} exists (pk={admin.pk})")

# 3. Delete all tenants — this drops their PostgreSQL schemas
tenant_count = Tenant.objects.count()
for t in Tenant.objects.all():
    print(f"  Dropping tenant '{t.slug}' (schema={t.schema_name})...")
    try:
        t.delete()
    except Exception as e:
        print(f"    WARNING: {e} (dropping schema manually)")
        with connection.cursor() as c:
            c.execute("DROP SCHEMA IF EXISTS %s CASCADE", [t.schema_name])
print(f"  Deleted {tenant_count} tenant(s)")

# 4. Delete orphaned public-schema data
from apps.tenants.models import Domain
Domain.objects.all().delete()
print("  Deleted all Domain records")

from apps.billing.models import SubscriptionPlan, TenantSubscription
TenantSubscription.objects.all().delete()
SubscriptionPlan.objects.all().delete()
print("  Deleted all Subscription plans")

with connection.cursor() as c:
    c.execute("SELECT to_regclass('public.tenants_platformauditlog')")
    if c.fetchone()[0]:
        c.execute("DELETE FROM tenants_platformauditlog")
        print("  Truncated PlatformAuditLog (public)")
    else:
        print("  No PlatformAuditLog table — nothing to truncate")

# ── Clean any shared-schema records that may linger
from apps.branches.models import Branch
try:
    Branch.objects.all().delete()
    print("  Deleted all Branch records (public schema)")
except Exception:
    print("  No Branch table in public schema — skipping")

from apps.branches.models import Table
try:
    Table.objects.all().delete()
except Exception:
    pass

from apps.branches.models import Room
try:
    Room.objects.all().delete()
except Exception:
    pass

# 5. Flush cache
try:
    from django.core.cache import cache
    cache.clear()
    print("  Flushed cache")
except Exception:
    pass


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2 — RESEED
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("  PHASE 2: Seeding fresh test data")
print("=" * 60)

# ── 2a. Subscription Plan ──────────────────────────────────────────
plan, _ = SubscriptionPlan.objects.get_or_create(
    name="Premium Plan",
    defaults={
        "max_branches": 10,
        "max_menu_items": 100,
        "max_staff_accounts": 20,
        "feature_flags": {"white_label": True, "advanced_analytics": True, "custom_domain": True},
        "price_etb": "999.00",
    },
)
print(f"  Plan: {plan.name} (pk={plan.pk})")

# ── 2b. Tenant ─────────────────────────────────────────────────────
from apps.tenants.services import ProvisioningService

DEMO_SLUG = "demo"
service = ProvisioningService()
try:
    tenant = service.create_tenant(
        name="Demo Restaurant",
        slug=DEMO_SLUG,
        plan_id=plan.pk,
        owner_email="owner@demo.localhost",
    )
    print(f"  Created tenant '{tenant.name}' (schema={tenant.schema_name})")
except Exception as e:
    tenant = Tenant.objects.get(slug=DEMO_SLUG)
    print(f"  Tenant exists: {tenant.slug} ({e})")

# Ensure localhost domain
from apps.tenants.models import Domain
Domain.objects.get_or_create(domain="localhost", tenant=tenant, defaults={"is_primary": False})
Domain.objects.get_or_create(domain="demo.localhost", tenant=tenant, defaults={"is_primary": True})
print("  Domain: localhost, demo.localhost")

# ── 2c. Branch + Tables + Rooms ────────────────────────────────────
connection.set_tenant(tenant)

from apps.branches.models import Branch, Table, Room

branch = Branch.objects.create(
    name="Main Branch",
    address="123 Bole Road, Addis Ababa",
    phone="+251911000001",
    email="branch@demo.localhost",
    opening_hours={
        "monday": {"open": "08:00", "close": "22:00"},
        "tuesday": {"open": "08:00", "close": "22:00"},
        "wednesday": {"open": "08:00", "close": "22:00"},
        "thursday": {"open": "08:00", "close": "22:00"},
        "friday": {"open": "08:00", "close": "23:00"},
        "saturday": {"open": "09:00", "close": "23:00"},
        "sunday": {"open": "09:00", "close": "21:00"},
    },
    timezone="Africa/Addis_Ababa",
    currency="ETB",
    is_active=True,
)
print(f"  Branch: {branch.name} (id={branch.pk})")

# Tables
tables = {}
for num in range(1, 7):
    t = Table.objects.create(branch=branch, number=str(num), seat_count=4 if num <= 4 else 6)
    tables[num] = t
    print(f"    Table #{t.number} (seats={t.seat_count})")

# Rooms
room1 = Room.objects.create(branch=branch, name="VIP Room", capacity=8)
room2 = Room.objects.create(branch=branch, name="Family Room", capacity=6)
print(f"    Room: {room1.name} (cap={room1.capacity})")
print(f"    Room: {room2.name} (cap={room2.capacity})")

# ── 2d. Staff Users ────────────────────────────────────────────────
# Users must be created in the public schema because authentication is a
# SHARED_APP. Switch to public, create users, then switch back to tenant.
set_schema("public")

staff_users = {}

staff_data = [
    ("manager@demo.localhost", "Branch_Manager", "Manager123!"),
    ("reception@demo.localhost", "Receptionist", "Reception123!"),
    ("kitchen@demo.localhost", "Kitchen_Staff", "Kitchen123!"),
]

for email, role, pw in staff_data:
    u, created = User.objects.get_or_create(
        email=email,
        defaults={"role": role, "branch": None, "is_active": True},
    )
    if created:
        u.set_password(pw)
        u.save()
    else:
        u.role = role
        u.branch = None
        u.save()
    staff_users[role] = u
    print(f"  User: {email} / {pw}  role={role}")

# Ensure tenant owner exists
owner_email = "owner@demo.localhost"
owner, _ = User.objects.get_or_create(
    email=owner_email,
    defaults={"role": "Tenant_Owner", "branch": None, "is_active": True},
)
if not owner.password or owner.password.startswith("!"):
    owner.set_password("Owner123!")
    owner.save()
print(f"  User: {owner_email} / Owner123!  role=Tenant_Owner")

# Switch back to tenant for remaining tenant-scoped data
connection.set_tenant(tenant)

# ── 2e. Menu Categories & Items ────────────────────────────────────
from apps.menus.models import Category, MenuItem, NutritionProfile, Recipe, Ingredient
from apps.inventory.models import InventoryItem, Supplier

# Categories
cat_appetizers = Category.objects.create(name="Appetizers", branch=branch)
cat_mains = Category.objects.create(name="Main Course", branch=branch)
cat_drinks = Category.objects.create(name="Beverages", branch=branch)
cat_desserts = Category.objects.create(name="Desserts", branch=branch)
print("  Categories: Appetizers, Main Course, Beverages, Desserts")

# Suppliers
supplier1 = Supplier.objects.create(name="Ethio Supply Co.", branch=branch, contact="+251911100001")
supplier2 = Supplier.objects.create(name="Addis Fresh Farms", branch=branch, contact="+251911100002")
print(f"  Suppliers: {supplier1.name}, {supplier2.name}")

# Inventory Items
inventory_items = {}
inv_data = [
    ("Berbere Spice", "Spices", 50, "kg", 120.00, 10, supplier1),
    ("Teff Flour", "Grains", 200, "kg", 45.00, 50, supplier1),
    ("Chicken (whole)", "Poultry", 30, "kg", 250.00, 10, supplier2),
    ("Beef (boneless)", "Meat", 40, "kg", 350.00, 10, supplier2),
    ("Onions", "Vegetables", 100, "kg", 30.00, 20, supplier2),
    ("Tomatoes", "Vegetables", 80, "kg", 40.00, 20, supplier2),
    ("Vegetable Oil", "Cooking", 100, "L", 80.00, 25, supplier1),
    ("Coffee Beans", "Beverages", 25, "kg", 600.00, 5, supplier1),
    ("Honey", "Condiments", 20, "L", 150.00, 5, supplier1),
    ("Chickpeas", "Grains", 60, "kg", 55.00, 15, supplier2),
    ("Injera (pack)", "Bakery", 100, "pcs", 15.00, 30, supplier1),
    ("Butter", "Dairy", 15, "kg", 200.00, 5, supplier2),
]
for name, cat, qty, unit, price, threshold, supp in inv_data:
    item = InventoryItem.objects.create(
        branch=branch,
        name=name,
        category=cat,
        quantity=qty,
        unit=unit,
        purchase_price=Decimal(str(price)),
        supplier=supp,
        expiration_date=date.today() + timedelta(days=90),
        reorder_threshold=Decimal(str(threshold)),
    )
    inventory_items[name] = item
print(f"  Inventory: {len(inv_data)} items")

# Menu Items
menu_items = {}
menu_data = [
    ("Doro Wat", "Traditional Ethiopian chicken stew in spicy berbere sauce, served with injera.",
     185.00, 25, "available", ["halal", "high_protein", "spicy"], cat_mains),
    ("Tibs", "Sautéed beef or lamb with onions, tomatoes, and rosemary.",
     210.00, 20, "available", ["halal", "high_protein"], cat_mains),
    ("Shiro", "Creamy chickpea stew, a vegetarian Ethiopian classic.",
     120.00, 15, "available", ["vegetarian", "vegan", "halal"], cat_mains),
    ("Kitfo", "Ethiopian steak tartare seasoned with mitmita and clarified butter.",
     280.00, 15, "seasonal", ["halal", "high_protein", "spicy"], cat_mains),
    ("Sambusa", "Crispy pastry filled with spiced lentils or meat.",
     120.00, 10, "available", ["vegetarian", "spicy"], cat_appetizers),
    ("Tej", "Traditional Ethiopian honey wine (mead).",
     80.00, 2, "available", ["vegan"], cat_drinks),
    ("Buna (Ethiopian Coffee)", "Freshly brewed traditional Ethiopian coffee ceremony style.",
     45.00, 5, "available", ["vegan", "halal"], cat_drinks),
    ("Juice", "Fresh fruit juice of the day.",
     80.00, 3, "available", ["vegan", "vegetarian"], cat_drinks),
    ("Tea", "Ethiopian spiced tea with cinnamon and cardamom.",
     35.00, 3, "unavailable", ["vegan"], cat_drinks),
    ("Kicha", "Ethiopian layered bread (similar to pancake), often served with honey.",
     80.00, 10, "available", ["vegetarian"], cat_desserts),
]

for name, desc, price, prep, status, tags, cat in menu_data:
    item = MenuItem.objects.create(
        branch=branch,
        name=name,
        description=desc,
        price=Decimal(str(price)),
        prep_time_minutes=prep,
        status=status,
        dietary_tags=tags,
    )
    item.categories.add(cat)
    menu_items[name] = item
print(f"  Menu: {len(menu_data)} items")

# Recipes (for items that need cooking)
recipe_data = [
    ("Doro Wat", "1. Marinate chicken in lemon juice and mitmita for 30min.\n"
                 "2. Sauté onions in niter kibbeh until golden.\n"
                 "3. Add berbere paste and cook for 10min.\n"
                 "4. Add chicken and simmer for 45min until tender.\n"
                 "5. Season with salt and serve with injera.", 45,
     [("Chicken (whole)", Decimal("0.5"), "kg"),
      ("Berbere Spice", Decimal("0.05"), "kg"),
      ("Onions", Decimal("0.3"), "kg"),
      ("Vegetable Oil", Decimal("0.1"), "L")]),
    ("Tibs", "1. Cut beef into bite-sized cubes.\n"
             "2. Sauté onions in butter until translucent.\n"
             "3. Add beef and sear on high heat for 3-5min.\n"
             "4. Add diced tomatoes, rosemary, and salt.\n"
             "5. Cook for another 5min and serve.", 20,
     [("Beef (boneless)", Decimal("0.3"), "kg"),
      ("Onions", Decimal("0.2"), "kg"),
      ("Tomatoes", Decimal("0.15"), "kg"),
      ("Butter", Decimal("0.05"), "kg")]),
    ("Shiro", "1. Sauté finely chopped onions in oil until golden.\n"
              "2. Add berbere spice and cook 2min.\n"
              "3. Mix chickpea flour with water to form a paste.\n"
              "4. Add paste to the pan, stirring continuously.\n"
              "5. Simmer for 20min, adding water as needed.", 15,
     [("Chickpeas", Decimal("0.2"), "kg"),
      ("Onions", Decimal("0.15"), "kg"),
      ("Vegetable Oil", Decimal("0.05"), "L"),
      ("Berbere Spice", Decimal("0.02"), "kg")]),
    ("Buna (Ethiopian Coffee)", "1. Wash green coffee beans.\n"
                                "2. Roast beans over charcoal until dark.\n"
                                "3. Grind beans fine with mortar and pestle.\n"
                                "4. Boil water in a jebena (clay pot).\n"
                                "5. Add grounds, let settle, and serve.", 5,
     [("Coffee Beans", Decimal("0.02"), "kg")]),
]

for name, method, cook_time, ingredients in recipe_data:
    mi = menu_items[name]
    recipe = Recipe.objects.create(menu_item=mi, method=method, cook_time_minutes=cook_time)
    for ing_name, qty, unit in ingredients:
        inv_item = inventory_items.get(ing_name)
        if inv_item:
            Ingredient.objects.create(
                recipe=recipe,
                inventory_item=inv_item,
                quantity=qty,
                unit=unit,
            )
print(f"  Recipes: {len(recipe_data)} with ingredient links")

# ── 2f. QR Codes ───────────────────────────────────────────────────
from apps.qr.models import QRCode

qr_table1 = QRCode.objects.create(table=tables[1], is_active=True)
QRCode.objects.create(table=tables[3], is_active=True)
QRCode.objects.create(table=tables[5], is_active=True)
print(f"  QR codes created for Tables 1, 3, 5 (e.g. token={qr_table1.token})")

# ── 2g. Sample Orders in Various Statuses ──────────────────────────
from apps.orders.models import Order as OrderModel, OrderItem as OrderItemModel

# Helper to create an order
def create_order(table, items_data, status="confirmed", minutes_ago=10):
    placed = timezone.now() - timedelta(minutes=minutes_ago)
    total = sum(qty * Decimal(str(price)) for _, qty, price in items_data)
    order = OrderModel.objects.create(
        branch=branch,
        table=table,
        status=status,
        customer_name="",
        total_amount=total,
        placed_at=placed,
    )
    for name, qty, price in items_data:
        mi = menu_items.get(name)
        if mi:
            OrderItemModel.objects.create(
                order=order, menu_item=mi,
                quantity=qty, unit_price=Decimal(str(price)),
                special_instructions="",
            )
    return order

# 1 served order (yesterday)
create_order(tables[2],
    [("Doro Wat", 2, 185.00), ("Injera (pack)", 2, 15.00)],
    status="served", minutes_ago=1440)

# 1 ready order
create_order(tables[4],
    [("Tibs", 1, 210.00), ("Buna (Ethiopian Coffee)", 2, 45.00)],
    status="ready", minutes_ago=15)

# 1 preparing order
create_order(tables[1],
    [("Shiro", 1, 120.00), ("Sambusa", 2, 120.00)],
    status="preparing", minutes_ago=8)

# 1 received order
create_order(tables[3],
    [("Kicha", 1, 80.00), ("Tej", 2, 80.00)],
    status="received", minutes_ago=3)

# 3 confirmed orders
create_order(tables[5],
    [("Doro Wat", 1, 185.00), ("Buna (Ethiopian Coffee)", 1, 45.00)],
    status="confirmed", minutes_ago=1)
create_order(tables[6],
    [("Tibs", 2, 210.00), ("Juice", 2, 80.00)],
    status="confirmed", minutes_ago=2)
create_order(tables[1],
    [("Sambusa", 3, 120.00), ("Tej", 1, 80.00)],
    status="confirmed", minutes_ago=0.5)

# 1 cancelled order
create_order(tables[2],
    [("Kitfo", 1, 280.00)],
    status="cancelled", minutes_ago=60)

print(f"  Orders: 1 served, 1 ready, 1 preparing, 1 received, 3 confirmed, 1 cancelled")

# ── 2h. Sample Expenses ────────────────────────────────────────────
from apps.expenses.models import Expense

expenses_data = [
    ("Staff Salaries", "payroll", "80000.00", 1),
    ("Electricity Bill", "utilities", "15000.00", 1),
    ("Water Bill", "utilities", "5000.00", 1),
    ("Cleaning Supplies", "miscellaneous", "3000.00", 1),
    ("Fresh Vegetables", "food_purchases", "8500.00", 3),
    ("Meat & Poultry", "food_purchases", "12000.00", 3),
]

for desc, cat, amount, days_ago in expenses_data:
    Expense.objects.create(
        branch=branch,
        description=desc,
        category=cat,
        amount=Decimal(amount),
        date_incurred=date.today() - timedelta(days=days_ago),
        notes="",
    )
print(f"  Expenses: {len(expenses_data)} records")

# ── 2i. Income Records ─────────────────────────────────────────────
from apps.financials.models import Income

served_order = OrderModel.objects.filter(status="served").first()
if served_order:
    Income.objects.create(
        branch=branch,
        source="order",
        order=served_order,
        amount=served_order.total_amount,
        description=f"Order {served_order.order_number}",
        date=date.today(),
    )

# Also add some extra income
Income.objects.create(
    branch=branch, source="catering",
    amount=Decimal("5000.00"), description="Event catering",
    date=date.today() - timedelta(days=2),
)
Income.objects.create(
    branch=branch, source="other",
    amount=Decimal("2500.00"), description="Gift shop sales",
    date=date.today() - timedelta(days=1),
)
print("  Income: 3 records")

# ── 2j. TenantConfig ───────────────────────────────────────────────
from apps.whitelabel.models import TenantConfig

TenantConfig.objects.get_or_create(
    restaurant_name="Demo Restaurant",
    defaults={
        "primary_color": "#8B3A2A",
        "secondary_color": "#5D7061",
        "default_language": "en",
        "currency": "ETB",
        "timezone": "Africa/Addis_Ababa",
        "tax_rate": Decimal("15.00"),
        "tax_label": "VAT",
        "service_charge_pct": Decimal("0.00"),
    },
)
print("  TenantConfig: Demo Restaurant")


# ═══════════════════════════════════════════════════════════════════════════
# DONE
# ═══════════════════════════════════════════════════════════════════════════

set_schema("public")

print("\n" + "=" * 60)
print("  ✅ Reseed complete!")
print("=" * 60)
print(f"""
  Users:
    admin@demo.localhost    / admin1234       → Super_Admin
    owner@demo.localhost    / Owner123!       → Tenant_Owner
    manager@demo.localhost  / Manager123!     → Branch_Manager
    reception@demo.localhost / Reception123!  → Receptionist
    kitchen@demo.localhost  / Kitchen123!     → Kitchen_Staff

  Tenant: Demo Restaurant (schema: tenant_demo)
  Branch: Main Branch (6 tables, 2 rooms)
  Menu:   10 items (available/unavailable/seasonal) with 4 recipes + inventory links
  Orders: 7 (served, ready, preparing, received, 3x confirmed, cancelled)
  Exp:    6 records  |  Income: 3 records

  QR Scan URL: http://localhost:8000/qr/scan/{qr_table1.token}/
  Staff URL:   http://localhost:8000/staff/
  Admin URL:   http://localhost:8000/admin/
""")
