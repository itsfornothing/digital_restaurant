import React from "react";
import { createRoot } from "react-dom/client";
import KitchenDisplay from "./KitchenDisplay";

const el = document.getElementById("kds-root");
if (el) {
  const branchId = el.dataset.branchId ?? "";
  const sessionKey = el.dataset.sessionKey ?? "";
  const apiBase = window.location.origin;
  createRoot(el).render(<KitchenDisplay branchId={branchId} apiBase={apiBase} sessionKey={sessionKey} />);
}
