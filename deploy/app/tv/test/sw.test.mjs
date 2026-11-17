// Service worker tests without a browser: sw.js runs in a node:vm sandbox with mocks.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SW_SOURCE = fs.readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'sw.js'), 'utf8');
const ORIGIN = 'https://tv.test';
const abs = k => new URL(typeof k === 'string' ? k : k.url, ORIGIN).href;

class MockCache {
  constructor() { this.map = new Map(); }
  async match(k) { const r = this.map.get(abs(k)); return r ? r.clone() : undefined; }
  async put(k, r) { this.map.set(abs(k), r); }
  async delete(k) { return this.map.delete(abs(k)); }
  async keys() { return [...this.map.keys()].map(u => new Request(u)); }
  async addAll() {}
}

function makeCaches() {
  const store = new Map();
  return {
    store,
    async open(name) { if (!store.has(name)) store.set(name, new MockCache()); return store.get(name); },
    async keys() { return [...store.keys()]; },
    async delete(name) { return store.delete(name); },
    async has(name) { return store.has(name); },
  };
}

// Loads sw.js into a sandbox; returns the handlers and mocks.
function loadSw({ fetchImpl = async () => { throw new Error('offline'); } } = {}) {
  const listeners = {};
  const posted = [];
  const caches = makeCaches();
  const client = { postMessage: m => posted.push(m) };
  const self = {
    addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    skipWaiting: () => {},
    clients: { claim: async () => {}, matchAll: async () => [client] },
  };
  const state = { fetchImpl };
  class CacheFirst {
    constructor(o) { this.cacheName = o.cacheName; }
    async handle() { return undefined; }
  }
  const sandbox = {
    self, caches, Request, Response, URL, Promise, JSON, Set, Array, Date, Error,
    encodeURIComponent, console,
    location: new URL(ORIGIN + '/sw.js'),
    fetch: (...a) => state.fetchImpl(...a),
    importScripts: () => {},
    workbox: {
      strategies: { CacheFirst },
      rangeRequests: { RangeRequestsPlugin: class {} },
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(SW_SOURCE, sandbox, { filename: 'sw.js' });

  const waits = [];
  const dispatch = (type, extra = {}) => {
    const event = { waitUntil: p => waits.push(p), ...extra };
    for (const fn of listeners[type] || []) fn(event);
    return event;
  };
  const doFetch = async url => {
    let responded;
    const event = dispatch('fetch', {
      request: new Request(url),
      respondWith: p => { responded = Promise.resolve(p); },
    });
    assert.ok(responded, 'the fetch handler did not call respondWith');
    void event;
    return responded;
  };
  const settle = () => Promise.all(waits.splice(0));
  return { caches, posted, state, dispatch, doFetch, settle };
}

test('activate deletes old signage-* caches except the current ones and leaves foreign ones', async () => {
  const sw = loadSw();
  for (const n of ['signage-media-v1', 'signage-shell-v2', 'signage-playlists-v1',
                   'signage-shell-v1', 'signage-old', 'jina-cache']) {
    await sw.caches.open(n);
  }
  sw.dispatch('activate');
  await sw.settle();
  assert.deepEqual([...sw.caches.store.keys()].sort(),
    ['jina-cache', 'signage-media-v1', 'signage-playlists-v1', 'signage-shell-v2']);
});

test('fetch /qr.png stores a successful response and returns the stored one offline', async () => {
  const sw = loadSw({ fetchImpl: async () => new Response('QR-DATA', { status: 200 }) });
  const online = await sw.doFetch(`${ORIGIN}/qr.png?device=lobby&slot=2&t=111`);
  assert.equal(await online.text(), 'QR-DATA');

  const shell = sw.caches.store.get('signage-shell-v2');
  assert.ok(shell.map.has(`${ORIGIN}/__signage-qr/lobby/2`), 'key without the cache-busting parameter');

  sw.state.fetchImpl = async () => { throw new Error('offline'); };
  const offline = await sw.doFetch(`${ORIGIN}/qr.png?device=lobby&slot=2&t=222`);
  assert.equal(await offline.text(), 'QR-DATA');
  assert.equal(shell.map.size, 1, 'the cache must not grow with every t=');
});

test('fetch /qr.png offline without a stored QR returns /assets/default-qr.png', async () => {
  const sw = loadSw();
  const shell = await sw.caches.open('signage-shell-v2');
  await shell.put('/assets/default-qr.png', new Response('FALLBACK'));
  const res = await sw.doFetch(`${ORIGIN}/qr.png?device=lobby&slot=1`);
  assert.equal(await res.text(), 'FALLBACK');
});

test('WARM_PLAYLIST with one 404 file accepts the playlist and sends WARM_DONE missing=1', async () => {
  const sw = loadSw({
    fetchImpl: async url => String(url).endsWith('/media/missing.mp4')
      ? new Response('nope', { status: 404 })
      : new Response('video', { status: 200 }),
  });
  const playlist = { items: [{ url: '/media/ok.mp4' }, { url: '/media/missing.mp4' }] };
  sw.dispatch('message', { data: {
    type: 'WARM_PLAYLIST', device: 'lobby', playlist,
    urls: ['/media/ok.mp4', '/media/missing.mp4'],
  } });
  await sw.settle();

  const media = sw.caches.store.get('signage-media-v1');
  assert.ok(media.map.has(`${ORIGIN}/media/ok.mp4`));
  assert.ok(!media.map.has(`${ORIGIN}/media/missing.mp4`));

  const pl = sw.caches.store.get('signage-playlists-v1');
  const saved = await (await pl.match('/__signage-playlist/lobby/current')).json();
  assert.deepEqual(JSON.parse(JSON.stringify(saved)), playlist);

  // JSON because of objects from another realm (vm sandbox)
  assert.deepEqual(JSON.parse(JSON.stringify(sw.posted)),
    [{ type: 'WARM_DONE', device: 'lobby', missing: 1 }]);
});
