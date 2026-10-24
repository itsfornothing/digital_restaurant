/**
 * staff-notifications.js — Browser notification + audio for new orders.
 * Included in base.html, used by all staff pages.
 */

let _audioCtx = null;

function _ensureAudioCtx() {
  if (!_audioCtx) {
    try {
      _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    } catch (e) {
      return null;
    }
  }
  return _audioCtx;
}

function playNotificationSound() {
  /* Vibration — works on mobile without user gesture */
  try { if (navigator.vibrate) navigator.vibrate(200); } catch (_) {}
  var ctx = _ensureAudioCtx();
  if (!ctx) return;
  if (ctx.state === "suspended") {
    ctx.resume().then(function () {
      _playBeep(ctx);
    }).catch(function () {});
    return;
  }
  _playBeep(ctx);
}

function _playBeep(ctx) {
  try {
    var now = ctx.currentTime;
    // Bell Chime Tone 1: A5 (880Hz)
    var osc1 = ctx.createOscillator();
    var gain1 = ctx.createGain();
    osc1.type = "sine";
    osc1.frequency.setValueAtTime(880, now);
    gain1.gain.setValueAtTime(0.4, now);
    gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
    osc1.connect(gain1);
    gain1.connect(ctx.destination);
    osc1.start(now);
    osc1.stop(now + 0.35);

    // Bell Chime Tone 2: D6 (1174.66Hz) after 130ms
    var osc2 = ctx.createOscillator();
    var gain2 = ctx.createGain();
    osc2.type = "sine";
    osc2.frequency.setValueAtTime(1174.66, now + 0.13);
    gain2.gain.setValueAtTime(0.45, now + 0.13);
    gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.6);
    osc2.connect(gain2);
    gain2.connect(ctx.destination);
    osc2.start(now + 0.13);
    osc2.stop(now + 0.6);
  } catch (e) {
    console.warn("Audio notification not available:", e);
  }
}

function requestNotificationPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }
}

function initAudioOnGesture() {
  var _gestureListener = function () {
    var ctx = _ensureAudioCtx();
    if (!ctx) return;
    if (ctx.state === "suspended") ctx.resume();
    document.removeEventListener("click", _gestureListener);
    document.removeEventListener("touchstart", _gestureListener);
  };
  document.addEventListener("click", _gestureListener);
  document.addEventListener("touchstart", _gestureListener);
}

function showOrderNotification(orderData) {
  if (!("Notification" in window)) return;
  const i18n = window.NOTIF_I18N || {};
  if (Notification.permission === "granted") {
    const table = orderData.table_number || orderData.table_id || "?";
    const itemCount = (orderData.items || []).length;
    const firstItem = orderData.items && orderData.items[0]
      ? orderData.items[0].menu_item_name || orderData.items[0].menu_item
      : "";
    const moreLabel = i18n.more || "more";
    const tableLabel = i18n.table || "Table";
    const title = i18n.newOrder || "New Order!";
    const summary = itemCount > 1
      ? `${firstItem} +${itemCount - 1} ${moreLabel}`
      : firstItem;
    try {
      const n = new Notification(title, {
        body: `${tableLabel} ${table}: ${summary}`,
        icon: "/static/favicon.svg",
        tag: "new-order",
        requireInteraction: true,
      });
      n.onclick = function () { window.focus(); this.close(); };
      setTimeout(() => n.close(), 8000);
    } catch (e) {
      console.warn("Notification failed:", e);
    }
  }
  playNotificationSound();
}

function showStatusNotification(data) {
  if (!("Notification" in window)) return;
  const i18n = window.NOTIF_I18N || {};
  const table = data.table_number || "?";
  const labels = {
    received: i18n.orderReceivedKitchen || "Order Received — Kitchen",
    preparing: i18n.preparingKitchen || "Preparing — Kitchen",
    ready: i18n.orderReadyServe || "Order Ready to Serve — Reception",
    served: i18n.orderServed || "Order Served",
    cancelled: i18n.orderCancelled || "Order Cancelled",
  };
  const title = labels[data.new_status] || (i18n.statusChanged || "Order Status Changed");
  const orderLabel = i18n.order || "Order";
  const tableLabel = i18n.table || "Table";
  const prevStatus = data.previous_status || (i18n.new || "new");
  const body = `${orderLabel} #${data.order_number || ""} — ${tableLabel} ${table}: ${prevStatus} → ${data.new_status}`;
  if (Notification.permission === "granted") {
    try {
      const n = new Notification(title, {
        body: body,
        icon: "/static/favicon.svg",
        tag: "order-status",
        requireInteraction: data.new_status === "ready",
      });
      n.onclick = function () { window.focus(); this.close(); };
      setTimeout(() => n.close(), 6000);
    } catch (e) {
      console.warn("Notification failed:", e);
    }
  }
  playNotificationSound();
}
