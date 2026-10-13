const VERSION = 'v4';
const SHELL_CACHE = 'narrator-shell-' + VERSION;
const FONT_CACHE = 'narrator-fonts-v1';
const SHELL_URLS = [
  '/', '/manifest.json', '/icon-192.png', '/icon-512.png', '/extract.js',
  '/vendor/pdf.min.js', '/vendor/pdf.worker.min.js', '/vendor/mammoth.browser.min.js', '/vendor/jszip.min.js',
];
const PAGE_KEYS = new Set(['/', '/extract.js']);

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => Promise.allSettled(SHELL_URLS.map((u) => cache.add(new Request(u, { cache: 'reload' })))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('narrator-shell-') && k !== SHELL_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function notifyUpdate() {
  self.clients.matchAll({ type: 'window' }).then((cs) => cs.forEach((c) => c.postMessage({ type: 'update-ready' })));
}

/* Open instantly from the saved copy; fetch a fresh one in the background and tell the page if it changed. */
function openFromCache(request, cacheKey) {
  return caches.open(SHELL_CACHE).then(async (cache) => {
    const hit = await cache.match(cacheKey);
    const refresh = fetch(request).then(async (response) => {
      if (response && response.ok) {
        if (hit) {
          try {
            const [a, b] = await Promise.all([hit.clone().text(), response.clone().text()]);
            if (a !== b) notifyUpdate();
          } catch (e) {}
        }
        await cache.put(cacheKey, response.clone());
      }
      return response;
    });
    refresh.catch(() => {});
    return hit || refresh;
  });
}

function staleWhileRevalidate(request, cacheName) {
  return caches.open(cacheName).then((cache) =>
    cache.match(request).then((hit) => {
      const refresh = fetch(request).then((response) => {
        if (response && (response.ok || response.type === 'opaque')) cache.put(request, response.clone());
        return response;
      }).catch(() => hit);
      return hit || refresh;
    })
  );
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);

  if (url.origin === self.location.origin) {
    if (url.pathname.startsWith('/api/')) return;
    if (request.mode === 'navigate' && url.pathname === '/' && url.searchParams.has('fresh')) {
      event.respondWith(
        fetch(request).then((response) => {
          if (response && response.ok) caches.open(SHELL_CACHE).then((c) => c.put('/', response.clone()));
          return response;
        }).catch(() => caches.open(SHELL_CACHE).then((c) => c.match('/')))
      );
      return;
    }
    if (request.mode === 'navigate') {
      if (url.pathname === '/') event.respondWith(openFromCache(request, '/'));
      return;
    }
    if (PAGE_KEYS.has(url.pathname)) {
      event.respondWith(openFromCache(request, url.pathname));
      return;
    }
    event.respondWith(staleWhileRevalidate(request, SHELL_CACHE));
    return;
  }

  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    event.respondWith(staleWhileRevalidate(request, FONT_CACHE));
  }
});
