# ZAP Findings — Security Hardening Applied

This document records the security controls already implemented in the
Restaurant Management & Smart Ordering Platform that address each class of
vulnerability targeted by the OWASP ZAP automated scan (task 19.3).

The controls below map to requirements 19.4 (injection prevention), 19.5
(authentication hardening), 19.6 (transport & header security), and 19.7
(tenant isolation enforcement).

---

## 1. Cross-Site Scripting (XSS)

**ZAP rules covered:** 40012 (Reflected XSS), 40014 (Persistent XSS),
40016 (Persistent XSS — Prime), 40017 (Persistent XSS — Spider)

**Applicable to:** All free-text fields including menu item names, descriptions,
and special-instruction fields. This explicitly includes Amharic strings in the
Ethiopic Unicode block (U+1200–U+137F), which are stored and rendered as UTF-8
throughout the stack.

### Controls in place

| Layer | Control | Location |
|---|---|---|
| Django templates | Auto-escaping enabled globally (`TEMPLATES[0]["OPTIONS"]["autoescape"] = True`) | `config/settings/base.py` |
| DRF serializers | Output is always `application/json`; HTML is never injected into JSON values | All serializers in `apps/*/serializers.py` |
| CSP header | `Content-Security-Policy` disallows `unsafe-inline` scripts and `unsafe-eval`; only allows scripts from `'self'` and the configured CDN origin | `config/settings/production.py` via `django-csp` |
| Input validation | DRF serializers validate field lengths and character sets; free-text fields accept Unicode (including Ethiopic) but strip control characters | `shared/validators.py` |
| Cookie flags | `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SECURE=True` — JavaScript cannot read the session cookie even if XSS were present | `config/settings/base.py` |

### Why ZAP should report no HIGH/CRITICAL XSS findings

Django's template engine escapes `<`, `>`, `"`, `'`, and `&` on every render.
DRF returns JSON responses whose `Content-Type: application/json` prevents
browsers from interpreting the body as HTML. The CSP header provides a
defence-in-depth layer that blocks any inline script execution even if a
rendering path were missed.

---

## 2. CSRF (Cross-Site Request Forgery)

**ZAP rule covered:** 20012 (Anti-CSRF Tokens Check)

**Applicable to:** All state-changing requests — order placement
(`POST /api/v1/customer/orders/`), menu updates, expense CRUD, user management,
tenant provisioning, and any other non-GET endpoint.

### Controls in place

| Layer | Control | Location |
|---|---|---|
| Django middleware | `django.middleware.csrf.CsrfViewMiddleware` is active and listed before `SessionMiddleware` | `config/settings/base.py` — `MIDDLEWARE` list |
| SameSite cookie | `SESSION_COOKIE_SAMESITE = "Strict"` — session cookie is not sent on cross-site requests at the browser level | `config/settings/base.py` |
| CSRF cookie | `CSRF_COOKIE_HTTPONLY = False` (deliberately) so JavaScript can read it for AJAX; `CSRF_COOKIE_SECURE = True` for HTTPS-only transmission | `config/settings/base.py` |
| DRF enforcement | All DRF ViewSets use `SessionAuthentication`, which enforces CSRF automatically for browser clients | `shared/permissions.py`, DRF default auth classes |
| Customer order form | HTMX-driven order placement form includes `{% csrf_token %}` in the template | `apps/orders/templates/order_form.html` |

### Why ZAP should report no HIGH/CRITICAL CSRF findings

Every state-changing endpoint requires a valid `X-CSRFToken` header or form
field matching the server-generated token. The `Strict` SameSite policy
prevents the cookie from being sent by cross-origin requests at all, providing
a second independent layer.

---

## 3. SQL Injection (SQLi)

**ZAP rule covered:** 40018 (SQL Injection)

**Applicable to:** All query parameters including the QR token parameter
(`?token=<uuid>` on `GET /api/v1/customer/menu/`), login email field, search
parameters, and any filter query strings.

### Controls in place

| Layer | Control | Location |
|---|---|---|
| Django ORM | All database queries use Django's ORM which produces parameterized SQL via `psycopg2` — user input is never interpolated into SQL strings | All `apps/*/views.py`, `apps/*/services.py` |
| No raw SQL | A `grep` audit of the codebase confirms zero uses of `cursor.execute(f"... {variable}")` string-formatted queries | Verified — use `grep -r "cursor.execute" restaurant_platform/apps/` |
| QR token validation | The QR token is validated as a valid UUID4 via `uuid.UUID(token, version=4)` before any database lookup; malformed inputs are rejected with HTTP 400 before hitting the ORM | `apps/qr/views.py` — `QRMenuView.get_queryset()` |
| Input serializer validation | DRF serializers define explicit field types (`UUIDField`, `EmailField`, `CharField(max_length=…)`); unrecognised fields are rejected | All serializers |
| Login field | The login view uses `User.objects.filter(email=email)` ORM query — the email value is passed as a parameter, never concatenated | `apps/authentication/views.py` |

### Why ZAP should report no HIGH/CRITICAL SQLi findings

All database interactions go through Django ORM's parameterized query layer.
The QR token has an explicit UUID validation step that rejects any non-UUID
payload before it reaches the database layer. No raw SQL strings are
constructed from user input anywhere in the codebase.

---

## 4. Tenant Boundary Crossing (IDOR / Path Traversal)

**ZAP rules covered:** 6 (Path Traversal), and passively via IDOR checks on
cross-tenant resource IDs.

**Applicable to:** All resource URLs containing tenant-scoped IDs, e.g.
`/api/v1/branches/{id}/`, `/api/v1/tenants/{id}/users/`,
`/api/v1/orders/{id}/`.

### Controls in place

| Layer | Control | Location |
|---|---|---|
| `TenantMiddleware` | Resolves the tenant from `request.get_host()` on every request and calls `connection.set_tenant(tenant)`, scoping the PostgreSQL search path to that tenant's schema | `apps/tenants/middleware.py` |
| Schema-level isolation | `django-tenants` creates a dedicated PostgreSQL schema per tenant; cross-schema queries are structurally impossible without an explicit `SET search_path` override | `apps/tenants/models.py`, `config/settings/base.py` |
| `TenantScopePermission` | Applied to all ViewSets; verifies the requested resource's tenant matches the requesting user's tenant — returns HTTP 403 otherwise | `shared/permissions.py` |
| `BranchScopePermission` | Applied to branch-scoped ViewSets; verifies the resource's `branch_id` matches the requesting user's assigned branch | `shared/permissions.py` |
| UUID primary keys | All resource IDs are random UUID4s, making enumeration attacks impractical | `apps/*/models.py` — `id = models.UUIDField(primary_key=True, default=uuid.uuid4)` |
| ORM queryset scoping | Every ViewSet overrides `get_queryset()` to filter by the current tenant/branch, so even if an ID is guessed the queryset will return empty and the view returns 404 | All `apps/*/views.py` ViewSets |

### Why ZAP should report no HIGH/CRITICAL tenant-crossing findings

The combination of PostgreSQL schema isolation (enforced at the database
driver level by `django-tenants`) and the `TenantScopePermission` class
(enforced at the application level) provides two independent barriers against
cross-tenant access. UUID primary keys eliminate sequential ID enumeration.
Every `get_queryset()` implementation further scopes results to the active
tenant, meaning a cross-tenant UUID lookup returns 404 rather than 403,
leaking no information about whether the resource exists in another tenant.

---

## 5. Amharic / Ethiopic Unicode Input Handling (XSS context)

**Relevant to:** Menu item names, descriptions, category names, special
instructions, and any free-text field that accepts Ethiopic characters
(U+1200–U+137F).

### Controls in place

- The database is configured with `client_encoding = UTF8` and the
  `DATABASES["default"]["OPTIONS"]` includes `{"client_encoding": "UTF8"}`.
- Django's template engine escapes Ethiopic characters the same way it escapes
  any other Unicode: HTML-special characters within the Ethiopic string are
  escaped; the Ethiopic codepoints themselves are output as-is in UTF-8.
- The ZAP scan is configured to include `maxEncodingStrength: high` on the
  active scan to verify that multi-byte UTF-8 sequences are handled correctly
  and do not bypass XSS filters.
- Property 30 (`test_amharic_unicode_round_trip`) in
  `apps/whitelabel/tests/test_unicode_property.py` verifies byte-for-byte
  round-trip fidelity for any Ethiopic string stored and retrieved from the
  database.

---

## 6. Sign-off Checklist

Before marking task 19.3 complete, the ZAP scan CI job must produce a clean
report with **zero HIGH or CRITICAL findings**. The items below must all pass:

- [ ] ZAP active scan exits with code 0 (no HIGH/CRITICAL findings)
- [ ] HTML report artifact uploaded to GitHub Actions
- [ ] SARIF report ingested by GitHub Advanced Security code scanning
- [ ] XSS rule 40012/40014/40016/40017 — no alerts at HIGH/CRITICAL
- [ ] CSRF rule 20012 — no alerts at HIGH/CRITICAL
- [ ] SQLi rule 40018 — no alerts at HIGH/CRITICAL
- [ ] Path traversal rule 6 — no alerts at HIGH/CRITICAL
- [ ] Amharic string inputs do not trigger additional findings

---

*Generated as part of task 19.3 — OWASP ZAP security scan.*
*References: Requirements 19.4 (injection prevention), 19.5 (auth hardening),*
*19.6 (transport security), 19.7 (tenant isolation).*
