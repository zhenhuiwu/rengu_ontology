// No browser, HTTP, storage, database or model calls. Exercise the actual dependency-free SDK.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

class Element {
  constructor(tag, document) {
    this.tagName = tag; this.document = document; this.children = []; this.attributes = {};
    this.listeners = new Map(); this.style = { setProperty(key, value) { this[key] = value; } };
    if (tag === 'iframe') this.contentWindow = {};
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  attachShadow() { this.shadowRoot = new Element('shadow', this.document); this.shadowRoot.host = this; return this.shadowRoot; }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(callback); }
  removeEventListener(type, callback) { this.listeners.get(type)?.delete(callback); }
  dispatch(type, event = {}) { for (const callback of this.listeners.get(type) || []) callback(event); }
  contains(child) { return child === this || this.children.some(node => node.contains(child)); }
  focus() {
    let parent = this;
    while (parent.parent) parent = parent.parent;
    if (parent.host) { parent.activeElement = this; this.document.activeElement = parent.host; }
    else this.document.activeElement = this;
  }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
  set innerHTML(_) { throw new Error('SDK must not use HTML interpolation'); }
}
const document = { createElement: tag => new Element(tag, document) };
document.body = document.createElement('body');
const window = new Element('window', document), timers = new Map();
window.setTimeout = callback => { const id = Symbol(); timers.set(id, callback); return id; };
window.clearTimeout = id => timers.delete(id);
for (const storage of ['localStorage', 'sessionStorage']) Object.defineProperty(window, storage, { get() { throw new Error('Host storage access prohibited'); } });
const source = readFileSync(new URL('../public/agent-widget.js', import.meta.url), 'utf8');
runInNewContext(source, { window, document, URL });
const settings = { baseUrl: 'https://agent.example.test/tools', ontologyId: 4, title: '<img src=x onerror=alert(1)>', theme: 'navy' };
for (const bad of ['javascript:alert(1)', 'https://name:password@agent.example.test', 'http://@agent.example.test', 'https://agent.example.test/?', 'https://agent.example.test/#', 'https://agent.example.test/ space']) {
  assert.throws(() => window.RenguAgent.mount({ ...settings, baseUrl: bad }));
}
for (const bad of [0, -1, '4x', 1.5, '9007199254740992']) assert.throws(() => window.RenguAgent.mount({ ...settings, ontologyId: bad }));
assert.throws(() => window.RenguAgent.mount({ ...settings, theme: 'unexpected' }));
assert.throws(() => window.RenguAgent.mount({ ...settings, width: '600px' }));
assert.throws(() => window.RenguAgent.mount({ ...settings, zIndex: '999;position:fixed' }));
const api = window.RenguAgent.mount(settings);
assert.equal(window.RenguAgent.mount({ ...settings }), api);
assert.throws(() => window.RenguAgent.mount({ ...settings, ontologyId: 16 }));
assert.equal(document.body.children.length, 1);
const host = document.body.children[0], shadow = host.shadowRoot;
const panel = shadow.children.find(node => node.className === 'panel');
const launcher = shadow.children.find(node => node.className === 'launcher');
const frame = panel.children.find(node => node.tagName === 'iframe');
const heading = panel.children[0], dismiss = heading.children[1];
const status = panel.children[1], retry = status.children[1];
assert.equal(heading.children[0].textContent, settings.title);
assert.equal(launcher.textContent, settings.title);
assert.equal(frame.src, undefined, 'iframe is lazy until first open');
api.open();
assert.equal(new URL(frame.src).pathname, '/tools/agent.html');
assert.equal(new URL(frame.src).searchParams.get('ontology'), '4');
assert.equal(new URL(frame.src).searchParams.get('theme'), 'navy');
assert.equal(new URL(frame.src).searchParams.get('embed'), '1');
assert.equal(frame.referrerPolicy, 'strict-origin');
assert.equal(panel.hidden, false);
assert.equal(launcher.attributes['aria-expanded'], 'true');
assert.equal(shadow.activeElement, dismiss);
window.dispatch('message', { origin: 'https://evil.example.test', source: frame.contentWindow, data: { type: 'rengu-agent:close' } });
window.dispatch('message', { origin: 'https://agent.example.test', source: {}, data: { type: 'rengu-agent:close' } });
assert.equal(panel.hidden, false);
window.dispatch('message', { origin: 'https://agent.example.test', source: frame.contentWindow, data: { type: 'rengu-agent:ontology', ontology: 16 } });
assert.equal(new URL(frame.src).searchParams.get('ontology'), '4');
window.dispatch('message', { origin: 'https://agent.example.test', source: frame.contentWindow, data: { type: 'rengu-agent:ready' } });
assert.equal(status.hidden, true);
assert.equal(timers.size, 0);
window.dispatch('message', { origin: 'https://agent.example.test', source: frame.contentWindow, data: { type: 'rengu-agent:close' } });
assert.equal(panel.hidden, true);
assert.equal(shadow.activeElement, launcher);
api.open();
let prevented = false;
host.dispatch('keydown', { key: 'Escape', preventDefault() { prevented = true; } });
assert.equal(prevented, true);
assert.equal(panel.hidden, true);
api.open();
launcher.focus();
host.dispatch('keydown', { key: 'Escape', preventDefault() { throw new Error('Do not consume keys outside the panel'); } });
assert.equal(panel.hidden, false);
retry.dispatch('click');
for (const callback of [...timers.values()]) callback();
assert.equal(retry.hidden, false);
api.destroy();
api.destroy();
api.open();
assert.equal(document.body.children.length, 0);
assert.equal(window.listeners.get('message').size, 0);
assert.equal(timers.size, 0);
const next = window.RenguAgent.mount({ ...settings, ontologyId: 16 });
assert.notEqual(next, api);
next.destroy();
runInNewContext(source, { window, document, URL });
assert.equal(document.body.children.length, 0, 'loading the SDK twice must not mount or duplicate anything');
console.log('Agent widget: parameters, literal titles, lazy/idempotent mount, exact message boundaries, focus and teardown passed (in memory only).');
