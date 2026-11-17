// Bilingual UI tests (admin.html, connect.html): dictionaries, data-i18n, language choice.
// Without a browser: the inline script with the dictionary runs in node:vm.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const TV = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const FILES = { admin: path.join(TV, 'admin.html'), connect: path.join(TV, 'connect.html') };
const html = f => fs.readFileSync(f, 'utf8');
const blocks = src => [...src.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const i18nBlock = src => {
  const b = blocks(src).find(x => x.includes('const I18N'));
  assert.ok(b, 'script with the dictionary not found');
  return b;
};

// Loads the i18n script into a sandbox with controllable location/localStorage/navigator.
function load(file, { search = '', stored, nav = 'cs-CZ', storageThrows = false } = {}) {
  const sb = {
    location: { search }, navigator: { language: nav }, URLSearchParams,
    localStorage: {
      data: stored === undefined ? {} : { signageLang: stored },
      getItem(k) { if (storageThrows) throw new Error('blocked'); return k in this.data ? this.data[k] : null; },
      setItem(k, v) { if (storageThrows) throw new Error('blocked'); this.data[k] = String(v); },
    },
    document: { documentElement: {}, querySelectorAll: () => [] },
  };
  vm.createContext(sb);
  vm.runInContext(i18nBlock(html(file)), sb);
  return { sb, run: code => vm.runInContext(code, sb) };
}

for (const [name, file] of Object.entries(FILES)) {
  test(`${name}: the cs and en dictionaries have the same keys, nothing empty, no HTML in en`, () => {
    const { run } = load(file);
    const cs = run('I18N.cs'), en = run('I18N.en');
    const kc = Object.keys(cs).sort(), ke = Object.keys(en).sort();
    assert.deepEqual(kc.filter(k => !(k in en)), [], 'keys only in cs');
    assert.deepEqual(ke.filter(k => !(k in cs)), [], 'keys only in en');
    assert.ok(kc.length > 5);
    for (const [k, v] of [...Object.entries(cs), ...Object.entries(en)]) {
      assert.equal(typeof v, 'string', k);
      assert.ok(v.trim().length > 0, `empty translation: ${k}`);
    }
    for (const [k, v] of Object.entries(en)) assert.ok(!/<[a-z\/]/i.test(v), `HTML in the en translation: ${k}`);
    // the same {parameters} in both languages
    const ph = s => (s.match(/\{\w+\}/g) || []).sort().join();
    for (const k of kc) assert.equal(ph(en[k]), ph(cs[k]), `different parameters: ${k}`);
  });

  test(`${name}: every data-i18n / data-i18n-attr key exists in the dictionary`, () => {
    const src = html(file);
    const { run } = load(file);
    const cs = run('I18N.cs');
    const keys = [...src.matchAll(/data-i18n="([^"]+)"/g)].map(m => m[1]);
    for (const m of src.matchAll(/data-i18n-attr="([^"]+)"/g))
      for (const p of m[1].split(';')) keys.push(p.slice(p.indexOf(':') + 1).trim());
    assert.ok(keys.length > 3);
    for (const k of keys) assert.ok(k in cs, `missing key ${k}`);
  });

  test(`${name}: literal t('key') calls in the script exist in the dictionary`, () => {
    const src = html(file);
    const { run } = load(file);
    const cs = run('I18N.cs');
    const mainJs = blocks(src).filter(b => !b.includes('const I18N')).join('\n');
    for (const m of mainJs.matchAll(/\bt\(\s*['"]([a-z]+\.[\w.]+)['"]/g))
      if (!m[1].endsWith('.')) assert.ok(m[1] in cs, `missing key ${m[1]}`); // 'x.' = dynamic prefix
  });

  test(`${name}: the Czech static version in the HTML matches the cs dictionary`, () => {
    const src = html(file);
    const { run } = load(file);
    const cs = run('I18N.cs');
    let n = 0;
    for (const m of src.matchAll(/data-i18n="([^"]+)"[^>]*>([^<]*)</g)) {
      assert.equal(m[2].trim(), cs[m[1]], `HTML != cs dictionary: ${m[1]}`);
      n++;
    }
    assert.ok(n > 3);
    assert.match(src, /<html lang="cs">/);
  });

  test(`${name}: t() fallback en -> cs -> key, and parameters`, () => {
    const { run } = load(file, { search: '?lang=en' });
    assert.equal(run('LANG'), 'en');
    const key = Object.keys(run('I18N.cs')).find(k => !/\{/.test(run('I18N.cs')[k]));
    assert.equal(run(`t(${JSON.stringify(key)})`), run('I18N.en')[key]);
    run(`delete I18N.en[${JSON.stringify(key)}]`);
    assert.equal(run(`t(${JSON.stringify(key)})`), run('I18N.cs')[key], 'fallback to cs');
    assert.equal(run(`t('neexistuje.klic')`), 'neexistuje.klic', 'fallback to the key');
    run("I18N.cs['x.p'] = 'A {n} B {m}'; I18N.en['x.p'] = 'C {n}'");
    assert.equal(run("t('x.p', {n: 3, m: 4})"), 'C 3');
    run("LANG = 'cs'");
    assert.equal(run("t('x.p', {n: 3, m: 4})"), 'A 3 B 4');
    assert.equal(run("t('x.p', {n: 3})"), 'A 3 B {m}');
  });

  test(`${name}: language choice query > storage > navigator`, () => {
    const lang = o => load(file, o).run('LANG');
    assert.equal(lang({}), 'cs');
    assert.equal(lang({ nav: 'en-US' }), 'en');
    assert.equal(lang({ nav: 'de-DE' }), 'cs');
    assert.equal(lang({ stored: 'en', nav: 'cs-CZ' }), 'en');
    assert.equal(lang({ stored: 'cs', nav: 'en-US' }), 'cs');
    assert.equal(lang({ search: '?lang=en', stored: 'cs', nav: 'cs' }), 'en');
    assert.equal(lang({ search: '?lang=cs', stored: 'en', nav: 'en-US' }), 'cs');
    assert.equal(lang({ search: '?lang=xx', stored: 'en', nav: 'cs' }), 'en', 'an invalid query is ignored');
    assert.equal(lang({ search: '?x=1&lang=en-GB' }), 'en');
    assert.equal(lang({ storageThrows: true, nav: 'en' }), 'en', 'storage throws an exception');
    assert.equal(lang({ storageThrows: true, nav: 'cs' }), 'cs');
    assert.equal(lang({ stored: 'klingon', nav: 'en' }), 'en');
  });

  test(`${name}: setLang stores the choice, ignores an invalid one; dateLocale`, () => {
    const { run, sb } = load(file);
    run("setLang('en')");
    assert.equal(run('LANG'), 'en');
    assert.equal(sb.localStorage.data.signageLang, 'en');
    assert.equal(run('dateLocale()'), 'en-GB');
    run("setLang('zz')");
    assert.equal(run('LANG'), 'en');
    run("setLang('cs')");
    assert.equal(run('dateLocale()'), 'cs-CZ');
    const t2 = load(file, { storageThrows: true });
    t2.run("setLang('en')");
    assert.equal(t2.run('LANG'), 'en', 'the choice holds even without storage');
  });
}

test('admin: API calls send Accept-Language and have no hard-coded toLocaleString(cs-CZ)', () => {
  const src = html(FILES.admin);
  assert.match(src, /'Accept-Language': LANG/);
  assert.match(src, /xhr\.setRequestHeader\('Accept-Language', LANG\)/);
  assert.ok(!/toLocaleString\('cs-CZ'\)/.test(src));
});

test('admin: dynamically composed keys have all variants', () => {
  const { run } = load(FILES.admin);
  const cs = run('I18N.cs');
  const need = [];
  for (const z of ['landscape', 'portrait', 'portrait270', 'fill']) need.push('admin.fit.desc.' + z);
  for (const z of ['seen', 'gap', 'unknown']) need.push('admin.avail.' + z);
  for (const b of ['admin.up.added', 'admin.up.notAdded']) for (const q of ['one', 'few', 'many']) need.push(`${b}.${q}`);
  for (const k of need) assert.ok(k in cs, k);
});
