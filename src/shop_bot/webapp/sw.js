/*
 * Alma VPN — PWA service worker (минимальный).
 * Назначение: сделать кабинет app.almapluse.ru устанавливаемым как отдельное
 * приложение (standalone). Chrome/Android требует зарегистрированный SW с
 * обработчиком fetch — поэтому он здесь есть, но НАМЕРЕННО passthrough:
 * НИЧЕГО не кэшируем (особенно HTML), чтобы деплой app.html/login.html через
 * git pull подхватывался мгновенно, без залипания старой версии.
 */
self.addEventListener('install', (event) => {
  // Активироваться сразу, не ждать закрытия старых вкладок.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // На всякий случай удаляем любые старые кэши, если когда-то появятся.
  event.waitUntil(
    (async () => {
      try {
        const names = await caches.keys();
        await Promise.all(names.map((n) => caches.delete(n)));
      } catch (e) { /* no-op */ }
      await self.clients.claim();
    })()
  );
});

self.addEventListener('fetch', (event) => {
  // Passthrough: отдаём запрос сети напрямую, без кэша.
  // Обработчик обязателен для критерия установки PWA, но ничего не подменяет.
});
