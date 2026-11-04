// Actual API + session modules, in-memory fetch/streams only; no HTTP/model/DB calls.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';

function moduleAt(path, deps, globals = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  runInNewContext(js, { exports, Error, AbortController, DOMException, URLSearchParams, TextDecoderStream, ...globals,
    require: key => { assert.ok(key in deps, key); return deps[key]; } });
  return exports;
}
const context = vue.reactive({ ontologyId: '17', demoAs: null, ver: null, previewAs: null, dataPurpose: 'verification' });
const fetches = [], posts = [], errors = [];
let respond, fallback;
const turn = { id: 'T-SYNTHETIC', kind: 'answer', question: '合成问题', answer: { card: 'table', text: '完整合成回答', table: { items: [{ value: 1 }] } } };
const output = { turnId: turn.id, turn };
const detail = id => ({ id, title: '新会话', turns: [] });
class ApiError extends Error { constructor(status, body) { super(body.message); this.status = status; Object.assign(this, body); } }
const http = {
  API_BASE: '/api', currentApiContext: () => ({ ...context }), ApiError, isApiError: e => e instanceof ApiError,
  handleUnauthorized() {},
  apiClient: () => ({ prefix: 'qa', isReal: () => true, get: async path => detail(decodeURIComponent(path.split('/').at(-1))),
    post: async (path, body, options) => { posts.push({ path, body, options }); return fallback ? fallback() : output; } }),
};
const api = moduleAt('../src/api/qa.ts', { './http': http }, { fetch: (url, options) => { fetches.push({ url, options }); return respond(options); } });
const scope = vue.effectScope();
let sessions;
scope.run(() => { sessions = moduleAt('../src/pages/qa/useQaSession.ts', {
  vue, 'ant-design-vue': { message: { error: text => errors.push(text) } }, '@/mock': { QA_SESSION_LIST: [], QA_TODAY: 'synthetic' },
  '@/api/qa': api, '@/api/http': http, './qaFallback': { qaLive: vue.ref(true), qaFallBackIfDown: () => false },
}); });
const session = sessions.useQaSession();
async function flush() { for (let i = 0; i < 20; i++) await Promise.resolve(); await vue.nextTick(); }
async function select(id) { session.activeId.value = id; await flush(); }
function send(text) {
  const done = new Promise(resolve => { const stop = vue.watch(session.sending, value => { if (!value) { stop(); resolve(); } }, { flush: 'sync' }); });
  assert.equal(session.send(text), true);
  return done;
}
function json(status, data) { return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }); }
let cancellations = 0;
function stream(parts, close = true, hangingCancel = false) {
  return new Response(new ReadableStream({ start(controller) { for (const part of parts) controller.enqueue(new TextEncoder().encode(part)); if (close) controller.close(); },
    cancel() { cancellations++; if (hangingCancel) return new Promise(() => {}); } }), { headers: { 'Content-Type': 'text/event-stream' } });
}
const terminal = `event: done\ndata: ${JSON.stringify(output)}\n\n`;
function bounded(task) {
  let timer;
  return Promise.race([task, new Promise((_, reject) => { timer = setTimeout(() => reject(Error('terminal event waited for EOF/cancellation')), 500); })]).finally(() => clearTimeout(timer));
}

await select('S-SUCCESS');
respond = () => stream(['event: status\r', '\ndata: {"text":"查询中"}\r\n\r', `\n${terminal}`], false, true);
await bounded(send('成功问题')); await flush();
assert.equal(session.detail.value.turns[0].answer.text, turn.answer.text, 'terminal done preserves the complete turn without waiting for EOF');
assert.ok(cancellations > 0, 'terminal result cancels its reader without awaiting a hanging cancellation');
assert.equal(fetches.at(-1).options.headers['X-Ontology-Id'], '17');
assert.equal(fetches.at(-1).options.headers['X-Data-Purpose'], 'verification');
assert.equal(api.qaRequestOptions().headers['X-Data-Purpose'], 'verification');
assert.equal(JSON.parse(fetches.at(-1).options.body).purpose, undefined, 'purpose comes from the caller context header');
assert.equal(fetches.at(-1).options.credentials, 'include');

context.dataPurpose = null;
await select('S-TAIL'); respond = () => stream([terminal.trimEnd()]);
await send('最后一块无空行'); assert.equal(session.detail.value.turns.length, 1, 'EOF residual data is parsed');
assert.equal(fetches.at(-1).options.headers['X-Data-Purpose'], undefined, 'an undeclared purpose is never inherited');
respond = () => stream(['event: error\ndata: {"code":"SYNTHETIC_FAILED","message":"合成失败"}\n\n'], false, true);
await bounded(send('终态错误')); assert.match(session.sendError.value, /合成失败/);

for (const [name, response] of [
  ['non-sse accepted', () => json(200, output)],
  ['no done', () => stream(['event: status\ndata: {"text":"正在处理"}\n\n'])],
  ['reader failure', () => new Response(new ReadableStream({ pull(controller) { controller.error(new TypeError('synthetic broken stream')); } }), { headers: { 'Content-Type': 'text/event-stream' } })],
  ['network failure', () => Promise.reject(new TypeError('synthetic network failure'))],
  ['business 404', () => json(404, { code: 'NOT_FOUND', message: '会话不存在' })],
  ['ambiguous HTML 404', () => new Response('not found', { status: 404, headers: { 'Content-Type': 'text/html' } })],
]) {
  await select(`S-${name}`); respond = response;
  const count = posts.length; await send(name);
  assert.equal(posts.length, count, `${name} must never submit a second POST`);
  assert.equal(session.detail.value.turns.length, 0); assert.equal(session.draft.value, name);
}
for (const response of [() => json(405, { code: 'METHOD_NOT_ALLOWED' }), () => json(501, {}),
  () => json(404, { detail: 'Not Found' }), () => json(404, { code: 'NOT_FOUND', message: 'Not Found' })]) {
  await select('S-FALLBACK'); respond = response;
  const count = posts.length; await send('确定不支持流');
  assert.equal(posts.length, count + 1, 'confirmed unsupported route/method falls back exactly once');
  assert.ok(posts.at(-1).options.signal instanceof AbortSignal);
  assert.equal(session.detail.value.turns.length, 1);
}

function deferred() { let resolve; return { promise: new Promise(done => { resolve = done; }), resolve }; }
await select('S-OLD'); const pending = deferred(); respond = () => pending.promise;
const old = send('切会话问题'), count = posts.length, signal = fetches.at(-1).options.signal;
session.activeId.value = 'S-NEW'; assert.equal(signal.aborted, true); await flush();
pending.resolve(json(405, {})); await old; await flush();
assert.equal(posts.length, count, 'late unsupported reply cannot fall back after selection changed');
assert.equal(session.detail.value.id, 'S-NEW'); assert.equal(session.detail.value.turns.length, 0);

const crossing = deferred(); respond = () => crossing.promise;
const changed = send('切本体问题'), contextSignal = fetches.at(-1).options.signal;
context.ontologyId = '18'; assert.equal(contextSignal.aborted, true);
crossing.resolve(stream([terminal])); await changed; await flush();
assert.equal(session.detail.value, null); assert.equal(session.draft.value, '');

await select('S-SYNC'); const sync = deferred(); fallback = () => sync.promise; respond = () => json(405, {});
const syncDone = send('同步回退中切换'); await flush();
const syncSignal = posts.at(-1).options.signal;
context.ontologyId = '19'; assert.equal(syncSignal.aborted, true);
sync.resolve(output); await syncDone; await flush(); assert.equal(session.detail.value, null);
scope.stop();
console.log('QA stream boundary passed: terminal-before-EOF, residual data, no duplicate accepted ask, safe unsupported fallback, abort/context isolation.');
