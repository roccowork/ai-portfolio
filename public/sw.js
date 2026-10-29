/**
 * 离线缓存已下线（2026-10-13）。这个文件只用来清理已经装在访客浏览器里的旧 Service Worker：
 * 浏览器检查到 sw.js 变化后会安装这个版本，它删掉缓存、注销自己，再刷新打开着的页面。
 * 不能直接删除 sw.js：更新检查拿到 404 时，旧的 Service Worker 会一直留在浏览器里。
 * 保留到 2026-12 左右，之后可以删掉这个文件和 eslint.config.js 里对应的配置。
 */
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.filter((k) => k.startsWith('ops-desk-')).map((k) => caches.delete(k)));
      await self.registration.unregister();
      const clients = await self.clients.matchAll({ type: 'window' });
      for (const client of clients) client.navigate(client.url);
    })(),
  );
});
