const CACHE_NAME = "garmin-health-assistant-v1";
const APP_SHELL = [
  "/",
  "/manifest.webmanifest",
  "/assets/icon.png",
  "/assets/icon_128.png",
  "/assets/icon_256.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).catch(() => undefined)
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
    ))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(req).catch(() => new Response(JSON.stringify({
      ok: false,
      error: "当前离线，无法请求本地服务。"
    }), {
      headers: { "Content-Type": "application/json" },
      status: 503
    })));
    return;
  }

  event.respondWith(
    fetch(req).then((resp) => {
      const copy = resp.clone();
      caches.open(CACHE_NAME).then((cache) => cache.put(req, copy)).catch(() => undefined);
      return resp;
    }).catch(() => caches.match(req).then((cached) => cached || caches.match("/")))
  );
});
