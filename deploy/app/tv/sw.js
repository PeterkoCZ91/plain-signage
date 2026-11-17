/* Offline media cache for the modern TV player. Workbox is vendored locally. */
importScripts('/assets/workbox-core.prod.js', '/assets/workbox-strategies.prod.js',
              '/assets/workbox-range-requests.prod.js');

const MEDIA_CACHE = 'signage-media-v1';
const SHELL_CACHE = 'signage-shell-v2';   // v2: also contains the last QR (/qr.png)
const PLAYLIST_CACHE = 'signage-playlists-v1';

self.addEventListener('install', event => {
  event.waitUntil(caches.open(SHELL_CACHE).then(cache => cache.addAll(['/tv/', '/assets/default-qr.png'])));
  self.skipWaiting();
});
/* Delete old / unmigrated caches. Media and playlists stay at v1 on purpose
   (the format did not change and deleting would force every TV to download the
   videos again); anything else with the signage- prefix that is not on the
   list of current caches gets deleted. */
const CURRENT_CACHES = [MEDIA_CACHE, SHELL_CACHE, PLAYLIST_CACHE];
self.addEventListener('activate', event => event.waitUntil((async () => {
  try {
    for (const name of await caches.keys()) {
      if (name.startsWith('signage-') && !CURRENT_CACHES.includes(name)) await caches.delete(name);
    }
  } catch (_) { /* cache cleanup must not block activation */ }
  await self.clients.claim();
})()));

const media = new workbox.strategies.CacheFirst({
  cacheName: MEDIA_CACHE,
  plugins: [new workbox.rangeRequests.RangeRequestsPlugin()],
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) return;
  /* Player page: network first, shell cache as the fallback. Without this the
     shell cache was filled on install but never read, so reloading the page
     during an outage left the screen on a browser error page with no way back. */
  if (event.request.mode === 'navigate' && url.pathname.startsWith('/tv')) {
    return event.respondWith((async () => {
      try {
        const fresh = await fetch(event.request);
        if (fresh && fresh.ok) {
          const cache = await caches.open(SHELL_CACHE);
          await cache.put('/tv/', fresh.clone());
        }
        return fresh;
      } catch (_) {
        const cache = await caches.open(SHELL_CACHE);
        const cached = await cache.match('/tv/');
        if (cached) return cached;
        throw _;
      }
    })());
  }
  /* Media: cache first, but playback must NEVER depend on the cache write
     succeeding. When the origin is out of quota (a nearly full disk shrinks it
     to almost nothing), cache.put() rejects, the strategy rejects with it, and
     respondWith() turns that into a network error -- the video element then
     fails every single file with NotSupportedError even though the server and
     the file are fine. Falling back to a plain fetch keeps the screen playing;
     it just plays online-only until there is room again. */
  if (url.pathname.startsWith('/media/')) {
    return event.respondWith((async () => {
      try {
        const r = await media.handle({event, request: event.request});
        if (r) return r;
      } catch (_) { /* cache unavailable - that must not break playback */ }
      return fetch(event.request);
    })());
  }
  /* QR: network first, the last successful image is stored under the
     device+slot key (without the cache-busting parameter, so the cache does not
     grow) and the offline idle screen shows the last QR; with no cache at all
     the built-in /assets/default-qr.png is returned. */
  if (url.pathname === '/qr.png') {
    const dev = url.searchParams.get('device') || 'default';
    const slot = url.searchParams.get('slot') || '1';
    const qrKey = new Request(new URL(`/__signage-qr/${encodeURIComponent(dev)}/${encodeURIComponent(slot)}`, location.origin));
    return event.respondWith((async () => {
      try {
        const fresh = await fetch(event.request);
        if (fresh && fresh.ok) {
          try { await (await caches.open(SHELL_CACHE)).put(qrKey, fresh.clone()); } catch (_) { /* quota */ }
        }
        return fresh;
      } catch (err) {
        const cache = await caches.open(SHELL_CACHE);
        return await cache.match(qrKey) || await cache.match('/assets/default-qr.png') || Response.error();
      }
    })());
  }
  const match = url.pathname.match(/^\/api\/tv\/([A-Za-z0-9_-]+)\/playlist$/);
  if (match) event.respondWith((async () => {
    try { return await fetch(event.request); }
    catch (_) {
      const cache = await caches.open(PLAYLIST_CACHE);
      return await cache.match(playlistKey(match[1], 'current')) ||
             await cache.match(playlistKey(match[1], 'last')) ||
             new Response(JSON.stringify({items: []}), {status: 503,
               headers: {'Content-Type': 'application/json'}});
    }
  })());
});

function playlistKey(device, slot) {
  return new Request(new URL(`/__signage-playlist/${encodeURIComponent(device)}/${slot}`, location.origin));
}

function mediaUrl(rawUrl) {
  const url = new URL(rawUrl, location.origin);
  if (url.origin !== location.origin || !url.pathname.startsWith('/media/')) throw new Error('invalid media URL');
  return url.href;
}

async function removeUnusedMedia() {
  const playlists = await caches.open(PLAYLIST_CACHE);
  const keep = new Set();
  let packageCount = 0;
  for (const key of await playlists.keys()) {
    try {
      const saved = await (await playlists.match(key)).json();
      packageCount += 1;
      for (const item of saved.items || []) keep.add(mediaUrl(item.url));
    } catch (_) { /* Broken metadata is not a reason to erase a playable cache. */ }
  }
  if (!packageCount) return; // Migration from the older media-only cache: preserve it.
  const mediaCache = await caches.open(MEDIA_CACHE);
  for (const key of await mediaCache.keys()) {
    if (key.url.startsWith(location.origin + '/media/') && !keep.has(key.url)) await mediaCache.delete(key);
  }
}

/* Fetch whole files before they are needed: browsers otherwise cache only a video range.
   Playlist becomes current once every file was fetched or reported unavailable
   by the server (404 etc.); the number of missing files is reported to clients. */
/* Only one warm cycle at a time. The player polls every 15 s; a file that takes
   longer than that to download used to get a fresh fetch on every poll, and the
   stacked downloads starved each other of bandwidth so none ever finished. */
let warming = false;
/* When a cache write fails there is no point re-downloading the same file on
   every 15 s poll -- the server log showed the same 22 MB fetched nine times
   in a row because the put never stuck. Back off and let playback run online. */
let warmBlockedUntil = 0;
const WARM_BACKOFF_MS = 10 * 60 * 1000;
/* A damaged cached copy makes playback fail forever without ever touching the
   network, so the screen is bricked and the server log shows nothing. The
   player asks for the copy to be dropped after an item fails twice. */
self.addEventListener('message', event => {
  const drop = event.data || {};
  if (drop.type === 'DROP_MEDIA' && typeof drop.url === 'string') {
    event.waitUntil((async () => {
      try {
        const cache = await caches.open(MEDIA_CACHE);
        await cache.delete(mediaUrl(drop.url));
      } catch (_) { /* Nothing to drop, or a URL outside /media/. */ }
    })());
    return;
  }
});
self.addEventListener('message', event => {
  const data = event.data || {};
  if (data.type !== 'WARM_PLAYLIST' || !Array.isArray(data.urls) || !data.playlist || !data.device) return;
  if (warming || Date.now() < warmBlockedUntil) return;
  warming = true;
  event.waitUntil((async () => {
   try {
    const cache = await caches.open(MEDIA_CACHE);
    let missing = 0;   // files the server returned as not downloadable (404 etc.)
    for (const rawUrl of [...new Set(data.urls)]) {
      let url;
      try { url = mediaUrl(rawUrl); }
      catch (_) { missing += 1; continue; }   // invalid URL: skip it, do not block the playlist
      if (await cache.match(url)) continue;
      try {
        const response = await fetch(url, {cache: 'no-store'});
        if (!response.ok) { missing += 1; continue; }   // a single 404 does not block the whole playlist
        await cache.put(url, response);
      } catch (_) {
        // Out of quota or offline: stop hammering the same file every poll.
        warmBlockedUntil = Date.now() + WARM_BACKOFF_MS;
        await removeUnusedMedia();
        return;                              // Keep current + last-good playlist untouched.
      }
    }
    try {
      const playlists = await caches.open(PLAYLIST_CACHE);
      const current = await playlists.match(playlistKey(data.device, 'current'));
      if (current) await playlists.put(playlistKey(data.device, 'last'), current.clone());
      await playlists.put(playlistKey(data.device, 'current'), new Response(JSON.stringify(data.playlist),
        {headers: {'Content-Type': 'application/json; charset=utf-8'}}));
      await removeUnusedMedia(); // Across all devices keep only current + last-good packages.
      if (missing) {
        const clients = await self.clients.matchAll();
        for (const c of clients) c.postMessage({type: 'WARM_DONE', device: data.device, missing});
      }
    } catch (_) { /* Quota failure: existing offline playlist remains available. */ }
   } finally { warming = false; }
  })());
});
