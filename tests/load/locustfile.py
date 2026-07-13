"""
tests/load/locustfile.py

Locust load test scenarios for the Restaurant Management & Smart Ordering Platform.

=============================================================================
Default run configuration (see locust.conf for full settings):
    locust -f tests/load/locustfile.py --headless -u 500 -r 10 --run-time 10m
=============================================================================

Performance targets (Requirements 19.1, 19.2, 19.3):
  - p95 API response time  < 500 ms
  - Order WebSocket notification delivery  < 2 s end-to-end
  - Customer menu page first-paint  < 2 s on 4G simulation (10 Mbps / 40 ms latency)
  - Inventory deduction background task (deduct_inventory)  < 5 s median

Scenarios
---------
CustomerFlow:
    Simulates a diner scanning a QR code, browsing the menu, and placing an order.
    Steps:
      1. POST /api/v1/customer/session/   — create anonymous session via QR token
      2. GET  /api/v1/customer/menu/      — browse active menu (read-heavy endpoint)
      3. POST /api/v1/customer/orders/    — place order with 1–3 random menu items

StaffFlow:
    Simulates a kitchen/reception staff member processing orders.
    Steps:
      1. POST /api/v1/auth/login/              — authenticate with staff credentials
      2. PATCH /api/v1/orders/{id}/status/     — advance order through status transitions
      3. GET  /api/v1/branches/{id}/inventory/ — check inventory levels

Environment variables (set in locust.conf or via --conf / CLI overrides):
  LOCUST_HOST            — target host, e.g. https://api.platform.com
  LOCUST_STAFF_EMAIL     — staff login email for StaffFlow
  LOCUST_STAFF_PASSWORD  — staff login password for StaffFlow
  LOCUST_QR_TOKEN        — a valid QR token UUID for CustomerFlow
                           (seed one per test run; the script rotates via list)
  LOCUST_BRANCH_ID       — branch UUID used by StaffFlow inventory check
  LOCUST_ORDER_IDS       — comma-separated list of confirmed order UUIDs to
                           cycle through for status-update steps

Requirements: 19.1, 19.2, 19.3
"""

import logging
import os
import random
import uuid

from locust import HttpUser, SequentialTaskSet, between, events, task

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment variables
# ---------------------------------------------------------------------------

HOST = os.environ.get("LOCUST_HOST", "http://localhost:8000")

STAFF_EMAIL = os.environ.get("LOCUST_STAFF_EMAIL", "staff@example.com")
STAFF_PASSWORD = os.environ.get("LOCUST_STAFF_PASSWORD", "changeme")

# A comma-separated list of QR token UUIDs to rotate through.
# If not supplied, the test generates random (invalid) UUIDs and expects
# a 404 — useful for measuring error-path response times.
QR_TOKENS_RAW = os.environ.get("LOCUST_QR_TOKENS", "")
QR_TOKENS: list[str] = [t.strip() for t in QR_TOKENS_RAW.split(",") if t.strip()]

BRANCH_ID = os.environ.get("LOCUST_BRANCH_ID", str(uuid.uuid4()))

# Comma-separated order UUIDs in 'confirmed' or 'received' state at test start.
ORDER_IDS_RAW = os.environ.get("LOCUST_ORDER_IDS", "")
ORDER_IDS: list[str] = [o.strip() for o in ORDER_IDS_RAW.split(",") if o.strip()]

# Status transitions to cycle through in StaffFlow
STATUS_TRANSITIONS = [
    "received",
    "preparing",
    "ready",
    "served",
]


# ---------------------------------------------------------------------------
# Shared menu items pool — populated by the first CustomerFlow GET /menu call
# ---------------------------------------------------------------------------

_shared_menu_items: list[dict] = []


# ---------------------------------------------------------------------------
# Scenario 1 — Customer flow
# ---------------------------------------------------------------------------

class CustomerTaskSet(SequentialTaskSet):
    """
    Sequential task set modelling a single customer QR-scan → order lifecycle.

    Step 1 — POST /api/v1/customer/session/
        Creates an anonymous session using a real QR token from the pool.
        On success: stores session cookie and retrieves branch_id / table_id.
        Performance target: p95 < 500 ms.

    Step 2 — GET /api/v1/customer/menu/
        Fetches the active menu for the session's branch.
        Exercises the Redis-cached menu endpoint (Task 20.2 cache key: menu:branch:{id}).
        Performance target: p95 < 500 ms; cached responses should be < 50 ms.

    Step 3 — POST /api/v1/customer/orders/
        Places an order with 1–3 randomly selected items from the menu response.
        Performance target: p95 < 500 ms; WebSocket notification delivered < 2 s.
    """

    # Branch and table IDs resolved after session creation
    _branch_id: str | None = None
    _table_id: str | None = None
    _table_number: str = "1"

    def on_start(self):
        """Reset per-user state at the start of each task set iteration."""
        self._branch_id = None
        self._table_id = None

    @task
    def step1_create_session(self):
        """
        POST /api/v1/customer/session/

        Uses a real QR token when available; otherwise sends a synthetic UUID
        (expected to return 404, useful for measuring error-path latency).
        """
        token = (
            random.choice(QR_TOKENS)
            if QR_TOKENS
            else str(uuid.uuid4())
        )

        with self.client.post(
            "/api/v1/customer/session/",
            json={"token": token},
            name="[Customer] POST /api/v1/customer/session/",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                self._branch_id = data.get("branch_id")
                self._table_id = data.get("table_id")
                self._table_number = data.get("table_number", "1")
                resp.success()
            elif resp.status_code == 404 and not QR_TOKENS:
                # No real token configured — 404 is expected; mark as success
                # so it counts as a valid latency sample.
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}")

    @task
    def step2_browse_menu(self):
        """
        GET /api/v1/customer/menu/

        Reads from the Redis-cached menu endpoint.
        The cache key is ``menu:branch:{branch_id}`` with 30-second TTL
        (set by Task 20.2).  Most requests under sustained load will be
        served from cache, yielding sub-millisecond DB round trips.
        """
        with self.client.get(
            "/api/v1/customer/menu/",
            name="[Customer] GET /api/v1/customer/menu/",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                items = resp.json()
                # Store in shared pool so other users can reuse them
                global _shared_menu_items
                if items and not _shared_menu_items:
                    _shared_menu_items = items
                resp.success()
            elif resp.status_code == 401:
                # Session creation failed (no real QR token) — skip gracefully
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}")

    @task
    def step3_place_order(self):
        """
        POST /api/v1/customer/orders/

        Places an order with 1–3 randomly selected items from the menu.
        Triggers the async send_order_notification Celery task (WebSocket
        delivery target: < 2 s end-to-end, per Requirement 17.1).
        """
        # Pick items from the shared pool (populated by previous step 2 runs)
        available_items = _shared_menu_items if _shared_menu_items else []
        if not available_items:
            # No menu items cached yet — skip order placement this iteration
            return

        count = random.randint(1, min(3, len(available_items)))
        selected = random.sample(available_items, count)

        order_payload = {
            "items": [
                {
                    "menu_item_id": item["id"],
                    "quantity": random.randint(1, 3),
                    "special_instructions": "",
                }
                for item in selected
            ],
        }

        with self.client.post(
            "/api/v1/customer/orders/",
            json=order_payload,
            name="[Customer] POST /api/v1/customer/orders/",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                resp.success()
            elif resp.status_code in (401, 422):
                # Session expired or item unavailable — not a performance failure
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}")


class CustomerUser(HttpUser):
    """
    Virtual customer user: wait 1–5 seconds between task set iterations
    to simulate realistic think-time between QR scan and menu browsing.

    Default run: --users 500 --spawn-rate 10
    """

    tasks = [CustomerTaskSet]
    wait_time = between(1, 5)
    # Locust selects the default host from locust.conf / --host CLI flag.
    # Override with LOCUST_HOST env var or --host argument.


# ---------------------------------------------------------------------------
# Scenario 2 — Staff flow
# ---------------------------------------------------------------------------

class StaffTaskSet(SequentialTaskSet):
    """
    Sequential task set modelling a staff member's session.

    Step 1 — POST /api/v1/auth/login/
        Authenticates with STAFF_EMAIL / STAFF_PASSWORD credentials.
        On success: session cookie is set automatically by the Locust client.
        Performance target: p95 < 500 ms.

    Step 2 — PATCH /api/v1/orders/{id}/status/
        Advances a randomly selected order through valid status transitions.
        Cycles through [received, preparing, ready, served].
        Performance target: p95 < 500 ms; background deduct_inventory < 5 s.

    Step 3 — GET /api/v1/branches/{id}/inventory/
        Reads inventory levels from the branch.
        Performance target: p95 < 500 ms.
    """

    _session_valid: bool = False
    _current_status_index: int = 0

    def on_start(self):
        self._session_valid = False
        self._current_status_index = 0

    @task
    def step1_login(self):
        """
        POST /api/v1/auth/login/

        Authenticates the virtual staff user and stores the session cookie.
        """
        with self.client.post(
            "/api/v1/auth/login/",
            json={"email": STAFF_EMAIL, "password": STAFF_PASSWORD},
            name="[Staff] POST /api/v1/auth/login/",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                self._session_valid = True
                resp.success()
            elif resp.status_code == 401:
                # Bad credentials in config — still measure latency
                self._session_valid = False
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}")

    @task
    def step2_update_order_status(self):
        """
        PATCH /api/v1/orders/{id}/status/

        Advances order status through the state machine.
        Uses a round-robin selection from ORDER_IDS if provided.
        Triggers deduct_inventory Celery task (median SLA < 5 s).
        """
        if not self._session_valid:
            return

        order_id = (
            random.choice(ORDER_IDS)
            if ORDER_IDS
            else str(uuid.uuid4())  # synthetic ID → 404 (measures error path)
        )

        new_status = STATUS_TRANSITIONS[
            self._current_status_index % len(STATUS_TRANSITIONS)
        ]
        self._current_status_index += 1

        with self.client.patch(
            f"/api/v1/orders/{order_id}/status/",
            json={"status": new_status},
            name="[Staff] PATCH /api/v1/orders/{id}/status/",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 422):
                # 422 = invalid transition (not a performance issue)
                resp.success()
            elif resp.status_code == 404:
                # Synthetic/recycled order ID — measure latency, mark success
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}")

    @task
    def step3_check_inventory(self):
        """
        GET /api/v1/branches/{id}/inventory/

        Reads branch inventory — exercises the (branch_id, quantity) index
        added by Task 20.3 migration.
        """
        if not self._session_valid:
            return

        with self.client.get(
            f"/api/v1/branches/{BRANCH_ID}/inventory/",
            name="[Staff] GET /api/v1/branches/{id}/inventory/",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 403, 404):
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}")


class StaffUser(HttpUser):
    """
    Virtual staff user: wait 2–8 seconds between task set iterations
    to simulate a staff member pausing between actions.

    Default run: --users 500 --spawn-rate 10 (shared with CustomerUser pool)
    """

    tasks = [StaffTaskSet]
    wait_time = between(2, 8)


# ---------------------------------------------------------------------------
# Locust event hooks — aggregate p95 assertions after test run
# ---------------------------------------------------------------------------

@events.quitting.add_listener
def on_quitting(environment, **kwargs):
    """
    Check p95 response time after test completion and emit warnings if
    any endpoint exceeds the 500 ms SLA threshold.

    Note: This hook emits advisory warnings only; it does not hard-fail
    the Locust run.  For CI pass/fail gates, query the /stats/requests
    JSON endpoint from your CI script and assert thresholds there.
    """
    if not environment.runner:
        return

    stats = environment.runner.stats
    threshold_ms = 500  # p95 SLA (Requirement 19.1)

    violations = []
    for name, entry in stats.entries.items():
        p95 = entry.get_response_time_percentile(0.95)
        if p95 is not None and p95 > threshold_ms:
            violations.append(f"  {name[1]}: p95={p95:.0f}ms (limit={threshold_ms}ms)")

    if violations:
        logger.warning("=== SLA VIOLATIONS (p95 > %dms) ===", threshold_ms)
        for v in violations:
            logger.warning(v)
    else:
        logger.info("All endpoints within p95 < %dms SLA.", threshold_ms)
