/**
 * Service worker чат-UI.
 *
 * Собирается `vite-plugin-pwa` в режиме `injectManifest` (см. `vite.config.ts`) и
 * отдаётся бэкендом из корня сайта — `/sw.js` (`app/routes/pages.py`). Именно корень
 * даёт scope `/`, который покрывает `/chat`: реальную страницу чата
 * (`templates/chat.html`). У `/chat-ui/sw.js` scope был бы ограничен `/chat-ui/`,
 * и навигацию на `/chat` service worker не контролировал бы.
 */
import { clientsClaim, skipWaiting, type WorkboxPlugin } from "workbox-core";
import {
  cleanupOutdatedCaches,
  matchPrecache,
  precacheAndRoute,
  type PrecacheEntry,
} from "workbox-precaching";
import { registerRoute } from "workbox-routing";
import { NetworkFirst } from "workbox-strategies";
import { ExpirationPlugin } from "workbox-expiration";
import { CacheableResponsePlugin } from "workbox-cacheable-response";

declare global {
  // Workbox объявляет `__WB_MANIFEST` на `ServiceWorkerGlobalScope`, а `self` в
  // lib.webworker типизирован как `WorkerGlobalScope` — объявляем на нём же.
  interface WorkerGlobalScope {
    __WB_MANIFEST: Array<PrecacheEntry | string>;
  }
}

/** Кеш GET-ответов API: список диалогов и их содержимое для чтения офлайн. */
const API_CACHE = "aichat-api";

/** Статическая оболочка SPA из сборки — без персональных данных. */
const SHELL_URL = "/chat-ui/index.html";

/** Что из API кешируем: только чтение своего (диалоги, настройки, навыки). */
const API_READ_PATTERN = /^\/api\/(conversations|settings|skills)(\/|$)/;

/**
 * Пути в precache-манифесте абсолютные (`/chat-ui/...`) — их проставляет
 * `injectManifest.modifyURLPrefix`, иначе Workbox разрешал бы относительные URL
 * от расположения самого SW, то есть от корня сайта, и все ассеты дали бы 404.
 */
precacheAndRoute(self.__WB_MANIFEST);
cleanupOutdatedCaches();

// Обновляемся сразу: старый SW не должен висеть до полного перезапуска браузера.
skipWaiting();
clientsClaim();

/**
 * Увидев 401 (сессия истекла), чистим кеш диалогов: данные не должны переживать
 * сессию, к которой они относились.
 */
const purgeCacheOnUnauthorized: WorkboxPlugin = {
  fetchDidSucceed: async ({ response }) => {
    if (response.status === 401) await caches.delete(API_CACHE);
    return response;
  },
};

/**
 * Чтение чатов офлайн. Сеть — источник истины, поэтому сетевого таймаута нет:
 * с ним список диалогов мог бы прийти из кеша сразу после создания нового чата.
 * Кеш подхватывается только тогда, когда сеть недоступна.
 */
registerRoute(
  ({ request, url }) =>
    request.method === "GET" &&
    API_READ_PATTERN.test(url.pathname) &&
    !url.pathname.endsWith("/download"),
  new NetworkFirst({
    cacheName: API_CACHE,
    plugins: [
      // 401/5xx в кеш не пишем, иначе офлайн они «проиграются» вместо данных.
      new CacheableResponsePlugin({ statuses: [200] }),
      purgeCacheOnUnauthorized,
      new ExpirationPlugin({
        maxEntries: 300,
        maxAgeSeconds: 60 * 60 * 24 * 7,
      }),
    ],
  }),
);

/**
 * Навигация на `/chat`: онлайн — всегда настоящая Jinja-страница (в ней email
 * пользователя, поэтому в кеш она не попадает), офлайн — статическая оболочка,
 * из которой приложение читает диалоги из кеша API.
 *
 * `setCatchHandler` здесь не подходит: Workbox зовёт catch-хендлер только когда
 * упал обработчик совпавшего роута, а не когда роут не найден вовсе.
 */
registerRoute(
  ({ request, url }) => request.mode === "navigate" && url.pathname === "/chat",
  async ({ request }) => {
    try {
      return await fetch(request);
    } catch {
      return (await matchPrecache(SHELL_URL)) ?? Response.error();
    }
  },
);

/**
 * Выход из аккаунта чистит кеш диалогов. `respondWith` здесь не вызываем:
 * ответом владеют роуты Workbox (и браузер), второй вызов бросил бы
 * InvalidStateError. `waitUntil` продлевает жизнь событию, пока идёт очистка.
 */
self.addEventListener("fetch", (event) => {
  // `self` типизирован как WorkerGlobalScope, где 'fetch' — не свой тип события.
  const { request, waitUntil } = event as FetchEvent;
  if (request.method !== "GET" && request.method !== "POST") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname !== "/logout") return;
  waitUntil(caches.delete(API_CACHE));
});
