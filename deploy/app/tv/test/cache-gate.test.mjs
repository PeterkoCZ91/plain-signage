// A new item joins the rotation only after it is stored in Cache Storage (under a service worker).
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadPlayer, flush, jsonResponse } from './helpers.mjs';

const ORIGIN = 'https://tv.test';
const img = (name, extra = {}) => ({ type: 'image', url: `/media/${name}.jpg`, seconds: 5, ...extra });

// Player with an "active" service worker and a Cache Storage that the test fills.
async function start(items, { cached = [], sw = true, version = 'v1' } = {}) {
  const ref = { data: { version, items, settings: { transition: 'cut' } } };
  const cachedUrls = new Set(cached.map(n => `${ORIGIN}/media/${n}.jpg`));
  class FakeURL extends URL {
    static createObjectURL(b) { return 'blob:' + b.u; }
    static revokeObjectURL() {}
  }
  const p = loadPlayer({
    fetchImpl: async () => jsonResponse(ref.data),
    globals: clock => ({
      URL: FakeURL,
      Date: class extends Date { static now() { return 1e12 + clock.now; } },
      navigator: sw ? { serviceWorker: { controller: { postMessage() {} },
        register: () => Promise.resolve({}), addEventListener() {},
        ready: new Promise(() => {}) } } : {},
      caches: { open: async () => ({
        match: async u => (cachedUrls.has(u) ? { blob: async () => ({ size: 10, u }) } : undefined),
      }) },
    }),
  });
  await flush();
  return { p, ref, cachedUrls, store: n => cachedUrls.add(`${ORIGIN}/media/${n}.jpg`) };
}
const shown = p => p.current();
const blob = n => `blob:${ORIGIN}/media/${n}.jpg`;

test('an item that is not downloaded does not play, a downloaded one does', async () => {
  const { p } = await start([img('a'), img('b')], { cached: ['a'] });
  p.imageLoaded();
  assert.equal(shown(p), blob('a'));
  await p.clock.advance(5000); p.imageLoaded();
  assert.equal(shown(p), blob('a'), 'b is not in memory -> only a plays');
});

test('nothing in memory -> brand screen, the item appears once downloaded', async () => {
  const { p, store } = await start([img('a')]);
  assert.equal(p.get('idle').style.display, 'flex');
  assert.equal(shown(p), null);
  store('a');
  await p.clock.advance(15000); p.imageLoaded();
  assert.equal(p.get('idle').style.display, 'none');
  assert.equal(shown(p), blob('a'));
});

test('finishing an item download does not interrupt the item that is playing', async () => {
  const { p, store } = await start([img('a', { seconds: 30 }), img('b')], { cached: ['a'] });
  p.imageLoaded();
  store('b');
  await p.clock.advance(15000);        // poll: b is ready
  assert.equal(shown(p), blob('a'), 'a keeps playing until the end of its 30 s');
  await p.clock.advance(15000); p.imageLoaded();
  assert.equal(shown(p), blob('b'));
});

test('when it cannot be stored, it plays from the network after 3 minutes', async () => {
  const { p } = await start([img('a')]);
  await p.clock.advance(170000);
  assert.equal(shown(p), null, 'nothing before the limit');
  await p.clock.advance(15000); p.imageLoaded();
  assert.equal(shown(p), '/media/a.jpg');
});

test('without a service worker it behaves as before (plays from the network at once)', async () => {
  const { p } = await start([img('a')], { sw: false });
  p.imageLoaded();
  assert.equal(shown(p), '/media/a.jpg');
});
