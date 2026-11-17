// Player logic tests: the inline script from player.html in node:vm, no browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadPlayer, flush, jsonResponse, dateStr } from './helpers.mjs';

const img = (name, extra = {}) => ({ type: 'image', url: `/media/${name}.jpg`, seconds: 5, ...extra });
const playlist = (items, extra = {}) => ({ version: 'v1', items, settings: { transition: 'cut' }, ...extra });

// Starts the player with the given playlist; fetch returns whatever is in ref.data at the moment.
async function start(data) {
  const ref = { data, fail: false };
  const p = loadPlayer({
    fetchImpl: async () => { if (ref.fail) throw new Error('network down'); return jsonResponse(ref.data); },
  });
  await flush();
  return { p, ref };
}

test('(a) the brand from the playlist fills the footer via textContent', async () => {
  const { p } = await start(playlist([img('a')], {
    settings: { brand: { email: 'a@example.com', phone: '+420 111', city: 'Brno' } } }));
  assert.equal(p.get('footmail').textContent, 'a@example.com · ');
  assert.equal(p.get('footrest').textContent, '+420 111 · Brno');
});

test('(a) a brand without a phone omits the empty parts; HTML is not interpreted', async () => {
  const { p } = await start(playlist([img('a')], {
    settings: { brand: { email: '<b>x</b>', city: 'Brno' } } }));
  assert.equal(p.get('footmail').textContent, '<b>x</b> · ');
  assert.equal(p.get('footrest').textContent, 'Brno');
});

test('(a) without a brand the default text from the HTML stays', async () => {
  const { p } = await start(playlist([img('a')]));
  assert.equal(p.get('footmail').textContent, '');   // the mock has no HTML => the script did not touch it
  assert.equal(p.get('footrest').textContent, '');
});

test('(b) after seconds elapse playback moves to the next item and loops', async () => {
  const { p } = await start(playlist([img('a', { seconds: 5 }), img('b', { seconds: 7 })]));
  assert.equal(p.current(), '/media/a.jpg');
  p.imageLoaded();
  await p.clock.advance(4999);
  assert.equal(p.current(), '/media/a.jpg');
  await p.clock.advance(1);
  assert.equal(p.current(), '/media/b.jpg');
  p.imageLoaded();
  await p.clock.advance(6999);
  assert.equal(p.current(), '/media/b.jpg');
  await p.clock.advance(1);
  assert.equal(p.current(), '/media/a.jpg');
});

test('(b) video: the item duration runs from the playing event, not from the start', async () => {
  const { p } = await start(playlist([
    { type: 'video', url: '/media/v.mp4', seconds: 20 }, img('b')]));
  const layer = p.stage.lastElementChild;
  await p.clock.advance(60000);                        // the download takes a while, still within LOAD_MS (120 s)
  assert.equal(p.stage.lastElementChild, layer);
  layer._video.emit('playing');
  await p.clock.advance(19999);
  assert.equal(p.stage.lastElementChild, layer);
  await p.clock.advance(1);
  assert.notEqual(p.stage.lastElementChild, layer);
});

test('(c) an item load error skips to the next one', async () => {
  const { p } = await start(playlist([img('a'), img('b')]));
  p.imageFailed();                                     // a.jpg is broken
  await p.clock.advance(1500);                         // RETRY_MS
  assert.equal(p.current(), '/media/b.jpg');
  p.imageLoaded();
  assert.equal(p.get('err').textContent, '');
});

test('(c) failure of all items neither jams nor floods the loop', async () => {
  const { p } = await start(playlist([img('a'), img('b')]));
  p.imageFailed();
  await p.clock.advance(1500);
  p.imageFailed();                                     // the whole round failed
  assert.equal(p.get('idle').style.display, 'flex');
  assert.match(p.get('err').textContent, /cannot play/);
  const count = p.images.length;
  await p.clock.advance(29999);                        // slow retry, not a tight loop
  assert.equal(p.images.length, count);
  await p.clock.advance(1);
  assert.equal(p.images.length, count + 1);            // after ROUND_MS it tries again
  p.imageLoaded();                                     // and recovers after a success
  assert.equal(p.get('idle').style.display, 'none');
  assert.match(p.get('err').textContent, /^(|cannot play.*)$/);   // the failure message lasts until a poll runs
});

test('(c) an item with no response counts as a failure after its time', async () => {
  const { p } = await start(playlist([img('a', { seconds: 5 }), img('b')]));
  await p.clock.advance(5000);                         // onload did not come
  assert.equal(p.get('err').textContent, '');          // 1 of 2 failures = not yet the whole round
  await p.clock.advance(1500);
  assert.equal(p.current(), '/media/b.jpg');           // it moves on
});

test('(d) an empty playlist shows the idle screen', async () => {
  const { p } = await start(playlist([]));
  assert.equal(p.get('idle').style.display, 'flex');
  assert.equal(p.images.length, 0);
});

test('(d) a playlist that becomes empty brings back idle and stops playback', async () => {
  const { p, ref } = await start(playlist([img('a')]));
  p.imageLoaded();
  assert.equal(p.get('idle').style.display, 'none');
  ref.data = playlist([], { version: 'v2' });
  await p.clock.advance(15000);                        // POLL_MS
  await flush();
  assert.equal(p.get('idle').style.display, 'flex');
  const count = p.images.length;
  await p.clock.advance(60000 - 15000);
  assert.equal(p.images.length, count);
});

test('(e) during a network outage the last known playlist keeps playing', async () => {
  const { p, ref } = await start(playlist([img('a', { seconds: 5 }), img('b', { seconds: 5 })]));
  p.imageLoaded();
  ref.fail = true;
  await p.clock.advance(5000);                         // the poll (15 s) has not run yet meanwhile
  assert.equal(p.current(), '/media/b.jpg');
  p.imageLoaded();
  await p.clock.advance(10000);                        // the poll fails
  assert.match(p.get('err').textContent, /^offline – playing the stored copy \(network down\)$/);
  assert.equal(p.get('idle').style.display, 'none');
  await p.clock.advance(5000);
  assert.ok(['/media/a.jpg', '/media/b.jpg'].includes(p.current()));
});

test('(e) an HTTP server error behaves like an outage', async () => {
  const p = loadPlayer({ fetchImpl: async () => jsonResponse({}, 503) });
  await flush();
  assert.match(p.get('err').textContent, /offline – playing the stored copy \(HTTP 503\)/);
});

test('(e) after the network returns the error message disappears', async () => {
  const { p, ref } = await start(playlist([img('a', { seconds: 100000 })]));
  p.imageLoaded();
  ref.fail = true;
  await p.clock.advance(15000);
  assert.match(p.get('err').textContent, /^offline/);
  ref.fail = false;
  await p.clock.advance(15000);
  assert.equal(p.get('err').textContent, '');
});

test('(f) items outside the From-Until validity and disabled ones are skipped', async () => {
  const { p } = await start(playlist([
    img('future', { from: dateStr(1) }),
    img('expired', { until: dateStr(-1) }),
    img('disabled', { enabled: false }),
    img('valid', { from: dateStr(-1), until: dateStr(1), seconds: 5 }),
    img('today', { from: dateStr(0), until: dateStr(0), seconds: 5 }),
  ]));
  assert.equal(p.current(), '/media/valid.jpg');
  p.imageLoaded();
  await p.clock.advance(5000);
  assert.equal(p.current(), '/media/today.jpg');
  p.imageLoaded();
  await p.clock.advance(5000);
  assert.equal(p.current(), '/media/valid.jpg');
});

test('(f) when nothing is valid, idle is shown', async () => {
  const { p } = await start(playlist([img('a', { until: dateStr(-1) })]));
  assert.equal(p.get('idle').style.display, 'flex');
});

test('the heartbeat carries the device from the URL, the state and the canvas size', async () => {
  const p = loadPlayer({ pathname: '/tv/bar', fetchImpl: async () => jsonResponse(playlist([img('a', { seconds: 100000 })])) });
  await flush();
  assert.match(p.fetches[0], /^\/api\/tv\/bar\/playlist\?heartbeat=1&ok=1&err=0&w=1920&h=1080&h264=1/);
  p.imageLoaded();
  await p.clock.advance(15000);
  assert.match(p.fetches[1], /ok=1&err=0/);
});

test('the heartbeat reports the failure count and the reason', async () => {
  const { p } = await start(playlist([img('a', { seconds: 100000 }), img('b', { seconds: 100000 })]));
  p.imageFailed();
  await p.clock.advance(15000);
  const last = p.fetches[p.fetches.length - 1];
  assert.match(last, /ok=0&err=1/);
  assert.match(last, /why=image/);
});

test('a new server version (higher build) schedules a reload after the item transition', async () => {
  const { p, ref } = await start(playlist([img('a', { seconds: 20 })], { build: '1' }));
  p.imageLoaded();
  ref.data = playlist([img('a', { seconds: 20 })], { build: '2' });
  await p.clock.advance(15000);
  // note: the message 'new version – reloading' is overwritten at once by err('') further down in poll() (see the report)
  assert.equal(p.sandbox.__reloaded, undefined);
  await p.clock.advance(5000);                         // item transition: reload at once
  assert.equal(p.sandbox.__reloaded, 1);
});

test('the new-version message stays visible until the page reloads', async () => {
  const { p, ref } = await start(playlist([img('a', { seconds: 20 })], { build: '1' }));
  p.imageLoaded();
  ref.data = playlist([img('a', { seconds: 20 })], { build: '2' });
  await p.clock.advance(15000);                        // the poll sees the higher build
  assert.equal(p.get('err').textContent, 'new version – reloading');
  assert.equal(p.sandbox.__reloaded, undefined);
  await p.clock.advance(15000);                        // the next poll must not overwrite it
  assert.equal(p.get('err').textContent, 'new version – reloading');
  assert.equal(p.sandbox.__reloaded, 1);               // the reload happened, the message held until then
});
