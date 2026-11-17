// Player bilingual tests (en default + cs): dictionaries, pickLang, QR labels.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadPlayer, flush, jsonResponse } from './helpers.mjs';

const TV = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const html = fs.readFileSync(path.join(TV, 'player.html'), 'utf8');
const m = /\/\/ i18n:begin\n([\s\S]*?)\/\/ i18n:end/.exec(html);
assert.ok(m, 'block i18n:begin/end not found');

// We run the block on its own; pickLang/tp are pure (tp depends only on LANG).
function load(search = '', nav = '') {
  const sb = { location: { search }, navigator: { language: nav }, document: { documentElement: {} } };
  vm.createContext(sb);
  vm.runInContext(m[1] + '\n;this.out = { I18N, pickLang, tp, LANG, qrLabelText };', sb);
  return sb.out;
}

test('the cs and en dictionaries have the same keys and no empty translation', () => {
  const { I18N } = load();
  assert.deepEqual(Object.keys(I18N.en).sort(), Object.keys(I18N.cs).sort());
  for (const lang of ['cs', 'en']) {
    for (const [k, v] of Object.entries(I18N[lang])) {
      assert.ok(typeof v === 'string' && v.trim() !== '', `${lang}.${k} is empty`);
    }
  }
  for (const k of Object.keys(I18N.cs)) {   // the same {0} placeholder
    assert.equal(I18N.en[k].includes('{0}'), I18N.cs[k].includes('{0}'), `placeholder {0} at ${k}`);
  }
});

test('pickLang: ?lang= takes precedence over the browser language', () => {
  const { pickLang } = load();
  assert.equal(pickLang('?lang=en', 'cs-CZ'), 'en');
  assert.equal(pickLang('?lang=cs', 'en-US'), 'cs');
  assert.equal(pickLang('?x=1&lang=EN-gb', 'cs'), 'en');
  assert.equal(pickLang('', 'en-US'), 'en');
  assert.equal(pickLang('', 'en'), 'en');
  assert.equal(pickLang('', 'cs-CZ'), 'cs');
  assert.equal(pickLang('', 'de-DE'), 'en');   // English is the default
  assert.equal(pickLang('?lang=de', 'en-US'), 'en');   // an unknown language in the URL is ignored
  assert.equal(pickLang(undefined, undefined), 'en');
});

test('the Czech version: strings in formal address', () => {
  const { tp, LANG } = load('', 'cs-CZ');
  assert.equal(LANG, 'cs');
  assert.equal(tp('title'), 'Přehrávač');
  assert.equal(tp('tagline'), 'Digitální obsah pro vaši značku');
  assert.equal(tp('qrIg'), 'Sledujte nás na Instagramu');
  assert.equal(tp('fsHint'), 'Klepněte nebo stiskněte OK pro zvuk a celou obrazovku');
  assert.equal(tp('newVer'), 'nová verze – obnovuji');
  assert.equal(tp('offline', 'x'), 'offline – přehrávám uloženou kopii (x)');
  assert.equal(tp('h264why', 'video a.mp4'), 'prohlížeč neumí H.264 – video a.mp4');
  assert.equal(tp('h264'), 'tento prohlížeč neumí H.264, žádné video nepřehraje – použijte Firefox nebo Chrome od Googlu');
  assert.equal(tp('cannotPlay', 'q'), 'nepodařilo se přehrát: q');
  assert.equal(tp('noResponse'), 'bez odezvy');
  assert.equal(tp('image', '/media/a.jpg'), 'obrázek /media/a.jpg');
  // the HTML carries the English default texts
  assert.ok(html.includes('data-i18n="tagline">Digital content for your brand</span>'));
  assert.ok(html.includes('data-i18n="qrIg">Follow us on Instagram</span>'));
  assert.equal(load('').LANG, 'en');
});

test('en: the texts are in English and the {0} placeholder is filled in', () => {
  const { tp, LANG } = load('?lang=en', 'cs-CZ');
  assert.equal(LANG, 'en');
  assert.equal(tp('offline', 'boom'), 'offline – playing the stored copy (boom)');
  assert.equal(tp('cannotPlay'), 'cannot play: ');
  assert.equal(tp('nenalezeno'), 'nenalezeno');   // an unknown key = the key
});

test('the player with a cs browser language: the failure message is in Czech', async () => {
  const p = loadPlayer({ fetchImpl: async () => jsonResponse({ version: 'v1', settings: { transition: 'cut' },
    items: [{ type: 'image', url: '/media/a.jpg', seconds: 5 }] }),
    globals: { location: { pathname: '/tv/', search: '?lang=cs', origin: 'https://tv.test', reload() {} } } });
  await flush();
  p.imageFailed();
  await flush();
  assert.match(p.get('err').textContent, /^nepodařilo se přehrát: obrázek \/media\/a\.jpg$/);
});

test('QR label: default texts from the server follow the player language, a custom one stays', () => {
  const en = load('?lang=en');
  const cs = load('?lang=cs');
  const pairs = [
    ['Write to us', 'Napište nám'], ['Follow us on Instagram', 'Sledujte nás na Instagramu'],
    ['Find more here', 'Víc najdete tady'], ['Scan to take a look', 'Naskenujte a podívejte se']
  ];
  for (const [label, czech] of pairs) {
    assert.equal(en.qrLabelText(label), label);
    assert.equal(cs.qrLabelText(label), czech);
    assert.equal(en.qrLabelText(czech), label);   // Czech from an older server
    assert.equal(cs.qrLabelText(czech), czech);
  }
  assert.equal(en.qrLabelText('Napiš nám'), 'Write to us');
  assert.equal(cs.qrLabelText('Sleduj nás na Instagramu'), 'Sledujte nás na Instagramu');
  assert.equal(en.qrLabelText('Sledujte náš tým'), 'Sledujte náš tým');
  assert.equal(cs.qrLabelText('Our team'), 'Our team');
  assert.equal(en.qrLabelText(''), '');
  assert.equal(en.qrLabelText(undefined), '');
  assert.equal(en.qrLabelText('constructor'), 'constructor');
});
