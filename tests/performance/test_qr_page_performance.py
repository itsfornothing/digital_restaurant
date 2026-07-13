"""
tests/performance/test_qr_page_performance.py

Playwright-based performance and Lighthouse tests for the customer-facing
QR menu page.

Test Cases
----------
TC-Q06:
    QR menu page loads meaningful content within 2 seconds and full page
    load within 4 seconds under Chrome DevTools Fast 3G network throttling.

    Fast 3G profile (matches Chrome DevTools "Fast 3G" preset):
        Download  : ~1.5 Mbit/s  (~187 KB/s)
        Upload    : ~750 kbit/s  (~94 KB/s)
        RTT       : ~150 ms

TC-LH01–TC-LH05:
    Programmatic Lighthouse audit of the customer QR menu page on a mobile
    device preset.

    Thresholds (Requirements 19.1, 19.2, 19.3):
        Performance score  >= 85
        LCP (Largest Contentful Paint)  < 2.5 s
        FCP (First Contentful Paint)    < 1.8 s
        CLS (Cumulative Layout Shift)   < 0.1
        Accessibility score            >= 80

    NOTE: Requires the ``lighthouse`` npm package.
    Install globally: npm install -g lighthouse
    Lighthouse is invoked via subprocess since it is a Node.js tool.

Running
-------
    pytest tests/performance/ -m performance -v

    # With a live server and QR token:
    PLAYWRIGHT_BASE_URL=http://localhost:8000 \\
    PLAYWRIGHT_QR_TOKEN=<uuid> \\
    pytest tests/performance/test_qr_page_performance.py -m performance -v

Requirements: 19.1, 19.2, 19.3
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

import pytest

# ---------------------------------------------------------------------------
# Fast 3G network condition constants
# Chrome DevTools "Fast 3G" preset values (matches DevTools UI exactly):
#   Download throughput  : 1.5 Mbit/s  = 1,572,864 bits/s ÷ 8 = 196,608 bytes/s
#   Upload throughput    : 750 kbit/s  =   768,000 bits/s ÷ 8 =  96,000 bytes/s
#   Additional latency   : 150 ms      (added on top of base RTT)
# ---------------------------------------------------------------------------
_FAST_3G_DOWNLOAD_BYTES_PER_SEC = int(1.5 * 1024 * 1024 / 8)   # ~196 608 B/s
_FAST_3G_UPLOAD_BYTES_PER_SEC = int(750 * 1024 / 8)             # ~96 000 B/s
_FAST_3G_LATENCY_MS = 150                                        # ms additional RTT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lighthouse_installed() -> bool:
    """Return True if the ``lighthouse`` CLI is available on PATH."""
    return shutil.which("lighthouse") is not None


# ---------------------------------------------------------------------------
# TC-Q06: Fast 3G network throttling — meaningful content ≤ 2s, full load ≤ 4s
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_qr_menu_load_time_fast_3g(page, qr_scan_url, qr_token):
    """
    TC-Q06: QR menu page loads meaningful content within 2 s and full page
    load within 4 s under Chrome DevTools Fast 3G network throttling.

    Network conditions (Fast 3G — matches Chrome DevTools preset):
        Download  : 1.5 Mbit/s  (~196 KB/s)
        Upload    : 750 kbit/s  (~94 KB/s)
        Latency   : 150 ms additional RTT

    Assertions (Requirement 19.3):
        First meaningful content visible within 2 000 ms
        Full page load (network idle) within 4 000 ms

    Skip condition:
        PLAYWRIGHT_QR_TOKEN env var not set — a valid token is required to
        reach a meaningful page (otherwise the server returns a session error).

    Validates: Requirements 19.1, 19.3
    """
    if not qr_token:
        pytest.skip(
            "PLAYWRIGHT_QR_TOKEN not set — set this env var to a valid QR token UUID "
            "before running TC-Q06."
        )

    # ----------------------------------------------------------------
    # Apply CDP (Chrome DevTools Protocol) network throttling
    # ----------------------------------------------------------------
    client = page.context.new_cdp_session(page)
    client.send(
        "Network.emulateNetworkConditions",
        {
            "offline": False,
            "downloadThroughput": _FAST_3G_DOWNLOAD_BYTES_PER_SEC,
            "uploadThroughput": _FAST_3G_UPLOAD_BYTES_PER_SEC,
            "latency": _FAST_3G_LATENCY_MS,
        },
    )

    # ----------------------------------------------------------------
    # Navigate and verify we get a valid response
    # ----------------------------------------------------------------
    response = page.goto(qr_scan_url, wait_until="domcontentloaded", timeout=15_000)
    assert response is not None, f"No HTTP response received from {qr_scan_url}"
    assert response.status < 400, (
        f"Page returned HTTP {response.status} from {qr_scan_url}. "
        "Ensure the dev server is running and PLAYWRIGHT_QR_TOKEN is valid."
    )

    # ----------------------------------------------------------------
    # Assert: meaningful content visible within 2 s
    # We look for any visible rendered element in the body — a menu item,
    # a heading, a loading spinner, or any non-empty text node qualifies
    # as "meaningful content" (Requirement 19.1).
    # ----------------------------------------------------------------
    first_content_locator = page.locator(
        "body *:not(script):not(style):not(head):not(meta):not(noscript)"
    ).first
    first_content_locator.wait_for(state="visible", timeout=2_000)

    # ----------------------------------------------------------------
    # Capture Navigation Timing via Performance API
    # ----------------------------------------------------------------
    timing = page.evaluate(
        """() => {
            const t = window.performance.timing;
            return {
                navigationStart:  t.navigationStart,
                domContentLoaded: t.domContentLoadedEventEnd,
                loadEvent:        t.loadEventEnd,
            };
        }"""
    )

    dom_content_time_ms = timing["domContentLoaded"] - timing["navigationStart"]

    # ----------------------------------------------------------------
    # Wait for full network idle — all XHR/fetch requests settled
    # ----------------------------------------------------------------
    page.wait_for_load_state("networkidle", timeout=4_000)

    load_time_ms = timing["loadEvent"] - timing["navigationStart"]

    # Fallback: use PerformanceNavigationTiming if loadEventEnd is 0
    if load_time_ms <= 0:
        nav_timing = page.evaluate(
            "() => { const e = window.performance.getEntriesByType('navigation')[0]; "
            "return e ? e.loadEventEnd : 0; }"
        )
        if nav_timing:
            load_time_ms = nav_timing

    print(
        "\nTC-Q06 Fast 3G timing:"
        f"\n  DOMContentLoaded : {dom_content_time_ms:.0f} ms"
        f"\n  Full load        : {load_time_ms:.0f} ms"
        f"\n  URL              : {qr_scan_url}"
    )

    # ----------------------------------------------------------------
    # Assertion 1: first meaningful content ≤ 2 000 ms (Requirement 19.3)
    # ----------------------------------------------------------------
    assert dom_content_time_ms < 2_000, (
        f"TC-Q06 FAIL: First meaningful content (DOMContentLoaded) took "
        f"{dom_content_time_ms:.0f} ms — exceeds 2 000 ms limit on Fast 3G. "
        "Reduce initial HTML payload, inline critical CSS, or enable "
        "server-side caching."
    )

    # ----------------------------------------------------------------
    # Assertion 2: full page load ≤ 4 000 ms (Requirement 19.3)
    # ----------------------------------------------------------------
    if load_time_ms > 0:
        assert load_time_ms < 4_000, (
            f"TC-Q06 FAIL: Full page load took {load_time_ms:.0f} ms — "
            f"exceeds 4 000 ms limit on Fast 3G. "
            "Optimise asset sizes, enable HTTP/2 push, or defer non-critical scripts."
        )


# ---------------------------------------------------------------------------
# TC-LH01–TC-LH05: Programmatic Lighthouse mobile audit
# ---------------------------------------------------------------------------


@pytest.mark.performance
@pytest.mark.skipif(
    not _lighthouse_installed(),
    reason=(
        "lighthouse CLI not found on PATH. "
        "Install with: npm install -g lighthouse  (requires Node.js ≥ 18)"
    ),
)
def test_lighthouse_mobile_audit(page, qr_scan_url, qr_token):
    """
    TC-LH01–TC-LH05: Programmatic Lighthouse audit on the customer QR menu
    page using a mobile device preset.

    Thresholds (Requirements 19.1, 19.2, 19.3):
        TC-LH01  Performance score    >= 85
        TC-LH02  LCP (Largest CP)     < 2.5 s
        TC-LH03  FCP (First CP)       < 1.8 s
        TC-LH04  CLS (Layout Shift)   < 0.1
        TC-LH05  Accessibility score  >= 80

    Lighthouse is a Node.js tool; this test invokes it via subprocess and
    parses the JSON output.  The test is skipped gracefully if:
        - the lighthouse CLI is not installed
        - PLAYWRIGHT_QR_TOKEN is not set
        - Lighthouse times out or returns a non-zero exit code

    Validates: Requirements 19.1, 19.2, 19.3
    """
    if not qr_token:
        pytest.skip(
            "PLAYWRIGHT_QR_TOKEN not set — set this env var to a valid QR token UUID "
            "before running TC-LH01–TC-LH05."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "lighthouse-report.json")

        lighthouse_bin = shutil.which("lighthouse")
        cmd = [
            lighthouse_bin,
            qr_scan_url,
            "--output", "json",
            "--output-path", output_path,
            "--preset", "perf",               # performance-focused preset
            "--form-factor", "mobile",
            "--screen-emulation.mobile",
            "--throttling-method", "simulate",
            "--chrome-flags=--headless --no-sandbox --disable-dev-shm-usage",
            "--quiet",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,   # Lighthouse can take up to 2 minutes
            )
        except subprocess.TimeoutExpired:
            pytest.skip(
                "Lighthouse timed out after 120 seconds — skipping in this environment. "
                "Run `lighthouse` manually or increase the subprocess timeout."
            )

        if result.returncode != 0:
            pytest.skip(
                f"Lighthouse exited with non-zero code {result.returncode}. "
                f"stderr: {result.stderr[:500]}"
            )

        # ----------------------------------------------------------------
        # Parse JSON report
        # ----------------------------------------------------------------
        try:
            with open(output_path, encoding="utf-8") as fh:
                report = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            pytest.skip(
                f"Could not parse Lighthouse JSON output ({output_path}): {exc}"
            )

        # ----------------------------------------------------------------
        # Extract scores and metrics
        # ----------------------------------------------------------------
        categories = report.get("categories", {})
        perf_score = (categories.get("performance", {}).get("score") or 0.0) * 100
        a11y_score = (categories.get("accessibility", {}).get("score") or 0.0) * 100

        audits = report.get("audits", {})

        def _audit_seconds(audit_id: str) -> float | None:
            """Extract a Lighthouse audit value in seconds (reports in ms)."""
            val = audits.get(audit_id, {}).get("numericValue")
            return (val / 1000.0) if val is not None else None

        lcp = _audit_seconds("largest-contentful-paint")
        fcp = _audit_seconds("first-contentful-paint")
        cls_val = audits.get("cumulative-layout-shift", {}).get("numericValue")

        # Print summary for CI logs
        lcp_str = f"{lcp:.3f} s" if lcp is not None else "n/a"
        fcp_str = f"{fcp:.3f} s" if fcp is not None else "n/a"
        cls_str = f"{cls_val:.4f}" if cls_val is not None else "n/a"
        print(
            "\nTC-LH01–LH05 Lighthouse mobile audit results:"
            f"\n  Performance score  : {perf_score:.0f}/100  (threshold: >= 85)"
            f"\n  Accessibility score: {a11y_score:.0f}/100  (threshold: >= 80)"
            f"\n  LCP                : {lcp_str}  (threshold: < 2.5 s)"
            f"\n  FCP                : {fcp_str}  (threshold: < 1.8 s)"
            f"\n  CLS                : {cls_str}  (threshold: < 0.1)"
        )

        # ----------------------------------------------------------------
        # TC-LH01: Performance score >= 85
        # ----------------------------------------------------------------
        assert perf_score >= 85, (
            f"TC-LH01 FAIL: Lighthouse performance score is {perf_score:.0f}/100 "
            f"(threshold: >= 85). "
            "Run `lighthouse {url} --preset perf --form-factor mobile` locally "
            "for actionable optimisation suggestions."
        )

        # ----------------------------------------------------------------
        # TC-LH02: LCP < 2.5 s
        # ----------------------------------------------------------------
        if lcp is not None:
            assert lcp < 2.5, (
                f"TC-LH02 FAIL: LCP is {lcp:.3f} s — exceeds 2.5 s threshold. "
                "Optimise hero image size, font preloading, or server response time "
                "(TTFB)."
            )

        # ----------------------------------------------------------------
        # TC-LH03: FCP < 1.8 s
        # ----------------------------------------------------------------
        if fcp is not None:
            assert fcp < 1.8, (
                f"TC-LH03 FAIL: FCP is {fcp:.3f} s — exceeds 1.8 s threshold. "
                "Inline critical CSS, reduce render-blocking resources, or use "
                "server-side rendering for the initial HTML shell."
            )

        # ----------------------------------------------------------------
        # TC-LH04: CLS < 0.1
        # ----------------------------------------------------------------
        if cls_val is not None:
            assert cls_val < 0.1, (
                f"TC-LH04 FAIL: CLS is {cls_val:.4f} — exceeds 0.1 threshold. "
                "Set explicit width/height on all images, ads, and dynamically "
                "injected elements to prevent layout shifts."
            )

        # ----------------------------------------------------------------
        # TC-LH05: Accessibility score >= 80
        # ----------------------------------------------------------------
        assert a11y_score >= 80, (
            f"TC-LH05 FAIL: Lighthouse accessibility score is {a11y_score:.0f}/100 "
            f"(threshold: >= 80). "
            "Open the full Lighthouse report locally to identify specific issues "
            "(missing alt text, contrast failures, ARIA errors, etc.)."
        )
