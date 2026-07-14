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
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = "sine";
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.setValueAtTime(660, ctx.currentTime + 0.15);
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.3);
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
  if (Notification.permission === "granted") {
    const table = orderData.table_number || orderData.table_id || "?";
    const itemCount = (orderData.items || []).length;
    const firstItem = orderData.items && orderData.items[0]
      ? orderData.items[0].menu_item_name || orderData.items[0].menu_item
      : "";
    const summary = itemCount > 1
      ? `${firstItem} +${itemCount - 1} more`
      : firstItem;
    try {
      const n = new Notification("New Order!", {
        body: `Table ${table}: ${summary}`,
        icon: "/static/eating.svg",
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
  const table = data.table_number || "?";
  const labels = {
    received: "Order Received — Kitchen",
    preparing: "Preparing — Kitchen",
    ready: "Order Ready to Serve — Reception",
    served: "Order Served",
    cancelled: "Order Cancelled",
  };
  const title = labels[data.new_status] || "Order Status Changed";
  const body = `Order #${data.order_number || ""} — Table ${table}: ${data.previous_status || "new"} → ${data.new_status}`;
  if (Notification.permission === "granted") {
    try {
      const n = new Notification(title, {
        body: body,
        icon: "/static/eating.svg",
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
