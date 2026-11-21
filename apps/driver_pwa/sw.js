/* Driver PWA service worker — shell cache only. Position reports are queued
 * by app.js in localStorage (they must be sent in order, so we never let the
 * SW swallow them into a generic cache). */
"use strict";
const SHELL = "livery-driver-shell-v1";
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
  if (e.request.method !== "GET") return;   // POSTs go straight to network (queue handles failures)
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
      const copy = res.clone();
      caches.open(SHELL).then((c) => c.put(e.request, copy));
      return res;
    }).catch(() => caches.match("index.html")))
  );
});
