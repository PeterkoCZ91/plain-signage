// The admin and player use their own SVG sprite icons, never emoji / pictographs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const RE = /[\u{1F000}-\u{1FFFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{23E9}-\u{23FF}\u{FE0F}]/u;
for (const f of ['admin.html', 'player.html', 'connect.html', 'sw.js']) {
  test(`${f} contains no emoji`, () => {
    const src = readFileSync(new URL('../' + f, import.meta.url), 'utf8');
    const bad = src.split('\n').map((l, i) => [i + 1, l]).filter(([, l]) => RE.test(l));
    assert.deepEqual(bad.map(([n]) => n), []);
  });
}
