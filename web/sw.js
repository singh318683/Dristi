const CACHE = 'drishti-v48';
const FRESH = ['/', '/index.html', '/connect.js', '/birthpicker.js', '/sw.js'];
const FILES = ['/', '/index.html', '/birthpicker.js', '/connect.js', '/manifest.json', '/icon-192.png', '/icon-512.png', '/privacy.html', '/terms.html'];
function freshRequest(url) {
  return new Request(url, {cache: 'no-store'});
}
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(cache => Promise.all(FILES.map(path => {
    const request = FRESH.indexOf(path) >= 0 ? freshRequest(path) : path;
    return fetch(request).then(response => cache.put(path, response));
  }))).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener('fetch', e => {
  const path = new URL(e.request.url).pathname;
  if (e.request.method !== 'GET' || path.startsWith('/api/') || path.startsWith('/.well-known/')) return;
  const request = FRESH.indexOf(path) >= 0 ? freshRequest(e.request.url) : e.request;
  e.respondWith(fetch(request).then(r => { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return r; }).catch(() => caches.match(e.request).then(r => r || caches.match('/index.html'))));
});
