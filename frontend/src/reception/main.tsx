import React from "react";
import { createRoot } from "react-dom/client";
import ReceptionDashboard from "./ReceptionDashboard";

const el = document.getElementById("reception-root");
if (el) {
  const branchId = el.dataset.branchId ?? "";
  const sessionKey = el.dataset.sessionKey ?? "";
  createRoot(el).render(<ReceptionDashboard branchId={branchId} sessionKey={sessionKey} />);
}
