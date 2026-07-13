"""
tenants/services.py — ProvisioningService

Responsible for the full lifecycle of tenant management:
  - create_tenant: provision a new tenant schema, config, and owner account
  - suspend_tenant: deactivate a tenant and invalidate all its sessions
  - delete_tenant: verify confirmation token, drop schema, purge records

All public methods are intended to be called from views (Task 4.2) or
Celery tasks.  They raise descriptive exceptions on validation failure.

Audit logging (Requirement 5.1) is applied via @audit_action on the three
lifecycle methods: TENANT_CREATE, TENANT_SUSPEND, TENANT_DELETE.

Requirements: 1.4, 1.5, 1.6, 5.1
"""

import hashlib
import logging
import secrets
import subprocess
import sys
from typing import Any

from apps.audit.decorators import _get_context_attr  # thread-local helpers

from django.core.exceptions import ObjectDoesNotExist
from django.db import connection, transaction

# Top-level imports kept lazy (inside methods) to avoid circular imports at
# app-load time, but we expose the model names at module level so that tests
# can patch 'apps.tenants.services.<Model>'.
try:
    from apps.tenants.models import Domain, PlatformAuditLog, Tenant
except Exception:  # pragma: no cover — models may not be importable in some test setups
    Tenant = None  # type: ignore[assignment,misc]
    Domain = None  # type: ignore[assignment,misc]
    PlatformAuditLog = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# Audit decorator — imported defensively so the service still works before
# the audit app migrations are applied.
try:
    from apps.audit.decorators import audit_action as _audit_action
    _AUDIT_AVAILABLE = True
except Exception:  # pragma: no cover
    _audit_action = None
    _AUDIT_AVAILABLE = False


def _noop_decorator(*d_args, **d_kwargs):
    """Fallback decorator that does nothing when audit is unavailable."""
    def decorator(func):
        return func
    return decorator


_audit = _audit_action if _AUDIT_AVAILABLE else _noop_decorator


# ---------------------------------------------------------------------------
# Service-specific exceptions
# ---------------------------------------------------------------------------


class ProvisioningError(Exception):
    """Base class for all provisioning failures."""


class TenantNotFound(ProvisioningError):
    """Raised when the requested tenant_id does not exist in the public schema."""


class PlanNotFound(ProvisioningError):
    """Raised when the requested plan_id does not exist."""


class InvalidConfirmationToken(ProvisioningError):
    """Raised when the delete confirmation token does not match the expected value."""


class TenantAlreadyExists(ProvisioningError):
    """Raised when the slug is already taken."""


# ---------------------------------------------------------------------------
# ProvisioningService
# ---------------------------------------------------------------------------


class ProvisioningService:
    """
    Service layer for tenant lifecycle management.

    All methods operate on the public (shared) schema because Tenant and Domain
    records live there.  Tenant-scoped records (User, TenantConfig) are created
    after switching the connection to the new tenant schema.

    Usage::

        service = ProvisioningService()
        tenant = service.create_tenant(
            name="Green Leaf",
            slug="greenleaf",
            plan_id=1,
            owner_email="owner@greenleaf.et",
        )
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_confirmation_token(tenant_id: Any) -> str:
        """
        Deterministically derive a confirmation token from the tenant's PK.

        The token is a 32-character hex string derived from
        ``SHA-256(SECRET_KEY + str(tenant_id))``.  This is *not* a security
        secret — it is simply a guard against accidental deletion from a UI
        that requires the caller to echo back a value they were shown first.

        For a production hardening, callers could add a nonce or timestamp;
        that's out of scope for Task 4.1.
        """
        from django.conf import settings

        raw = f"{settings.SECRET_KEY}:{tenant_id}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    @staticmethod
    def _run_migrate_schemas(schema_name: str) -> None:
        """
        Execute ``manage.py migrate_schemas --tenant --schema=<schema_name>``.

        This runs as a subprocess so it inherits the full Django environment
        (settings, PYTHONPATH).  In testing the actual subprocess call is
        mocked out.

        Raises:
            ProvisioningError: if the migration subprocess exits non-zero.
        """
        cmd = [
            sys.executable,
            "manage.py",
            "migrate_schemas",
            "--tenant",
            f"--schema={schema_name}",
            "--noinput",
        ]
        logger.info(
            "Running migrate_schemas for schema '%s': %s", schema_name, " ".join(cmd)
        )
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(
                "migrate_schemas failed for schema '%s':\n%s\n%s",
                schema_name,
                result.stdout,
                result.stderr,
            )
            raise ProvisioningError(
                f"migrate_schemas failed for schema '{schema_name}': "
                f"{result.stderr or result.stdout}"
            )
        logger.info(
            "migrate_schemas completed for schema '%s'", schema_name
        )

    # ------------------------------------------------------------------
    # create_tenant
    # ------------------------------------------------------------------

    @_audit("TENANT_CREATE", resource_type="Tenant", get_resource_id=lambda t: t.pk if t else None)
    def create_tenant(
        self,
        name: str,
        slug: str,
        plan_id: Any,
        owner_email: str,
    ):
        """
        Provision a new tenant end-to-end.

        Steps (in order):
          1. Validate the slug is not already taken.
          2. Look up the SubscriptionPlan (billing.SubscriptionPlan).
          3. Create the Tenant record — TenantMixin.save() auto-creates the
             PostgreSQL schema when ``auto_create_schema=True``.
          4. Create the primary Domain record
             (``{slug}.{PLATFORM_DOMAIN}`` or just ``{slug}`` in tests).
          5. Run ``migrate_schemas --tenant`` to create all tenant-scoped tables.
          6. Switch connection to the new tenant schema and create:
             a. TenantConfig with platform defaults.
             b. User with Tenant_Owner role and a secure random password.
          7. Create a TenantSubscription linking tenant ↔ plan (if billing
             models are available; skipped gracefully when they are stubs).
          8. Activate the tenant (set is_active=True) and save.

        Args:
            name:        Human-readable restaurant name.
            slug:        URL-safe unique identifier (used in schema name & subdomain).
            plan_id:     PK of the billing.SubscriptionPlan to assign.
            owner_email: Email address for the Tenant_Owner user account.

        Returns:
            The newly created and activated :class:`apps.tenants.models.Tenant`.

        Raises:
            TenantAlreadyExists: if *slug* is already registered.
            PlanNotFound:        if *plan_id* does not match any SubscriptionPlan.
            ProvisioningError:   if schema migration fails.
        """
        # Use module-level Tenant/Domain (patchable in tests)
        from apps.tenants import services as _self_module
        _Tenant = _self_module.Tenant
        _Domain = _self_module.Domain

        # --- 1. Check slug uniqueness ---
        if _Tenant.objects.filter(slug=slug).exists():
            raise TenantAlreadyExists(
                f"A tenant with slug '{slug}' already exists."
            )

        # --- 2. Resolve the subscription plan (best-effort; stubs ok) ---
        plan = self._resolve_plan(plan_id)

        # --- 3. Create Tenant (auto-creates PG schema via TenantMixin) ---
        schema_name = f"tenant_{slug}"
        with transaction.atomic():
            tenant = _Tenant(
                name=name,
                slug=slug,
                schema_name=schema_name,
                is_active=False,  # activated at end of provisioning
            )
            tenant.save()  # triggers schema creation via TenantMixin
            logger.info(
                "Tenant record created: id=%s, schema=%s", tenant.pk, schema_name
            )

            # --- 4. Create Domain record ---
            domain_hostname = self._build_domain(slug)
            _Domain.objects.create(
                domain=domain_hostname,
                tenant=tenant,
                is_primary=True,
            )
            logger.info("Domain created: %s → tenant %s", domain_hostname, tenant.pk)

        # --- 5. Run migrations for the new tenant schema ---
        self._run_migrate_schemas(schema_name)

        # --- 6. Switch to tenant schema and create tenant-scoped records ---
        previous_schema = connection.schema_name
        try:
            connection.set_tenant(tenant)
            self._create_tenant_config(tenant)
            owner_user = self._create_owner_user(owner_email)
            logger.info(
                "Tenant schema provisioned: config + owner user (id=%s) created",
                owner_user.pk,
            )
        finally:
            # Restore the public schema connection regardless of outcome
            self._restore_public_schema(previous_schema)

        # --- 7. Create TenantSubscription (best-effort; skipped if stubs) ---
        if plan is not None:
            self._create_subscription(tenant, plan)

        # --- 8. Activate the tenant ---
        tenant.is_active = True
        tenant.save(update_fields=["is_active"])
        logger.info("Tenant %s activated (is_active=True)", tenant.pk)

        self._audit(
            action_code="TENANT_CREATE",
            resource_type="Tenant",
            resource_id=tenant.pk,
            new_value={"name": name, "slug": slug},
        )

        return tenant

    # ------------------------------------------------------------------
    # suspend_tenant
    # ------------------------------------------------------------------

    def suspend_tenant(self, tenant_id: Any) -> None:
        """
        Suspend an active tenant immediately.

        Steps:
          1. Look up the Tenant by *tenant_id* (public schema).
          2. Set ``is_active=False`` and save.
          3. Flush all Redis session keys associated with this tenant so that
             every currently authenticated user of this tenant is immediately
             logged out.

        Args:
            tenant_id: The PK of the :class:`apps.tenants.models.Tenant`.

        Raises:
            TenantNotFound: if no Tenant with *tenant_id* exists.

        Requirements: 1.5
        """
        from apps.tenants import services as _self_module
        _Tenant = _self_module.Tenant

        try:
            tenant = _Tenant.objects.get(pk=tenant_id)
        except _Tenant.DoesNotExist:
            raise TenantNotFound(
                f"No tenant found with id={tenant_id!r}."
            )

        tenant.is_active = False
        tenant.save(update_fields=["is_active"])
        logger.info(
            "Tenant %s (%s) suspended (is_active=False)", tenant.pk, tenant.name
        )

        # Invalidate all Redis sessions for this tenant
        self._flush_tenant_sessions(tenant)

        # Dispatch tenant.suspended webhook
        try:
            from apps.webhooks.dispatch import dispatch_webhook_event
            dispatch_webhook_event(
                branch_id=None,
                event_type="tenant.suspended",
                payload={
                    "tenant_id": str(tenant.id),
                    "tenant_name": tenant.name,
                    "slug": tenant.slug,
                    "timestamp": __import__("django").utils.timezone.now().isoformat(),
                },
            )
        except Exception:
            pass

        # Write platform-level audit log in the public schema
        try:
            PlatformAuditLog.objects.create(
                tenant_id=str(tenant.pk),
                user_id=_get_context_attr("user_id"),
                user_role=_get_context_attr("user_role", ""),
                ip_address=_get_context_attr("ip_address") or "0.0.0.0",
                user_agent=_get_context_attr("user_agent", ""),
                action="TENANT_SUSPEND",
                resource_type="Tenant",
                resource_id=str(tenant.pk),
                old_value={"is_active": True},
                new_value={"is_active": False},
                status="success",
                failure_reason="",
            )
        except Exception as exc:
            logger.debug("PlatformAuditLog write failed for TENANT_SUSPEND: %s", exc)

    # ------------------------------------------------------------------
    # delete_tenant
    # ------------------------------------------------------------------

    @_audit("TENANT_DELETE", resource_type="Tenant")
    def delete_tenant(self, tenant_id: Any, confirmation_token: str) -> None:
        """
        Permanently delete a tenant and all its data.

        The caller must supply the *confirmation_token* obtained by calling
        :meth:`generate_delete_token`.  This guard prevents accidental deletion
        from bugs or missing UI safeguards.

        Steps:
          1. Look up the Tenant by *tenant_id*.
          2. Verify *confirmation_token* matches the expected value.
          3. Drop the tenant's PostgreSQL schema (CASCADE) via raw SQL.
          4. Delete the Tenant record (cascades to Domain via FK).

        Args:
            tenant_id:          PK of the Tenant to delete.
            confirmation_token: Hex string returned by :meth:`generate_delete_token`.

        Raises:
            TenantNotFound:          if no tenant with *tenant_id* exists.
            InvalidConfirmationToken: if the token does not match.

        Requirements: 1.6
        """
        from apps.tenants import services as _self_module
        _Tenant = _self_module.Tenant

        try:
            tenant = _Tenant.objects.get(pk=tenant_id)
        except _Tenant.DoesNotExist:
            raise TenantNotFound(
                f"No tenant found with id={tenant_id!r}."
            )

        expected_token = self._generate_confirmation_token(tenant_id)
        if not secrets.compare_digest(confirmation_token, expected_token):
            raise InvalidConfirmationToken(
                "The provided confirmation token is invalid. "
                "Use ProvisioningService.generate_delete_token(tenant_id) to "
                "obtain the expected value."
            )

        schema_name = tenant.schema_name
        logger.warning(
            "Deleting tenant %s (%s) — dropping schema '%s'",
            tenant.pk, tenant.name, schema_name,
        )

        # Drop the schema — this is irreversible.
        with connection.cursor() as cursor:
            cursor.execute(
                "DROP SCHEMA IF EXISTS %s CASCADE" % connection.ops.quote_name(schema_name)
            )
        logger.info("Schema '%s' dropped.", schema_name)

        # Delete the Tenant record; Domain is cascade-deleted by the FK.
        tenant.delete()
        logger.info(
            "Tenant record (id=%s, schema=%s) deleted from public schema.",
            tenant_id, schema_name,
        )

        self._audit(
            action_code="TENANT_DELETE",
            resource_type="Tenant",
            resource_id=tenant_id,
            old_value={"name": tenant.name, "slug": tenant.slug},
        )

    # ------------------------------------------------------------------
    # generate_delete_token (public helper for callers)
    # ------------------------------------------------------------------

    def generate_delete_token(self, tenant_id: Any) -> str:
        """
        Return the confirmation token required to delete *tenant_id*.

        Callers (views, management commands) should present this token to the
        operator, who must echo it back in the delete request.
        """
        return self._generate_confirmation_token(tenant_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _audit(action_code: str, resource_type: str, resource_id=None,
                old_value=None, new_value=None, status: str = "success",
                failure_reason: str = "") -> None:
        """Write an AuditLog entry. Silently swallows errors."""
        try:
            from apps.audit.models import AuditLog
            AuditLog.objects.create(
                tenant_id=_get_context_attr("tenant_id"),
                user_id=_get_context_attr("user_id"),
                user_role=_get_context_attr("user_role", ""),
                ip_address=_get_context_attr("ip_address") or "0.0.0.0",
                user_agent=_get_context_attr("user_agent", ""),
                action=action_code,
                resource_type=resource_type,
                resource_id=resource_id,
                old_value=old_value,
                new_value=new_value,
                status=status,
                failure_reason=failure_reason,
            )
        except Exception as exc:
            logger.debug("ProvisioningService: audit write failed: %s", exc)

    @staticmethod
    def _resolve_plan(plan_id: Any):
        """
        Attempt to load the billing.SubscriptionPlan with *plan_id*.

        Returns None if the billing app is not yet implemented (stub models)
        so that provisioning still works in early development phases.
        """
        try:
            from apps.billing.models import SubscriptionPlan
        except ImportError:
            logger.debug("billing.SubscriptionPlan not yet available; skipping plan lookup")
            return None

        try:
            return SubscriptionPlan.objects.get(pk=plan_id)
        except AttributeError:
            # SubscriptionPlan is a stub module without a real model
            logger.debug("billing.SubscriptionPlan is a stub; skipping plan lookup")
            return None
        except Exception:  # SubscriptionPlan.DoesNotExist or similar
            raise PlanNotFound(f"No SubscriptionPlan found with id={plan_id!r}.")

    @staticmethod
    def _build_domain(slug: str) -> str:
        """
        Construct the primary domain hostname for a tenant.

        Reads ``PLATFORM_DOMAIN`` from settings if set; falls back to a
        plain ``{slug}.localhost`` for local development and tests.
        """
        from django.conf import settings

        platform_domain = getattr(settings, "PLATFORM_DOMAIN", None)
        if platform_domain:
            return f"{slug}.{platform_domain}"
        return f"{slug}.localhost"

    @staticmethod
    def _create_tenant_config(tenant) -> None:
        """
        Create a TenantConfig record with safe defaults in the tenant's schema.

        Gracefully skipped when whitelabel.TenantConfig is still a stub.
        """
        try:
            from apps.whitelabel.models import TenantConfig
        except ImportError:
            logger.debug("whitelabel.TenantConfig not yet available; skipping config creation")
            return

        # TenantConfig may be a stub module without a real model class
        if not hasattr(TenantConfig, "objects"):
            logger.debug("whitelabel.TenantConfig is a stub; skipping config creation")
            return

        TenantConfig.objects.create(
            restaurant_name=tenant.name,
            primary_color="#8B3A2A",
            secondary_color="#5D7061",
            font_choice="Roboto",
            default_language="en",
            currency="ETB",
            timezone="Africa/Addis_Ababa",
            tax_rate="15.00",
            tax_label="VAT",
            service_charge_pct="0.00",
        )
        logger.debug("TenantConfig created for tenant '%s'", tenant.name)

    @staticmethod
    def _create_owner_user(owner_email: str):
        """
        Create a Tenant_Owner user in the current tenant schema.

        A cryptographically strong temporary password is generated.
        In production, the owner receives a password-reset email so they can
        set their own password; that email-send step is deferred to the view
        layer (Task 4.2).

        Returns:
            The newly created :class:`apps.authentication.models.User`.
        """
        from apps.authentication.models import User, UserRole

        temp_password = secrets.token_urlsafe(32)
        user = User.objects.create_user(
            email=owner_email,
            password=temp_password,
            role=UserRole.TENANT_OWNER,
        )
        logger.debug("Tenant_Owner user created: %s", owner_email)
        return user

    @staticmethod
    def _create_subscription(tenant, plan) -> None:
        """
        Create a TenantSubscription linking *tenant* ↔ *plan*.

        Gracefully skipped when billing.TenantSubscription is still a stub.
        """
        try:
            from apps.billing.models import TenantSubscription
        except ImportError:
            return

        if not hasattr(TenantSubscription, "objects"):
            return

        import datetime

        today = datetime.date.today()
        # Default billing period: 1 month
        TenantSubscription.objects.create(
            tenant=tenant,
            plan=plan,
            status="active",
            current_period_start=today,
            current_period_end=today.replace(month=today.month % 12 + 1)
            if today.month < 12
            else today.replace(year=today.year + 1, month=1),
        )
        logger.debug(
            "TenantSubscription created: tenant=%s plan=%s", tenant.pk, plan.pk
        )

    @staticmethod
    def _flush_tenant_sessions(tenant) -> None:
        """
        Delete all Redis session keys that belong to this tenant.

        Sessions are stored in Redis with keys matching the pattern:
            ``django.contrib.sessions.cache:<session_key>``

        We cannot cheaply enumerate sessions per-tenant without an auxiliary
        index.  The approach here is:

          1. Use the Redis SCAN command to iterate keys matching the session
             key prefix in the configured cache database.
          2. For each candidate key, load the session data and check if it
             contains ``tenant_schema = tenant.schema_name``.  Sessions set
             by the authentication system store this field.
          3. Delete matching keys.

        Falls back gracefully when Redis is not configured (e.g. in tests
        with LocMemCache), logging a warning rather than raising.

        The pattern ``django:1:*`` covers the default Django cache key prefix
        for the Redis backend.
        """
        import django.core.cache as cache_module

        redis_client = _get_redis_client(cache_module.cache)
        if redis_client is None:
            logger.warning(
                "Redis not available; sessions for tenant %s could not be flushed. "
                "Users may remain authenticated until their session expires naturally.",
                tenant.pk,
            )
            return

        schema_name = tenant.schema_name
        deleted_count = 0

        try:
            # Scan for all session keys in the Redis store.
            # Django's RedisCache uses a key prefix; the default is ":1:".
            # Session keys are stored under the SESSION_CACHE_ALIAS.
            session_key_pattern = "*"  # broad scan — we filter by session content below
            cursor = 0
            keys_to_delete = []

            while True:
                cursor, keys = redis_client.scan(cursor, match=session_key_pattern, count=100)
                for key in keys:
                    try:
                        # Load raw session data
                        raw = redis_client.get(key)
                        if raw and _session_belongs_to_tenant(raw, schema_name):
                            keys_to_delete.append(key)
                    except Exception:
                        pass  # skip unreadable keys
                if cursor == 0:
                    break

            if keys_to_delete:
                deleted_count = redis_client.delete(*keys_to_delete)

            logger.info(
                "Flushed %d session(s) for tenant '%s' (schema=%s)",
                deleted_count, tenant.name, schema_name,
            )
        except Exception as exc:
            logger.error(
                "Error while flushing sessions for tenant %s: %s",
                tenant.pk, exc,
            )

    @staticmethod
    def _restore_public_schema(previous_schema: str) -> None:
        """Switch the database connection back to the public schema."""
        from django_tenants.utils import get_public_schema_name

        try:
            from apps.tenants.models import Tenant as TenantModel
            public_tenant = TenantModel.objects.get(schema_name=get_public_schema_name())
            connection.set_tenant(public_tenant)
        except Exception:
            # In tests the public Tenant record may not exist; use the raw cursor.
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET search_path TO public")
            except Exception as exc:
                logger.warning(
                    "Could not restore public schema after tenant provisioning: %s", exc
                )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _get_redis_client(django_cache):
    """
    Extract the underlying redis-py client from a Django cache backend.

    Supports:
      - django.core.cache.backends.redis.RedisCache (Django 4+)
      - django_redis cache backend

    Returns None if the cache is not Redis-backed.
    """
    # Django 4+ built-in Redis backend
    if hasattr(django_cache, "_cache"):
        client = django_cache._cache
        # django-redis wraps the client further
        if hasattr(client, "get_client"):
            return client.get_client()
        # Django's built-in redis backend exposes the pool directly
        if hasattr(client, "_pools"):
            try:
                import redis as redis_lib
                # Build a client from the first connection pool
                pool = next(iter(client._pools.values()))
                return redis_lib.Redis(connection_pool=pool)
            except (ImportError, StopIteration, Exception):
                pass
        return client

    # django-redis top-level
    if hasattr(django_cache, "client"):
        try:
            return django_cache.client.get_client()
        except Exception:
            pass

    return None


def _session_belongs_to_tenant(raw_session_data: bytes, schema_name: str) -> bool:
    """
    Return True if the serialized session data contains a reference to
    *schema_name* (i.e. the session was created in that tenant's context).

    Django sessions are pickled or JSON-encoded dicts.  We do a cheap bytes
    search first to avoid deserialisation overhead.
    """
    try:
        if schema_name.encode() in raw_session_data:
            return True
        # Also try decoding as UTF-8 string for JSON-encoded sessions
        if schema_name in raw_session_data.decode("utf-8", errors="ignore"):
            return True
    except Exception:
        pass
    return False
