// Service Worker — no-op for development (bypasses all caching)
// Re-enable caching logic for production deployment.
self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
