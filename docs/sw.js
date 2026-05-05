const CACHE = 'seydyaar-v0.5.0';
const CORE = ['./', './index.html', './app.html', './styles.css', './home.js', './app.js', './manifest.json', './assets/logo.png'];
self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)));
  self.skipWaiting();
});
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => (k === CACHE ? null : caches.delete(k))));
    await self.clients.claim();
  })());
});
const isDynamic = (url) => url.pathname.includes('/latest/') || url.pathname.includes('/runs/');
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const accept = req.headers.get('accept') || '';
  if (req.mode === 'navigate' || accept.includes('text/html')) {
    event.respondWith(fetch(req).catch(() => caches.match('./app.html') || caches.match('./index.html')));
    return;
  }
  if (isDynamic(url)) {
    event.respondWith(fetch(req, { cache: 'no-store' }).catch(() => caches.match(req)));
    return;
  }
  event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
