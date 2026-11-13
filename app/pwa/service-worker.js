const CACHE = 'classbrief-shell-v5';
const SHELL = ['/static/app.css?v=8', '/static/app.js?v=9', '/static/icons/icon.svg', '/static/icons/maskable.svg', '/manifest.webmanifest', '/offline'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))));
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
    return;
  }
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => caches.match('/offline')));
  }
});
self.addEventListener('push', event => {
  const data = event.data ? event.data.json() : {title: 'ClassBrief', body: 'Dein Briefing ist fertig.', url: '/'};
  event.waitUntil(self.registration.showNotification(data.title, {body: data.body, icon: '/static/icons/icon.svg', data: {url: data.url || '/'}}));
});
self.addEventListener('notificationclick', event => { event.notification.close(); event.waitUntil(clients.openWindow(event.notification.data.url)); });
