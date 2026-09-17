self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
  let payload = { title: 'Auto-Wave', body: 'Nova atualização do dashboard' };

  try {
    payload = event.data ? event.data.json() : payload;
  } catch (error) {
    payload = { title: 'Auto-Wave', body: event.data ? event.data.text() : 'Nova atualização do dashboard' };
  }

  const options = {
    body: payload.body || 'Nova atualização do dashboard',
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    tag: 'auto-wave-push',
    data: {
      url: payload.url || '/',
    },
  };

  event.waitUntil(self.registration.showNotification(payload.title || 'Auto-Wave', options));
});

self.addEventListener('notificationclick', (event) => {
  event.preventDefault();
  const url = event.notification.data?.url || '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          return client.focus();
        }
      }

      return clients.openWindow(url);
    })
  );
});
