// Item schedule in the player: the pure function scheduleActive with a fixed time.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const TV = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const src = fs.readFileSync(path.join(TV, 'player.html'), 'utf8');
const m = src.match(/\/\/ schedule:begin([\s\S]*?)\/\/ schedule:end/);
assert.ok(m, 'block schedule:begin/end not found');
const ctx = vm.createContext({});
vm.runInContext(m[1] + '\nthis.scheduleActive = scheduleActive;', ctx);
const active = (sch, iso) => ctx.scheduleActive(sch, new Date(iso));
// 2026-09-28 is a Monday (local time)
const MON = '2026-09-28', TUE = '2026-09-29', SUN = '2026-10-04';
const at = (d, hm) => `${d}T${hm}:00`;

test('without a schedule or with an empty schedule it always plays', () => {
  for (const s of [undefined, null, {}, { days: [] }])
    assert.equal(active(s, at(MON, '12:00')), true);
});

test('days: Monday=0, empty/missing = every day', () => {
  assert.equal(active({ days: [0] }, at(MON, '03:00')), true);
  assert.equal(active({ days: [0] }, at(TUE, '03:00')), false);
  assert.equal(active({ days: [6] }, at(SUN, '10:00')), true);
  assert.equal(active({ days: [6] }, at(MON, '10:00')), false);
  assert.equal(active({ days: [] }, at(SUN, '10:00')), true);
});

test('a window within one day and minute boundaries (the end is exclusive)', () => {
  const s = { from: '08:00', to: '17:30' };
  assert.equal(active(s, at(MON, '07:59')), false);
  assert.equal(active(s, at(MON, '08:00')), true);
  assert.equal(active(s, at(MON, '17:29')), true);
  assert.equal(active(s, at(MON, '17:30')), false);
});

test('across midnight: Monday 22:00-02:00 belongs to Monday, also on Tuesday at 01:00', () => {
  const s = { days: [0], from: '22:00', to: '02:00' };
  assert.equal(active(s, at(MON, '21:59')), false);
  assert.equal(active(s, at(MON, '22:00')), true);
  assert.equal(active(s, at(MON, '23:59')), true);
  assert.equal(active(s, at(TUE, '00:00')), true);
  assert.equal(active(s, at(TUE, '01:59')), true);
  assert.equal(active(s, at(TUE, '02:00')), false);
  assert.equal(active(s, at(TUE, '22:30')), false);   // Tuesday evening not anymore
  assert.equal(active(s, at(MON, '01:00')), false);   // Monday morning belongs to Sunday
});

test('across midnight from Sunday to Monday (week wrap)', () => {
  const s = { days: [6], from: '23:00', to: '01:00' };
  assert.equal(active(s, at(SUN, '23:30')), true);
  assert.equal(active(s, at('2026-10-05', '00:30')), true);   // Monday
  assert.equal(active(s, at('2026-10-05', '01:00')), false);
});

test('across midnight without days = every day', () => {
  const s = { from: '22:00', to: '02:00' };
  assert.equal(active(s, at(TUE, '01:00')), true);
  assert.equal(active(s, at(TUE, '12:00')), false);
});

test('from == to is invalid and the schedule is ignored (it plays)', () => {
  assert.equal(active({ from: '10:00', to: '10:00' }, at(MON, '03:00')), true);
});

test('a malformed or incomplete time is ignored, the days apply', () => {
  assert.equal(active({ days: [0], from: '08:00' }, at(MON, '03:00')), true);
  assert.equal(active({ days: [0], from: '8:00', to: '9:00' }, at(TUE, '08:30')), false);
});

test('player.html: no local variable or parameter t', () => {
  const scripts = [...src.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(x => x[1]).join('\n');
  const hits = scripts.split('\n').filter(l =>
    /\b(?:const|let|var)\s+t\b/.test(l) || /(?:\(|,)\s*t\s*(?:,|\))\s*=>|\bt\s*=>/.test(l));
  assert.deepEqual(hits, []);
});
