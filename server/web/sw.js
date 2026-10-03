const VERSION = 'v3';
const SHELL_CACHE = 'narrator-shell-' + VERSION;
const FONT_CACHE = 'narrator-fonts-v1';
const SHELL_URLS = [
  '/', '/manifest.json', '/icon-192.png', '/icon-512.png', '/extract.js',
  '/vendor/pdf.min.js', '/vendor/pdf.worker.min.js', '/vendor/mammoth.browser.min.js', '/vendor/jszip.min.js',
];
const NETWORK_FIRST = new Set(['/', '/extract.js']);

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

function networkFirst(request, cacheKey, timeoutMs) {
  return caches.open(SHELL_CACHE).then((cache) => {
    const network = fetch(request).then((response) => {
      if (response && response.ok) cache.put(cacheKey, response.clone());
      return response;
    });
    network.catch(() => {});
    const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), timeoutMs));
    return Promise.race([network, timeout]).catch(() =>
      cache.match(cacheKey).then((hit) => hit || network)
    );
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
    if (request.mode === 'navigate') {
      event.respondWith(networkFirst(request, '/', 3500));
      return;
    }
    if (NETWORK_FIRST.has(url.pathname)) {
      event.respondWith(networkFirst(request, url.pathname, 3500));
      return;
    }
    event.respondWith(staleWhileRevalidate(request, SHELL_CACHE));
    return;
  }

  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    event.respondWith(staleWhileRevalidate(request, FONT_CACHE));
  }
});
