// Idempotency-Key for uploads in the admin: format, uniqueness and key reuse on retry.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const TV = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const src = fs.readFileSync(path.join(TV, 'admin.html'), 'utf8');
const m = src.match(/\/\/ idem:begin([\s\S]*?)\/\/ idem:end/);
assert.ok(m, 'block idem:begin/end not found');
const RE = /^[A-Za-z0-9_.:-]{1,128}$/;

function load(sb) {
  const ctx = vm.createContext({ Date, Math, ...sb });
  vm.runInContext(m[1] + '\nthis.makeUploadKey = makeUploadKey; this.uploadKeyFor = uploadKeyFor;', ctx);
  return ctx;
}

test('a key built with crypto.randomUUID matches the regex and is unique', () => {
  let n = 0;
  const c = load({ crypto: { randomUUID: () => `00000000-0000-4000-8000-${String(n++).padStart(12, '0')}` } });
  const keys = new Set(Array.from({ length: 50 }, () => c.makeUploadKey()));
  assert.equal(keys.size, 50);
  for (const k of keys) assert.match(k, RE);
});

test('fallback random string without crypto: format and uniqueness', () => {
  const c = load({});
  const keys = new Set(Array.from({ length: 200 }, () => c.makeUploadKey()));
  assert.equal(keys.size, 200);
  for (const k of keys) assert.match(k, RE);
});

test('crypto.randomUUID throws -> fallback', () => {
  const c = load({ crypto: { randomUUID() { throw new Error('insecure context'); } } });
  assert.match(c.makeUploadKey(), RE);
});

test('an overlong UUID is truncated to 128 characters', () => {
  const c = load({ crypto: { randomUUID: () => 'a'.repeat(300) } });
  const k = c.makeUploadKey();
  assert.equal(k.length, 128);
  assert.match(k, RE);
});

test('uploadKeyFor: same job = same key (retry), another job = another key', () => {
  const c = load({});
  const a = {}, b = {};
  const k1 = c.uploadKeyFor(a);
  assert.equal(c.uploadKeyFor(a), k1);
  assert.equal(a.idemKey, k1);
  assert.notEqual(c.uploadKeyFor(b), k1);
});
