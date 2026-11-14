/* Only the application shell and static files are cached. API responses are never cached. */
const CACHE = 'pak-static-python-v2.0.0-rc12';
self.addEventListener('install', event => { event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(['/icon.svg', '/pak-logo.png', '/manifest.webmanifest']))); self.skipWaiting(); });
self.addEventListener('activate', event => { event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('pak-static-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim())); });
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api')) return;
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).then(response => { if (response.ok) { const copy = response.clone(); caches.open(CACHE).then(cache => cache.put('/', copy)); } return response; }).catch(() => caches.match('/').then(response => response || new Response('برای اتصال به سامانه، شبکه شرکت را بررسی کنید.', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } }))));
    return;
  }
  if (url.pathname.startsWith('/assets/') || ['/icon.svg', '/pak-logo.png', '/manifest.webmanifest'].includes(url.pathname)) {
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request).then(response => { if (response.ok) { const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); } return response; })));
  }
});
