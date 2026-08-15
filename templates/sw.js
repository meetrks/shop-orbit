// Service worker for the storefront's PWA install support. Registered
// from templates/base.html at the site root (see pages.views.service_worker
// for why). Deliberately conservative: this is a server-rendered Django +
// HTMX site, not a SPA, so the only thing worth caching offline is static
// assets (CSS/JS/fonts/icons) — HTML pages, and anything under /cart/,
// /payments/, /accounts/, /store-dashboard/, are always fetched fresh.
// Caching a product page would risk serving stale prices/stock; caching
// checkout/cart would risk stale CSRF tokens or stock reservations.
// Suffixed with the current deploy's SITE_VERSION (see
// pages.views.service_worker / config/settings/base.py) so this name
// changes on every release, which is what actually busts the cache below
// — activate() deletes any cache whose name doesn't match the current one.
const CACHE_NAME = "avr-static-{{ site_version }}";
const STATIC_ASSET_PATTERN = /^\/static\//;

self.addEventListener("install", () => {
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches
            .keys()
            .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);
    const isCacheableStaticAsset =
        event.request.method === "GET" &&
        url.origin === self.location.origin &&
        STATIC_ASSET_PATTERN.test(url.pathname);

    if (!isCacheableStaticAsset) {
        return;
    }

    event.respondWith(
        caches.open(CACHE_NAME).then((cache) =>
            cache.match(event.request).then((cached) => {
                if (cached) {
                    return cached;
                }
                return fetch(event.request).then((response) => {
                    if (response.ok) {
                        cache.put(event.request, response.clone());
                    }
                    return response;
                });
            })
        )
    );
});
