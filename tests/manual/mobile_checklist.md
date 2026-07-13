# Mobile Responsiveness Manual Test Checklist

**Feature:** Customer QR Menu — Mobile Responsiveness  
**Sprint:** 20 (Weeks 47–48)  
**Requirements:** 19.1, 19.2, 19.3  
**Related Test Cases:** TC-MOB01–TC-MOB05

---

## Instructions for Testers

1. Before starting, confirm all **prerequisites** in the section below are satisfied.
2. Execute each test case in the order listed: TC-MOB01 → TC-MOB05.
3. For each step, record the **Actual Result** observed and mark **Pass/Fail**.
4. Leave no field blank — write "N/A" if a step cannot be performed for a documented reason.
5. Open a defect ticket for every failed step and record the ticket ID in the **Defect Log** at the bottom.
6. After completing all test cases, complete the **Sign-off** section.
7. Commit this checklist (with results filled in) to the repository alongside the build under test.

---

## Prerequisites

Before executing these test cases, confirm:

- [ ] The staging or production environment is accessible and responding
- [ ] At least one valid QR code URL is available (`/qr/scan/<token>/`)
- [ ] The branch under test has ≥ 3 active menu items configured
- [ ] Chrome DevTools is available for network throttling (TC-MOB04)
- [ ] Physical devices **or** Chrome DevTools device emulation is configured
- [ ] The browser console can be observed for JavaScript errors

---

## TC-MOB01 — iPhone SE (375 px viewport)

| Field | Value |
|-------|-------|
| **Test Case ID** | TC-MOB01 |
| **Description** | Verify the customer QR menu renders correctly on an iPhone SE viewport (375 px width): no horizontal scroll, readable text, and tappable buttons |
| **Device / Condition** | iPhone SE — 375 × 667 px viewport, Wi-Fi network, portrait orientation |
| **Browser** | Safari on physical device **or** Chrome DevTools → Device: iPhone SE |

### Steps to Test

| # | Step | Expected Result | Actual Result | Pass / Fail |
|---|------|-----------------|---------------|-------------|
| 1 | Open the QR menu URL (e.g. `https://{tenant}.platform.com/qr/scan/{token}/`) in the browser | Page loads and the branded digital menu is visible; HTTP status 200; no error page displayed | | |
| 2 | Without scrolling vertically, check for horizontal scroll by attempting to scroll left or right | **No horizontal scroll** — all content fits within the 375 px viewport width; the page does not extend beyond the screen edges | | |
| 3 | Read menu item names, prices, descriptions, and labels without zooming in | Text is legible at default zoom; all font sizes are ≥ 14 px; no characters are clipped or truncated mid-word | | |
| 4 | Locate the "Add to Cart" button (or equivalent primary action button) for any menu item | Button is visible within the viewport without horizontal scrolling; tappable area is ≥ 44 × 44 px per WCAG 2.5.5 | | |
| 5 | Tap "Add to Cart" for one item, proceed through the cart, and confirm an order | Order confirmation screen appears; the entire flow completes without JS errors in the browser console | | |

**Tester:** ___________________________  
**Date:** ___________________________

---

## TC-MOB02 — Samsung Galaxy S20 (360 px viewport)

| Field | Value |
|-------|-------|
| **Test Case ID** | TC-MOB02 |
| **Description** | Verify the customer QR menu renders correctly on a Samsung Galaxy S20 viewport (360 px width): no horizontal scroll, readable text, and touch targets ≥ 44 px |
| **Device / Condition** | Samsung Galaxy S20 — 360 × 800 px viewport, Wi-Fi network, portrait orientation |
| **Browser** | Chrome for Android on physical device **or** Chrome DevTools → Device: Galaxy S20 Ultra |

### Steps to Test

| # | Step | Expected Result | Actual Result | Pass / Fail |
|---|------|-----------------|---------------|-------------|
| 1 | Open the QR menu URL in the browser | Page loads and the branded digital menu is visible; HTTP status 200; no error page displayed | | |
| 2 | Check for horizontal scroll by attempting to swipe left or right | **No horizontal scroll** — all content fits within the 360 px viewport width | | |
| 3 | Read menu item names, prices, and descriptions without zooming | Text is legible at default zoom; font size ≥ 14 px; descriptions do not overlap adjacent elements | | |
| 4 | Locate and tap the primary action button (e.g. "Add to Cart") for any item | Touch target is ≥ 44 × 44 px; button responds to tap without requiring precise finger placement | | |
| 5 | Add an item to the cart and place an order | Order confirmation screen appears; no JavaScript errors logged in the console | | |

**Tester:** ___________________________  
**Date:** ___________________________

---

## TC-MOB03 — iPad Mini (768 px viewport)

| Field | Value |
|-------|-------|
| **Test Case ID** | TC-MOB03 |
| **Description** | Verify the customer QR menu adapts to tablet width (768 px): menu items display in an appropriate grid layout and the cart panel is accessible |
| **Device / Condition** | iPad Mini — 768 × 1024 px viewport, Wi-Fi network, portrait orientation |
| **Browser** | Safari on physical iPad Mini **or** Chrome DevTools → Device: iPad Mini |

### Steps to Test

| # | Step | Expected Result | Actual Result | Pass / Fail |
|---|------|-----------------|---------------|-------------|
| 1 | Open the QR menu URL in the browser | Page loads and the branded digital menu is visible; HTTP status 200 | | |
| 2 | Observe the menu item layout at 768 px width | Menu items are displayed in a **multi-column grid** (≥ 2 columns) appropriate for tablet width; items are not stretched to fill the full viewport in a single column | | |
| 3 | Check whether a cart/order summary panel is visible alongside the menu content | A sidebar cart or inline cart summary panel is visible without requiring navigation to a separate page | | |
| 4 | Add ≥ 2 different items to the cart using the tablet layout | Items appear in the cart; quantities update correctly; no layout overflow occurs | | |
| 5 | Proceed through the checkout flow and confirm an order | Order confirmation screen appears; the full ordering flow functions correctly on the tablet layout | | |

**Tester:** ___________________________  
**Date:** ___________________________

---

## TC-MOB04 — Slow 3G Network Simulation (~400 kbps)

| Field | Value |
|-------|-------|
| **Test Case ID** | TC-MOB04 |
| **Description** | Verify the customer QR menu loads progressively under Slow 3G conditions: a loading indicator is shown immediately, images load progressively, no layout shift occurs, and content is visible within 8 seconds |
| **Device / Condition** | Any mobile device or Chrome DevTools with "Slow 3G" throttling (≈ 400 kbps download, 400 ms additional latency) |
| **Browser** | Chrome DevTools → Network tab → Throttling: "Slow 3G" |

### Steps to Test

| # | Step | Expected Result | Actual Result | Pass / Fail |
|---|------|-----------------|---------------|-------------|
| 1 | Open Chrome DevTools → Network tab → select "Slow 3G" throttling preset | Network is throttled to ≈ 400 kbps; the DevTools network panel shows the active throttle profile | | |
| 2 | Navigate to the QR menu URL and observe the page immediately after navigation begins | A loading indicator (spinner, skeleton screen, or progress bar) is visible **within 1 second** of the navigation starting | | |
| 3 | Wait for menu item images to appear | Images load progressively from top to bottom or via progressive JPEG/WebP; a placeholder or skeleton is shown in the image area before the image resolves | | |
| 4 | Wait for the full page to become interactive | All menu item names, prices, and "Add to Cart" buttons are visible and functional **within 8 seconds** of page navigation | | |
| 5 | After page load, open the browser console (F12 → Console tab) | **Zero JavaScript errors** are logged; any network warnings are informational only | | |
| 6 | Attempt to add an item to the cart and start an order while on Slow 3G | The cart and order flow remain functional; no broken UI states or unresponsive buttons | | |

**Tester:** ___________________________  
**Date:** ___________________________

---

## TC-MOB05 — Portrait / Landscape Rotation

| Field | Value |
|-------|-------|
| **Test Case ID** | TC-MOB05 |
| **Description** | Verify the customer QR menu adapts correctly when the device is rotated between portrait and landscape orientations on both iPhone SE and Samsung Galaxy S20 |
| **Device / Condition** | iPhone SE (375 × 667 portrait → 667 × 375 landscape) and Samsung Galaxy S20 (360 × 800 portrait → 800 × 360 landscape), Wi-Fi network |
| **Browser** | Safari / Chrome on physical device **or** Chrome DevTools device emulation with orientation toggle |

### Steps to Test

| # | Step | Expected Result | Actual Result | Pass / Fail |
|---|------|-----------------|---------------|-------------|
| 1 | Open the QR menu URL on iPhone SE in **portrait** orientation (375 × 667 px) | Menu displays correctly in portrait; no horizontal scroll; content is readable | | |
| 2 | Rotate iPhone SE to **landscape** orientation (667 × 375 px) | Layout **reflows** to the wider viewport; no content is clipped, overlapped, or cut off; the page does not require a manual refresh | | |
| 3 | Scroll through the full menu in landscape on iPhone SE | All menu items remain visible and readable; no horizontal scroll in landscape; buttons remain tappable | | |
| 4 | Rotate back to **portrait** and verify the layout restores correctly | Layout returns to the portrait arrangement; no visual regressions or broken elements | | |
| 5 | Repeat steps 1–4 on **Samsung Galaxy S20** (portrait: 360 × 800, landscape: 800 × 360) | Same results as iPhone SE; layout adapts to the 800 × 360 landscape viewport; menu remains fully usable | | |
| 6 | While in landscape on either device, locate and tap the cart / checkout button | The cart button is visible and tappable in landscape orientation; the checkout flow opens without errors | | |

**Tester:** ___________________________  
**Date:** ___________________________

---

## Amharic Language Supplementary Checks

If the tenant under test has Amharic (አማርኛ) configured as the default language, additionally verify:

| # | Check | Expected Result | Actual Result | Pass / Fail |
|---|-------|-----------------|---------------|-------------|
| 1 | Amharic text renders on the menu page | Noto Sans Ethiopic font loads; all Ethiopic characters display correctly without boxes, placeholders, or garbled glyphs | | |
| 2 | Line height for Ethiopic text | Lines do not overlap; Ethiopic stacked character forms (fidel) have adequate vertical spacing | | |
| 3 | Amharic text does not cause layout overflow | Long Amharic text in menu item names or descriptions does not cause horizontal overflow or break the grid layout on any tested viewport | | |

**Tester:** ___________________________  
**Date:** ___________________________

---

## Sign-off

| Field | Value |
|-------|-------|
| **All TC-MOB01–MOB05 passed** | ☐ Yes &nbsp; ☐ No |
| **Amharic supplementary checks passed** | ☐ Yes &nbsp; ☐ No &nbsp; ☐ Not applicable |
| **Tester name** | ___________________________________ |
| **Tester role** | ___________________________________ |
| **Review date** | ___________________________________ |
| **Build / commit SHA** | ___________________________________ |
| **Environment** | ☐ Staging &nbsp; ☐ Production &nbsp; ☐ Local |
| **Browser versions tested** | ___________________________________ |
| **Defects raised** | _(list ticket IDs, or "None")_ |

---

## Defect Log

| Defect ID | TC ID | Step # | Description | Severity | Status | Assignee |
|-----------|-------|--------|-------------|----------|--------|----------|
| | | | | | | |

---

*Last updated: Sprint 20 (Task 20.6) — Requirements 19.1, 19.2, 19.3*
