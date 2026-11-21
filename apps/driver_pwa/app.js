/* LiveryOS driver PWA — status transitions + GPS position reporting.
 *
 * Offline-first (Nigeria discipline):
 * - Position fixes queue in localStorage when the network drops and flush
 *   in order on reconnect. Each fix carries the DEVICE timestamp with the
 *   device's real UTC offset — the server rejects naive timestamps, so we
 *   always send ISO-8601 with offset.
 * - The UI grays buttons per the ride state machine, but the SERVER is the
 *   enforcer (illegal_transition errors render as-is).
 * - Queued count is always visible — a driver knows exactly what hasn't
 *   reached dispatch yet; nothing pretends to have been sent.
 */
"use strict";

const API = "/api/method/livery_core.api.driver";      // bench REST mount (Phase 3 bench step)
const REPORT_MS = 15000;
const $ = (id) => document.getElementById(id);

const TRANSITIONS = {   // client-side hints ONLY — server re-validates
  ASSIGNED:    ["EN_ROUTE", "NO_SHOW", "QUOTED"],
  EN_ROUTE:    ["AT_PICKUP", "CANCELLED"],
  AT_PICKUP:   ["IN_PROGRESS", "NO_SHOW"],
  IN_PROGRESS: ["COMPLETED"],
  QUOTED:      [], COMPLETED: [], INVOICED: [], CANCELLED: [], NO_SHOW: [],
};
const LABELS = {
  EN_ROUTE: "En route", AT_PICKUP: "At pickup", IN_PROGRESS: "Start trip",
  COMPLETED: "Complete", NO_SHOW: "No show", CANCELLED: "Cancel", QUOTED: "Release back",
};

const state = { status: null, watchId: null, timer: null, lastFix: null };

function tenantFromHost() { return location.hostname.split(".")[0] || "demo"; }
function queueKey() { return `livery.driver.queue.${tenantFromHost()}`; }
function getQueue() { return JSON.parse(localStorage.getItem(queueKey()) || "[]"); }
function setQueue(q) {
  localStorage.setItem(queueKey(), JSON.stringify(q));
  $("queue").textContent = q.length ? `${q.length} position(s) queued — will send when back online` : "";
}

async function post(path, body) {
  const r = await fetch(`${API}.${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

async function transition(target) {
  $("err").textContent = "";
  try {
    const j = await post("transition", {
      ride_id: $("ride").value.trim(),
      driver_id: $("driver").value.trim(),
      target,
    });
    if (!j.ok) { $("err").textContent = j.error.message; return; }
    state.status = j.data.to;
    renderPanel();
  } catch (e) {
    $("err").textContent = "No connection — status change NOT sent. Try again when online.";
  }
}

function renderPanel() {
  $("panel").hidden = false;
  $("status").textContent = (state.status || "—").replace(/_/g, " ");
  const box = $("buttons");
  box.innerHTML = "";
  (TRANSITIONS[state.status] || []).forEach((t) => {
    const b = document.createElement("button");
    b.textContent = LABELS[t] || t;
    if (t === "CANCELLED" || t === "NO_SHOW" || t === "QUOTED") b.className = "warn";
    b.onclick = () => transition(t);
    box.appendChild(b);
  });
  $("hint").textContent = (TRANSITIONS[state.status] || []).length
    ? ""
    : "No further actions on this ride.";
}

async function loadRide() {
  $("err").textContent = "";
  try {
    const j = await post("ride_status", { ride_id: $("ride").value.trim() });
    if (!j.ok) { $("err").textContent = j.error.message; return; }
    state.status = j.data.status;
    renderPanel();
  } catch (e) {
    $("err").textContent = "No connection — cannot load the ride right now.";
  }
}

function sendFix(fix) {
  post("position", fix).then((j) => {
    if (j.ok) { $("posline").textContent = `Sent ${fix.recorded_at}`; flushQueue(); }
    else enqueue(fix, j.error && j.error.message);
  }).catch(() => enqueue(fix));
}

function enqueue(fix, why) {
  const q = getQueue();
  q.push(fix);
  setQueue(q);
  $("posline").textContent = `Not sent${why ? ` (${why})` : ""} — queued`;
}

async function flushQueue() {
  let q = getQueue();
  while (q.length) {
    try {
      const j = await post("position", q[0]);
      if (!j.ok) { q.shift(); setQueue(q); continue; }  // server rejected: drop, keep the rest moving
      q.shift(); setQueue(q);
    } catch (e) { break; }  // still offline — stop, keep queue
  }
}

function captureAndSend() {
  if (!state.lastFix) return;
  const f = state.lastFix;
  sendFix({
    tenant: tenantFromHost(),
    driver_id: $("driver").value.trim(),
    device_id: `pwa-${tenantFromHost()}-${$("driver").value.trim()}`,
    lat: f.coords.latitude, lng: f.coords.longitude,
    speed_kmh: f.coords.speed != null ? Math.round(f.coords.speed * 3.6 * 10) / 10 : 0,
    heading: f.coords.heading || 0,
    recorded_at: new Date(f.timestamp).toISOString(),   // always offset-carrying
  });
}

$("toggleGps").addEventListener("click", () => {
  if (state.watchId != null) {
    navigator.geolocation.clearWatch(state.watchId);
    clearInterval(state.timer);
    state.watchId = null;
    $("gps").className = "off"; $("gps").textContent = "● GPS off";
    $("toggleGps").textContent = "Start position reporting";
    return;
  }
  if (!navigator.geolocation) { $("err").textContent = "This device has no geolocation."; return; }
  state.watchId = navigator.geolocation.watchPosition(
    (fix) => { state.lastFix = fix; },
    (e) => { $("err").textContent = `GPS error: ${e.message}`; },
    { enableHighAccuracy: true, maximumAge: 5000 });
  state.timer = setInterval(captureAndSend, REPORT_MS);
  $("gps").className = "on"; $("gps").textContent = `● GPS on (every ${REPORT_MS / 1000}s)`;
  $("toggleGps").textContent = "Stop position reporting";
});

$("load").addEventListener("click", loadRide);
window.addEventListener("online", flushQueue);
setQueue(getQueue());  // render any leftover queue from a previous session
