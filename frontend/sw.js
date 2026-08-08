/* Judah Scanner — Service Worker
   Caches static assets for offline use + PWA installability.
   Strategy: stale-while-revalidate for HTML, cache-first for static assets.
*/

const CACHE_NAME = 'judah-scanner-v3';
const STATIC_ASSETS = [
  '/',
  '/static/style.css',
  '/static/app.js',
  '/static/icon-192.svg',
  '/static/icon-512.svg',
];

// Install — pre-cache static assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

// Activate — clean old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

// Fetch — network first for API, cache-first for static
self.addEventListener('fetch', (event) => {
  const req = event.request;

  // API requests — always go to network
  if (req.url.includes('/api/') || req.url.includes('/ws') || req.url.includes('/ws-fusion')) {
    return;
  }

  // Static assets — cache first, fallback to network
  event.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached;

      return fetch(req).then((response) => {
        // Cache successful responses
        if (response && response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(req, clone);
          });
        }
        return response;
      }).catch(() => {
        // Offline — return cached index.html if available
        if (req.headers.get('accept')?.includes('text/html')) {
          return caches.match('/');
        }
      });
    })
  );
});
