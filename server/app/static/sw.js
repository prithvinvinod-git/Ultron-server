const CACHE_NAME = 'ultron-v1';
const urlsToCache = ['/', '/manifest.json', '/favicon.png'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = event.request.url;
  if (url.includes('/auth') || url.includes('/events') || url.includes('/chat') || url.includes('/api')) return;
  event.respondWith(caches.match(event.request).then(r => r || fetch(event.request).catch(() => r)));
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.pathname.includes('/auth') || url.pathname.includes('/events') || url.pathname.includes('/chat')) {
    event.respondWith(fetch(event.request).catch(() => caches.match('/offline.html') || new Response('OFFLINE')));
    return;
  }
  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request).catch(() => caches.match('/offline.html')))
  );
});
