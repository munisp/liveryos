/* LiveryOS corporate portal (GA corporate-booking-tool parity).
 *
 * The portal NEVER books around policy: it calls the policy evaluation
 * endpoint first (livery_core.widget.corporate.evaluate_booking on the
 * bench) and renders the decision verbatim — including "needs approval".
 * A rejected booking shows the rejection reason; there is no "book anyway"
 * button. PO-required accounts get a hard-required PO field client-side
 * too, but the server decision is the only one that counts.
 */
"use strict";

const API = "/api/method/livery_core.api.corporate";
const $ = (id) => document.getElementById(id);
const CLASSES = ["economy", "business", "executive", "sprinter"];

let account = null;   // server-trimmed account view: travelers, centers, flags

async function api(path, body) {
  const res = await fetch(`${API}.${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error(`request failed (${res.status})`);
  return (await res.json()).message;
}

function fmt(minor, ccy) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: ccy })
    .format(minor / 100);
}

async function init() {
  CLASSES.forEach((c) => $("cls").appendChild(new Option(c, c)));
  try {
    account = await api("account_view", { tenant: tenantFromHost() });
  } catch (e) {
    $("decision").hidden = false;
    $("decision").textContent = `Portal unavailable — ${e.message}`;
    return;
  }
  account.travelers.forEach((t) =>
    $("traveler").appendChild(new Option(t.name, t.traveler_id)));
  account.cost_centers.forEach((c) =>
    $("center").appendChild(new Option(`${c.code} — ${c.name}`, c.code)));
  $("ponote").textContent = account.po_required
    ? `This account requires a PO number on every booking (${account.currency} billing).`
    : "PO optional on this account.";
  if (account.po_required) $("po").required = true;
  $("decision").hidden = false;
  $("decision").textContent =
    `${account.name} — monthly billing in ${account.currency}. ` +
    "Policy is checked before every booking; rejections are final here.";
}

function tenantFromHost() { return location.hostname.split(".")[0] || "demo"; }

$("book").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const box = $("decision");
  box.classList.remove("err");
  box.hidden = false;
  box.textContent = "Checking policy…";
  try {
    const d = await api("evaluate_and_book", {
      tenant: tenantFromHost(),
      traveler_id: $("traveler").value,
      pickup: $("pickup").value,
      dropoff: $("dropoff").value,
      pickup_at: $("when").value,
      service_class: $("cls").value,
      cost_center: $("center").value,
      po_number: $("po").value,
    });
    if (d.needs_approval) {
      box.textContent = `Sent for approval (${d.approver}) — ${d.reason}. ` +
        `Estimated ${fmt(d.quoted_minor, d.currency)}.`;
    } else {
      box.textContent = `Booked — ${d.sales_order}. ${d.reason}. ` +
        `Billed to ${$("center").value} at month-end (${d.currency}).`;
    }
  } catch (e) {
    // Policy rejections arrive as 4xx with the readable reason — show it as-is.
    box.classList.add("err");
    box.textContent = e.message;
  }
});

init();
