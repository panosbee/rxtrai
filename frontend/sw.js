/**
 * RxTrail — Service Worker v4
 * NETWORK-FIRST for JS/CSS/HTML (always get latest code).
 * Cache-first only for static assets (icons, fonts).
 * API calls pass through untouched.
 */
const CACHE_STATIC = 'rxtrail-static-v6';
const CACHE_PAGES = 'rxtrail-pages-v6';
const SW_VERSION = '6';

// Only cache truly static assets — never JS/CSS/HTML (they change on every deploy)
const PRECACHE = [
  '/manifest.json',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/apple-touch-icon.png',
  '/icons/favicon.png',
];

// ── Install: pre-cache immutable assets only ─────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_STATIC).then((cache) => {
      console.log('[SW v4] Pre-caching static assets...');
      return cache.addAll(PRECACHE).catch((err) => {
        console.warn('[SW v4] Pre-cache partial failure:', err);
      });
    })
  );
  // IMPORTANT: activate immediately — don't wait for all tabs to close
  self.skipWaiting();
});

// ── Activate: clean ALL caches including old versions, take control ────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((k) => k !== CACHE_STATIC && k !== CACHE_PAGES)
          .map((k) => {
            console.log('[SW v5] Deleting old cache:', k);
            return caches.delete(k);
          })
      );
    }).then(() => {
      // Force all clients to reload and get the new version
      console.log('[SW v5] Claiming clients, forcing reload');
      self.clients.claim();
      self.clients.matchAll().then((clients) => {
        clients.forEach((client) => {
          client.postMessage({ type: 'SW_UPDATED', version: SW_VERSION });
        });
      });
    })
  );
});

// ── Fetch: NETWORK-FIRST for everything dynamic ─────────────────────────
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Skip non-GET
  if (event.request.method !== 'GET') return;

  // API calls: pass through untouched
  if (url.pathname.startsWith('/auth/') ||
      url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/cases/') ||
      url.pathname.startsWith('/doctors') ||
      url.pathname.startsWith('/structures') ||
      url.pathname.startsWith('/skus') ||
      url.pathname.startsWith('/contacts') ||
      url.pathname.startsWith('/sources') ||
      url.pathname.startsWith('/queue/') ||
      url.pathname.startsWith('/notifications/') ||
      url.pathname.startsWith('/week/') ||
      url.pathname.startsWith('/appointments/') ||
      url.pathname.startsWith('/llm/') ||
      url.pathname.startsWith('/dashboard/') ||
      url.pathname.startsWith('/imports/') ||
      url.pathname.startsWith('/onboarding/') ||
      url.pathname.startsWith('/documents/') ||
      url.pathname.startsWith('/visits') ||
      url.pathname.startsWith('/settings') ||
      url.pathname.startsWith('/fulfilments') ||
      url.pathname.startsWith('/prescriptions') ||
      url.pathname.startsWith('/patients') ||
      url.pathname.startsWith('/export/') ||
      url.pathname.startsWith('/knowledge')) {
    return; // Let browser handle API calls normally
  }

  // JS, CSS, HTML files: NETWORK-FIRST (always get latest)
  // This is the CRITICAL fix — old SW cached auth.js, api.js, app.js
  if (url.pathname.endsWith('.js') || 
      url.pathname.endsWith('.css') || 
      url.pathname.endsWith('.html') ||
      url.pathname === '/app') {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          // Update cache in background
          const clone = response.clone();
          caches.open(CACHE_PAGES).then((cache) => cache.put(event.request, clone));
          return response;
        })
        .catch(() => {
          // Offline: use cached version if available
          return caches.match(event.request);
        })
    );
    return;
  }

  // Everything else (icons, fonts, images): cache-first with network fallback
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) {
        // Update cache in background
        fetch(event.request).then((response) => {
          if (response.ok) {
            caches.open(CACHE_STATIC).then((cache) => cache.put(event.request, response));
          }
        }).catch(() => {});
        return cached;
      }
      return fetch(event.request).then((response) => {
        if (!response.ok) throw new Error('Network error');
        const clone = response.clone();
        caches.open(CACHE_STATIC).then((cache) => cache.put(event.request, clone));
        return response;
      }).catch(() => {
        if (event.request.mode === 'navigate') {
          return caches.match('/app');
        }
        return new Response('Offline', { status: 503 });
      });
    })
  );
});

// ── Message: allow clients to force-update the SW ───────────────────────
self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// ── Push notifications ────────────────────────────────────────────────────
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { /* μη-JSON payload */ }
  const title = data.title || 'RxTrail';
  const options = {
    body: data.body || 'Νέα ειδοποίηση',
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    tag: data.tag || 'default',
    data: { url: data.url || '/app' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/app';
  event.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((clients) => {
      for (const c of clients) {
        if (c.url.includes('/app')) {
          c.navigate(target);
          return c.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
