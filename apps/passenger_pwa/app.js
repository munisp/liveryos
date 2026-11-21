/* LiveryOS passenger PWA — offline-first tracking (Nigeria-market discipline:
 * every screen works from cache; live data is a bonus, never a prerequisite).
 *
 * Honesty rules enforced in UI:
 * - ETA is rendered WITH its source label ("~12 min (estimate)") — never a
 *   bare number. eta_source "none" renders "—".
 * - Offline state is announced ("offline — showing last known state") with
 *   the timestamp of the last successful update; nothing pretends to be live.
 */
"use strict";

const API = "/api/method/livery_core.api.passenger";   // bench REST mount (Phase 3 bench step)
const CENTRIFUGO_URL = "wss://centrifugo.liveryos.example/connection/websocket";
const $ = (id) => document.getElementById(id);

function tenantFromHost() {
  return location.hostname.split(".")[0] || "demo";
}

function cacheKey(ref) { return `livery.ride.${tenantFromHost()}.${ref}`; }

function fmtFare(minor, ccy) {
  if (minor == null) return "—";
  return new Intl.NumberFormat(undefined, { style: "currency", currency: ccy })
    .format(minor / 100);
}

function render(d, meta) {
  $("ride").hidden = false;
  $("svc").textContent = d.service_class;
  $("status").textContent = d.status.replace(/_/g, " ");
  $("pickup").textContent = d.pickup_address;
  $("dropoff").textContent = d.dropoff_address;
  $("when").textContent = new Date(d.pickup_at).toLocaleString();
  $("driver").textContent = d.driver_name || "—";
  $("vehicle").textContent = d.vehicle_label || "—";
  $("fare").textContent = fmtFare(d.quoted_minor, d.currency);
  $("eta").textContent = d.eta_minutes == null
    ? "—"
    : `~${d.eta_minutes} min (${d.eta_source})`;   // source label — contract rule
  const live = meta && meta.live;
  $("live").className = live ? "on" : "off";
  $("live").textContent = live
    ? "● live — updates as they happen"
    : "● offline — showing last known state";
  $("updated").textContent = meta && meta.at
    ? `Last updated ${new Date(meta.at).toLocaleString()}`
    : "";
}

async function load(ref) {
  $("err").textContent = "";
  const cached = localStorage.getItem(cacheKey(ref));
  if (cached) render(JSON.parse(cached).data, { live: false, at: JSON.parse(cached).at });
  try {
    const r = await fetch(`${API}.booking_view?ride_id=${encodeURIComponent(ref)}`);
    const j = await r.json();
    if (!j.ok) { $("err").textContent = j.error.message; return; }
    localStorage.setItem(cacheKey(ref), JSON.stringify({ data: j.data, at: new Date().toISOString() }));
    render(j.data, { live: true, at: new Date().toISOString() });
    subscribe(ref);
  } catch (e) {
    if (!cached) $("err").textContent = "No connection and no saved copy of this booking yet.";
  }
}

/* Live position + status via Centrifugo. If the socket can't connect
 * (weak Nigerian mobile data, captive portals), the cached view above
 * remains and the indicator honestly stays "offline". */
let cent = null;
function subscribe(ref) {
  if (typeof Centrifuge === "undefined") return;
  if (cent) cent.disconnect();
  cent = new Centrifuge(CENTRIFUGO_URL);
  const sub = cent.newSubscription(`tenant:${tenantFromHost()}#positions`);
  sub.on("publication", (ctx) => {
    const p = ctx.data;
    if (p.ride_id !== ref) return;
    const c = localStorage.getItem(cacheKey(ref));
    if (c) {
      const j = JSON.parse(c);
      j.data.eta_minutes = p.eta_minutes ?? j.data.eta_minutes;
      j.data.eta_source = p.eta_source || j.data.eta_source;
      j.at = new Date().toISOString();
      localStorage.setItem(cacheKey(ref), JSON.stringify(j));
      render(j.data, { live: true, at: j.at });
    }
  });
  cent.on("connected", () => { $("live").className = "on"; });
  cent.on("disconnected", () => {
    $("live").className = "off";
    $("live").textContent = "● offline — showing last known state";
  });
  sub.subscribe();
  cent.connect();
}

$("go").addEventListener("click", () => {
  const ref = $("ref").value.trim();
  if (ref) load(ref);
});
$("refresh").addEventListener("click", () => {
  const ref = $("ref").value.trim();
  if (ref) load(ref);
});
$("wa").addEventListener("click", () => {
  // WhatsApp-first sharing: the tracking link itself is the message — the
  // recipient needs no app install (PWA) and no account.
  const text = `My ride ${$("ref").value.trim()} — live status: ${location.href}`;
  window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, "_blank");
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
// Deep link: ?ref=RES-9001 from a WhatsApp message opens straight to tracking.
const q = new URLSearchParams(location.search).get("ref");
if (q) { $("ref").value = q; load(q); }
