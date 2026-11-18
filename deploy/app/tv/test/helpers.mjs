// Harness for the client tests: the inline script from player.html runs in
// node:vm with minimal DOM mocks and controllable fake timers. No dependencies.
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const PLAYER_HTML = path.join(HERE, '..', 'player.html');

// Extracts the inline <script> (without src) that holds the main player logic.
export function extractPlayerScript(file = PLAYER_HTML) {
  const html = fs.readFileSync(file, 'utf8');
  const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  const main = blocks.find(b => b.includes('POLL_MS'));
  if (!main) throw new Error('main player script not found');
  return main;
}

export class MockElement {
  constructor(tag = 'div', id = '') {
    this.tagName = tag; this.id = id; this.children = [];
    this.style = {}; this.attrs = {}; this.listeners = {};
    this.textContent = ''; this.className = ''; this.src = ''; this.parent = null;
    const cls = new Set();
    this.classList = {
      add: c => cls.add(c), remove: c => cls.delete(c),
      toggle: (c, on) => { (on === undefined ? !cls.has(c) : on) ? cls.add(c) : cls.delete(c); },
      contains: c => cls.has(c),
    };
    this._cls = cls;
    this.paused = false; this.ended = false; this.muted = false;
  }
  get firstChild() { return this.children[0] || null; }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; }
  remove() { if (this.parent) this.parent.removeChild(this); }
  addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
  emit(ev, arg) { for (const f of this.listeners[ev] || []) f(arg); }
  setAttribute(k, v) { this.attrs[k] = v; }
  removeAttribute(k) { delete this.attrs[k]; }
  querySelector() { return null; }
  play() { return Promise.resolve(); }
  pause() {}
  load() {}
  canPlayType() { return 'probably'; }
}

// Controllable timers: nothing runs by itself, advance(ms) moves the time.
export function makeClock() {
  let now = 0, nextId = 1;
  const timers = new Map();
  const clock = {
    setTimeout(fn, ms = 0) { const id = nextId++; timers.set(id, { fn, at: now + ms, every: null }); return id; },
    setInterval(fn, ms) { const id = nextId++; timers.set(id, { fn, at: now + ms, every: ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    get pending() { return timers.size; },
    get now() { return now; },
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        let best = null;
        for (const [id, t] of timers) if (t.at <= end && (!best || t.at < best[1].at)) best = [id, t];
        if (!best) break;
        const [id, t] = best;
        now = t.at;
        if (t.every) t.at += t.every; else timers.delete(id);
        t.fn();
        await flush();
      }
      now = end;
    },
  };
  clock.clearInterval = clock.clearTimeout;
  return clock;
}

// Finishes running promise chains (fetch -> json -> poll).
export async function flush(n = 8) { for (let i = 0; i < n; i++) await new Promise(r => setImmediate(r)); }

export const jsonResponse = (data, status = 200) => ({
  ok: status >= 200 && status < 300, status, json: async () => data,
});

// Loads the player into a sandbox. fetchImpl(url, opts) => response | reject.
// globals: overrides/adds sandbox globals (navigator, caches, URL, Date...);
// a function receives the clock so time can be tied to the fake timers.
export function loadPlayer({ fetchImpl, pathname = '/tv/', source = extractPlayerScript(), globals = null } = {}) {
  const clock = makeClock();
  const els = new Map();
  const get = id => { if (!els.has(id)) els.set(id, new MockElement('div', id)); return els.get(id); };
  const images = [];
  class MockImage {
    constructor() { this.onload = null; this.onerror = null; this.src = ''; images.push(this); }
  }
  const body = new MockElement('body');
  const fetches = [];
  const document = {
    body, hidden: false, listeners: {},
    documentElement: Object.assign(new MockElement('html'), { clientWidth: 1920, clientHeight: 1080 }),
    getElementById: get,
    createElement: tag => new MockElement(tag),
    createTextNode: text => ({ data: String(text), textContent: String(text) }),
    querySelector: () => null,
    addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); },
  };
  const sandbox = {
    document, Image: MockImage, URL, encodeURIComponent, console,
    location: { pathname, origin: 'https://tv.test', reload() { sandbox.__reloaded = (sandbox.__reloaded || 0) + 1; } },
    navigator: {}, screen: { width: 1920, height: 1080 },
    innerWidth: 1920, innerHeight: 1080, devicePixelRatio: 1,
    requestAnimationFrame: fn => fn(),
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    setInterval: clock.setInterval, clearInterval: clock.clearInterval,
    addEventListener() {},
    fetch: (url, opts) => { fetches.push(String(url)); return fetchImpl(String(url), opts); },
  };
  if (globals) Object.assign(sandbox, typeof globals === 'function' ? globals(clock) : globals);
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'player.html<script>' });
  const stage = get('stage');
  return {
    clock, els, get, images, fetches, sandbox, stage,
    // what is on the canvas right now (image via the background-image of the last layer)
    current() {
      const l = stage.lastElementChild;
      const m = l && /url\("(.*)"\)/.exec(l.style.backgroundImage || '');
      return m ? m[1] : null;
    },
    // simulates a successful load of the last image
    imageLoaded() { const i = images[images.length - 1]; i.onload && i.onload(); },
    imageFailed() { const i = images[images.length - 1]; i.onerror && i.onerror(); },
  };
}

// YYYY-MM-DD in local time, shifted by `days` days from today.
export function dateStr(days = 0) {
  const d = new Date(); d.setDate(d.getDate() + days);
  const p = n => (n < 10 ? '0' : '') + n;
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}
