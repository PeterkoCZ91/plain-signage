// Regression: a local variable `t` shadows the translation function t() and the
// admin then fails with "t is not a function" / "Cannot access 't' before initialization"
// (the error does not show in the console, only the message "Server not responding" (cs: "Server neodpovídá")).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const TV = join(dirname(fileURLToPath(import.meta.url)), '..');

for (const file of ['admin.html', 'connect.html']) {
  test(`${file}: no local variable or parameter named t`, () => {
    const html = readFileSync(join(TV, file), 'utf8');
    const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
    const hits = [];
    scripts.split('\n').forEach((line, i) => {
      if (/\b(?:const|let|var)\s+t\b/.test(line)) hits.push(`${i + 1}: ${line.trim().slice(0, 90)}`);
      if (/(?:\(|,)\s*t\s*(?:,|\))\s*=>|\bt\s*=>/.test(line)) hits.push(`${i + 1}: ${line.trim().slice(0, 90)}`);
    });
    assert.deepEqual(hits, [], `shadowed t():\n${hits.join('\n')}`);
  });
}
