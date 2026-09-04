// The shell, cached, so the device opens with no signal.
//
// Deliberately the SHELL ONLY: the page and nothing else. Caching the answers
// would mean a field client showing a unit that was never written, which is the
// one lie an assistant like this must not tell. What survives being offline is
// the ability to WORK — the queue in the page holds what was said until the
// node answers again.
//
// THE SHELL NOW INCLUDES THE BRAND, and that is the point of vendoring it. The
// fetch handler below is cache-first, so the theme and the faces would end up
// cached after the first online load anyway — but "after the first online load"
// is exactly the case a trench does not offer. Precached, the assistant opens
// looking like itself on a device that has never had signal.
//
// ~190 KB for eight woff2 and a stylesheet: the price of not depending on a
// network, paid once, on a device that will spend its day without one.
// THE CACHE NAME IS GENERATED, NOT BUMPED BY HAND.
//
// `__SHELL_VERSION__` is substituted by the node when it serves this file (see
// `app/main.py::service_worker`) and is a digest of the shell's actual bytes.
// Hand-bumping a version is a step somebody forgets, and forgetting it means a
// device in a trench keeps serving yesterday's page from cache with no way to
// know — the worst shape of an offline bug, because it looks like it works.
//
// Left as a literal when nothing substitutes it, so opening this file straight
// from disk still gives a working worker.
const SHELL = "sg-shell-__SHELL_VERSION__";

// WHERE THIS WORKER LIVES — `/` in development, `/chat/` behind the node's Caddy.
//
// Derived from the worker's own URL and never written down, for the same reason
// the page derives its API base: on the shared https origin, `/brand/...` is not
// this app's `/brand/...`, and a precache list of absolute paths would quietly
// fill the cache with somebody else's files (or with 404s) while looking like it
// worked. Relative entries below resolve against this, which is also exactly the
// worker's scope.
const BASE = new URL("./", self.location.href).pathname;

// THE PRECACHE LIST IS GENERATED FROM `web/`, NOT WRITTEN HERE.
//
// It used to be these twelve lines by hand, and that was honest while the
// surface was one file. It stops being honest the moment the front-end has
// parts: every file added is one more thing that can fail to be in the cache,
// and the failure is invisible until somebody is in a trench with no signal.
//
// `__SHELL_FILES__` is substituted by the node with what is actually on disk
// under `web/` (`app/main.py::service_worker`). A font weight added to the
// brand, or a module added to the page, is cached because it is THERE — not
// because somebody remembered to add a line.
//
// The literal below is the fallback for reading this file straight from disk:
// the page and the theme, which is the minimum that makes the app open.
const SHELL_FILES = __SHELL_FILES__ || ["./", "./brand/stratigraph-theme.css"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL)
    // Individually, not `addAll`: that rejects the WHOLE install if one file is
    // missing, and an assistant with no service worker at all is a worse
    // outcome than one missing a font weight.
    .then((c) => Promise.all(SHELL_FILES.map(
      (f) => c.add(f).catch(() => undefined))))
    .then(() => self.skipWaiting()));
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
  //
  // Matched against BASE, not against `/`: under a prefix this app's API is at
  // `/chat/v1/...`, and the bare test would have cached every answer it was
  // meant to refuse.
  if (url.pathname.startsWith(BASE + "v1/") || url.pathname === BASE + "health") {
    return;
  }
  if (e.request.method !== "GET") return;
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).catch(
      () => caches.match(BASE)))
  );
});
