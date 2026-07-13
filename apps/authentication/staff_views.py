"""
staff_views.py — Server-rendered staff portal views.

Provides login/logout and dashboard pages for:
  - Branch Manager
  - Kitchen Staff (KDS)
  - Receptionist
  - Tenant Owner

All views require authentication. The branch context is inferred from the
logged-in user's branch FK (or the first branch for tenant owners).
"""

import logging

import pyotp

from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_staff_context(request):
    """Build common template context from the authenticated user."""
    user = request.user
    branch = getattr(user, "branch", None)

    # Tenant owners and superusers may not have a branch FK —
    # use the first branch for the current tenant
    if branch is None:
        try:
            from apps.branches.models import Branch
            branch = Branch.objects.first()
        except Exception:
            branch = None

    config = None
    try:
        from apps.whitelabel.models import TenantConfig
        config = TenantConfig.objects.first()
    except Exception:
        pass

    role = getattr(user, "role", "")

    # Role-group helpers for template-level gating
    is_manager_up = role in ("Branch_Manager", "Tenant_Owner", "Super_Admin")

    return {
        "user": user,
        "branch": branch,
        "branch_id": str(branch.id) if branch else "",
        "branch_name": branch.name if branch else "",
        "restaurant_name": config.restaurant_name if config else "Restaurant",
        "currency": config.currency if config else "ETB",
        "user_role": role,
        "can_view_kds": role in ("Kitchen_Staff", "Receptionist", "Branch_Manager", "Tenant_Owner", "Super_Admin"),
        "can_view_reception": role in ("Receptionist", "Branch_Manager", "Tenant_Owner", "Super_Admin"),
        "can_view_orders": role in ("Receptionist", "Branch_Manager", "Kitchen_Staff", "Tenant_Owner", "Super_Admin"),
        "can_manage_menu": is_manager_up,
        "can_manage_qr": is_manager_up,
        "can_manage_tables": is_manager_up,
        "can_manage_inventory": is_manager_up,
        "can_manage_expenses": is_manager_up,
        "can_view_financials": is_manager_up,
        "session_key": request.session.session_key or "",
    }


ALLOWED_STAFF_ROLES = frozenset({
    "Branch_Manager",
    "Kitchen_Staff",
    "Receptionist",
    "Tenant_Owner",
    "Super_Admin",
})


def _require_auth(view_fn):
    """Decorator — requires authenticated staff user with an allowed role."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/staff/login/?next={request.path}")
        role = getattr(request.user, "role", "")
        if role not in ALLOWED_STAFF_ROLES:
            logger.warning("User %s with role %s denied staff access", request.user, role)
            return redirect(f"/staff/login/?next={request.path}")
        return view_fn(request, *args, **kwargs)
    wrapper.__name__ = view_fn.__name__
    return wrapper


# ---------------------------------------------------------------------------
# Auth views
# ---------------------------------------------------------------------------

def staff_register(request):
    """GET/POST /staff/register/ — Self-registration or manager-invite.

    - Unauthenticated users: self-register with email + password + role.
    - Authenticated managers: invite staff (original invite flow).
    """
    from apps.authentication.models import User

    error = None
    success = None

    # ── Authenticated user → manager invite flow ───────────────────
    if request.user.is_authenticated:
        from apps.authentication.serializers import SignupSerializer

        if request.user.role not in ("Branch_Manager", "Tenant_Owner", "Super_Admin"):
            return redirect("/staff/")

        if request.method == "POST":
            email = request.POST.get("email", "").strip()
            role = request.POST.get("role", "")
            branch_id = request.POST.get("branch_id", "") or None

            if not all([email, role]):
                error = "Email and role are required."
            else:
                tenant = getattr(request, "tenant", None)
                serializer = SignupSerializer(
                    data={"email": email, "role": role, "branch_id": branch_id},
                    tenant=tenant,
                )
                if serializer.is_valid():
                    user = serializer.save()
                    logger.info(
                        "Staff account created by %s: %s (role=%s)",
                        request.user.email, email, role,
                    )
                    from apps.authentication.models import PasswordResetToken
                    from django.core.mail import send_mail
                    from django.conf import settings as django_settings

                    try:
                        PasswordResetToken.objects.filter(
                            user=user, is_used=False
                        ).update(is_used=True)
                        reset_token = PasswordResetToken.objects.create(user=user)
                        scheme = "https" if request.is_secure() else "http"
                        set_url = (
                            f"{scheme}://{request.get_host()}"
                            f"/reset-password?token={reset_token.token}"
                        )
                        send_mail(
                            subject="Set your password — Restaurant Platform",
                            message=(
                                f"Hi {user.email},\n\n"
                                f"A staff account has been created for you.\n\n"
                                f"Click the link below to set your password:\n{set_url}\n\n"
                                f"This link expires in 1 hour."
                            ),
                            from_email=getattr(
                                django_settings, "DEFAULT_FROM_EMAIL",
                                "noreply@platform.local",
                            ),
                            recipient_list=[user.email],
                            fail_silently=False,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to send invite email to %s", user.email
                        )

                    return redirect("/staff/?invite_sent=1")
                else:
                    for _field, errors in serializer.errors.items():
                        error = errors[0] if isinstance(errors, list) else errors
                        break

        return render(request, "staff/register.html", {
            "error": error,
            "success": success,
            "self_register": False,
        })

    # ── Unauthenticated → self-registration ────────────────────────
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        role = request.POST.get("role", "")
        password = request.POST.get("password", "")
        confirm = request.POST.get("confirm_password", "")

        if not all([email, role, password, confirm]):
            error = "All fields are required."
        elif password != confirm:
            error = "Passwords do not match."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif role not in ("Branch_Manager", "Receptionist", "Kitchen_Staff"):
            error = "Please select a valid role."
        elif User.objects.filter(email=email).exists():
            error = "A user with this email already exists."
        else:
            try:
                from apps.branches.models import Branch
                first_branch = Branch.objects.first()
                user = User(
                    email=email,
                    role=role,
                    branch=first_branch,
                    is_active=True,
                )
                user.set_password(password)
                user.save()
                logger.info(
                    "Self-registration: %s (role=%s, branch=%s)",
                    email, role, first_branch,
                )
                return redirect("/staff/login/?registered=1")
            except Exception as exc:
                logger.exception("Self-registration failed for %s", email)
                error = f"Registration failed: {exc}"

    return render(request, "staff/register.html", {
        "error": error,
        "success": success,
        "self_register": True,
    })


def staff_login(request):
    """GET/POST /staff/login/ — supports 2-step auth when TOTP is enabled."""
    if request.user.is_authenticated:
        return redirect("/staff/")

    error = None
    next_url = request.GET.get("next", "/staff/")
    # On GET (e.g. "Back to sign in"), clear any pending 2FA state
    if request.method == "GET":
        request.session.pop("_2fa_pending_user_id", None)
        request.session.pop("_2fa_pending_email", None)
    show_totp = bool(request.session.get("_2fa_pending_user_id"))
    totp_email = request.session.get("_2fa_pending_email", "")

    if request.method == "POST":
        next_url = request.POST.get("next", "/staff/")

        # Step 2: verify TOTP code
        if "_2fa_pending_user_id" in request.session:
            code = request.POST.get("totp_code", "").strip()
            uid = request.session.get("_2fa_pending_user_id")
            try:
                from apps.authentication.models import User
                user = User.objects.get(pk=uid, is_active=True)
            except User.DoesNotExist:
                error = "Session expired. Please sign in again."
                request.session.pop("_2fa_pending_user_id", None)
                request.session.pop("_2fa_pending_email", None)
            else:
                totp = pyotp.TOTP(user.totp_secret)
                if totp.verify(code, valid_window=1):
                    login(request, user)
                    logger.info("Staff 2FA login: %s", user.email)
                    request.session.pop("_2fa_pending_user_id", None)
                    request.session.pop("_2fa_pending_email", None)
                    return redirect(next_url)
                else:
                    error = "Invalid verification code. Please try again."
            return render(request, "staff/login.html", {
                "error": error, "next": next_url,
                "show_totp": True, "totp_email": totp_email,
            })

        # Step 1: email + password
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")

        # Lockout check — look up user first so we can track failures
        from apps.authentication.models import User as AuthUser
        try:
            auth_user = AuthUser.objects.get(email=email, is_active=True)
        except AuthUser.DoesNotExist:
            auth_user = None

        if auth_user is not None and auth_user.is_locked:
            error = "This account is locked due to too many failed login attempts. Please try again later."
            logger.warning("Staff login blocked (locked): %s", email)
            return render(request, "staff/login.html", {
                "error": error, "next": next_url,
                "show_totp": False, "totp_email": "",
            })

        user = authenticate(request, email=email, password=password)
        if user is not None:
            user.reset_login_attempts()
            if user.totp_secret:
                # 2FA enabled — store in session, show TOTP step
                request.session["_2fa_pending_user_id"] = str(user.pk)
                request.session["_2fa_pending_email"] = user.email
                return render(request, "staff/login.html", {
                    "next": next_url,
                    "show_totp": True,
                    "totp_email": user.email,
                })
            login(request, user)
            logger.info("Staff login: %s", email)
            return redirect(next_url)
        else:
            if auth_user is not None:
                auth_user.record_failed_login()
                remaining = 5 - auth_user.failed_login_count
                if remaining > 0:
                    error = "Invalid email or password. {} attempt(s) remaining.".format(remaining)
                else:
                    error = "This account is locked due to too many failed login attempts. Please try again later."
                logger.warning("Failed staff login attempt for: %s (attempt %d/5)", email, auth_user.failed_login_count)
            else:
                error = "Invalid email or password."
                logger.warning("Failed staff login attempt for unknown user: %s", email)

    return render(request, "staff/login.html", {
        "error": error, "next": next_url,
        "show_totp": show_totp, "totp_email": totp_email,
    })


def staff_logout(request):
    """GET /staff/logout/"""
    logout(request)
    return redirect("/staff/login/")


# ---------------------------------------------------------------------------
# Staff dashboard pages
# ---------------------------------------------------------------------------

@_require_auth
def staff_dashboard(request):
    """GET /staff/ — Overview dashboard"""
    ctx = _get_staff_context(request)
    return render(request, "staff/dashboard.html", ctx)


@_require_auth
def staff_kds(request):
    """GET /staff/kds/ — Kitchen Display System"""
    if request.user.role not in ("Kitchen_Staff", "Receptionist", "Branch_Manager", "Tenant_Owner", "Super_Admin"):
        return redirect("/staff/")
    ctx = _get_staff_context(request)
    return render(request, "staff/kds.html", ctx)


@_require_auth
def staff_reception(request):
    """GET /staff/reception/ — Reception dashboard"""
    if request.user.role not in ("Receptionist", "Branch_Manager", "Tenant_Owner", "Super_Admin"):
        return redirect("/staff/")
    ctx = _get_staff_context(request)
    return render(request, "staff/reception.html", ctx)


@_require_auth
def staff_orders(request):
    """GET /staff/orders/ — Orders management"""
    if request.user.role not in ("Receptionist", "Branch_Manager", "Kitchen_Staff", "Tenant_Owner", "Super_Admin"):
        return redirect("/staff/")
    ctx = _get_staff_context(request)
    return render(request, "staff/orders.html", ctx)


@_require_auth
def staff_menu(request):
    """GET /staff/menu/ — Menu management"""
    if request.user.role not in ("Branch_Manager", "Tenant_Owner", "Super_Admin"):
        return redirect("/staff/")
    ctx = _get_staff_context(request)
    return render(request, "staff/menu.html", ctx)


@_require_auth
def staff_inventory(request):
    """GET /staff/inventory/ — Inventory management"""
    if request.user.role not in ("Branch_Manager", "Tenant_Owner", "Super_Admin"):
        return redirect("/staff/")
    ctx = _get_staff_context(request)
    return render(request, "staff/inventory.html", ctx)


@_require_auth
def staff_expenses(request):
    """GET /staff/expenses/ — Expense tracking"""
    if request.user.role not in ("Branch_Manager", "Tenant_Owner", "Super_Admin"):
        return redirect("/staff/")
    ctx = _get_staff_context(request)
    return render(request, "staff/expenses.html", ctx)


@_require_auth
def staff_financials(request):
    """GET /staff/financials/ — Financial dashboard"""
    if request.user.role not in ("Branch_Manager", "Tenant_Owner", "Super_Admin"):
        return redirect("/staff/")
    ctx = _get_staff_context(request)
    return render(request, "staff/financials.html", ctx)


@_require_auth
def staff_profile(request):
    """GET /staff/profile/ — User profile with 2FA settings"""
    ctx = _get_staff_context(request)
    ctx["totp_enabled"] = bool(request.user.totp_secret)
    return render(request, "staff/profile.html", ctx)


@_require_auth
def staff_branch_comparison(request):
    """GET /staff/branch-comparison/ — Branch comparison for managers"""
    ctx = _get_staff_context(request)
    if request.user.role not in ("Tenant_Owner", "Super_Admin"):
        return redirect("/staff/")
    return render(request, "staff/branch_comparison.html", ctx)


@_require_auth
def staff_qr_codes(request):
    """GET /staff/qr-codes/ — QR code management"""
    if request.user.role not in ("Branch_Manager", "Tenant_Owner", "Super_Admin"):
        return redirect("/staff/")
    ctx = _get_staff_context(request)
    return render(request, "staff/qr_codes.html", ctx)


@_require_auth
def staff_tables(request):
    """GET /staff/tables/ — Table and room management"""
    if request.user.role not in ("Branch_Manager", "Tenant_Owner", "Super_Admin"):
        return redirect("/staff/")
    ctx = _get_staff_context(request)
    return render(request, "staff/tables.html", ctx)
