// FINN service worker: app shell offline, data network-first with the last copy as fallback.
const SHELL = 'finn-shell-v7', DATA = 'finn-data-v1';
const ASSETS = ['/', '/app.js', '/styles.css', '/vendor/preact-htm.js', '/icon.svg', '/manifest.webmanifest', '/symbols.json', '/vendor/inter-latin.woff2', '/vendor/inter-latin-ext.woff2'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => ![SHELL, DATA].includes(k)).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

const networkFirst = async (req, cacheName) => {
  const cache = await caches.open(cacheName);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch {
    const hit = await cache.match(req);
    if (hit) return hit;
    throw new Error('offline');
  }
};

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.endsWith('/stream')) return;                // live research runs: never cache
  if (url.pathname.startsWith('/api/')) return e.respondWith(networkFirst(e.request, DATA));
  e.respondWith(networkFirst(e.request, SHELL));                 // shell: fresh when online, cached when not
});
