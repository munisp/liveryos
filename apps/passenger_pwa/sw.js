/* Service worker — cache-first shell, network-first API with cache fallback.
 * A booking viewed once is viewable forever (until storage pressure evicts),
 * which is the offline-tolerance the Nigeria market requires. */
"use strict";
const SHELL = "livery-passenger-shell-v1";
const ASSETS = [".", "index.html", "app.js", "manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.includes("/api/")) {
    // network-first; on failure the app falls back to its localStorage copy
    e.respondWith(fetch(e.request).catch(() => new Response(
      JSON.stringify({ ok: false, error: { code: "offline", message: "offline" } }),
      { status: 503, headers: { "Content-Type": "application/json" } })));
    return;
  }
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
      const copy = res.clone();
      caches.open(SHELL).then((c) => c.put(e.request, copy));
      return res;
    }).catch(() => caches.match("index.html")))
  );
});
