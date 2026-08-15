// The shell, cached, so the device opens with no signal.
//
// Deliberately the SHELL ONLY: the page and nothing else. Caching the answers
// would mean a field client showing a unit that was never written, which is the
// one lie an assistant like this must not tell. What survives being offline is
// the ability to WORK — the queue in the page holds what was said until the
// node answers again.
const SHELL = "sg-shell-v1";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(["/"])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Never cache the API: an answer is about a graph that changes, and a cached
  // "Ho creato la US 12" would be a claim about a write that did not happen.
  if (url.pathname.startsWith("/v1/") || url.pathname === "/health") return;
  if (e.request.method !== "GET") return;
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).catch(
      () => caches.match("/")))
  );
});
