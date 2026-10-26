// Run: node scripts/check-pde-integration.mjs (no server or database writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function load(path, imports) {
  const source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
    .replaceAll('import.meta.env', "({ VITE_PALANTIR_CR_WRITE: '1' })");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  runInNewContext(outputText, { exports, require: (name) => {
    if (name === 'vue') return require('vue');
    assert.ok(name in imports, `Missing test dependency ${name} in ${fileURLToPath(new URL(path, import.meta.url))}`);
    return imports[name];
  } });
  return exports;
}

let context = { demoAs: 'admin' };
const drafts = [{ id: 'CR-1', changes: 99 }];
let lock = 7;
let conflict = false;
let deleteFailure;
let allocationFails = false;
let headRead = true;
let oldRows = [];
const puts = [];
const posts = [];
const head = () => ({ id: 'CR-1', title: 'Draft', owner: 'Owner', updatedAt: 'now', changes: 3, lockVersion: lock });
const api = {
  isReal: () => true,
  post: async (path, body) => { posts.push({ path, body }); if (path.endsWith('/targets:allocate')) { if (allocationFails) throw Error('allocation failed'); return { id: 'CR-1', target: { kind: body.kind, id: body.kind === 'link' ? 'L-0001' : 'K-027', isNew: true, name: body.name } }; } return head(); },
  delete: async () => { if (deleteFailure) throw deleteFailure; },
  get: async (path) => {
    if (path.endsWith('/items')) { assert.ok(headRead, 'read the lock before starting the items request'); return oldRows; }
    headRead = false; await Promise.resolve(); headRead = true; return head();
  },
  put: async (_path, body) => {
    puts.push(body);
    if (conflict) throw new Error('VERSION_CONFLICT');
    assert.equal(body.lockVersion, lock++);
    return { changes: 4, lockVersion: lock };
  },
};
const http = { apiClient: () => api, currentApiContext: () => context, isApiError: (e) => Number.isInteger(e?.status) };
const staticApi = load('api/static.ts', {
  './http': http, '@/mock/releases': { DRAFTS: drafts }, '@/mock/links': { LINK_OBJECTS: [], LINKS: [], PATH_GROUPS: [] },
  '@/mock/objectProps': { OBJECT_PROPS: {} }, '@/mock/metricCatalog': { METRIC_LIST: [], METRIC_DEF: [], METRIC_DIMS: [], METRIC_VERSIONS: [], METRIC_REFS: [] },
  '@/mock/dimensions': { DIM_ITEMS: [] }, '@/mock/knowledge': { KNOWLEDGE: [] },
});
const action = load('api/action.ts', {
  './http': http, './static': staticApi, './integration': {}, './fallback': { backend: { down: false } },
  '@/mock/actionEvent': {}, '@/mock/releases': { DRAFTS: drafts },
});
await action.crStart({ kind: 'event', id: 'type' }, { title: 'Draft', desc: 'Event explanation' });
assert.equal(posts.at(-1).body.reason, 'Event explanation');
await staticApi.crStart({ kind: 'object', id: 'store' }, { title: 'Object draft', desc: 'Object explanation' });
assert.equal(posts.at(-1).body.reason, 'Object explanation');
assert.equal(drafts[0].changes, 3, 'registration must use server changes');
assert.equal(action.crChanges['CR-1'], 3);
const writer = action.crWriter('event', () => ({ id: 'type', name: 'Type' }));
const changes = [{ section: 'payload', rows: [{ key: 'x', op: 'add', after: { api: 'x' } }] }];
await writer.save('CR-1', changes);
await writer.save('CR-1', changes);
assert.equal(puts[1].lockVersion, 8, 'each save must read the current lock');
assert.equal(drafts[0].changes, 4);
conflict = true;
await assert.rejects(writer.save('CR-1', changes), /VERSION_CONFLICT/);
assert.equal(action.crChanges['CR-1'], 4, 'failed writes must preserve saved changes');
deleteFailure = Object.assign(new Error('NOT_FOUND'), { status: 404 });
await writer.discard('CR-1');
assert.equal(action.crChanges['CR-1'], 3, 'already removed targets still allow local discard and reload');
deleteFailure = Object.assign(new Error('FORBIDDEN'), { status: 403 });
await assert.rejects(writer.discard('CR-1'), /FORBIDDEN/);
assert.equal(action.crChanges['CR-1'], 3, 'other discard failures preserve local state');
conflict = false;
oldRows = [{ key: 'new_prop', op: 'add', after: { api: 'new_prop' }, target: { kind: 'object', id: 'store' }, section: 'properties', updatedAt: 'now' }];
await staticApi.crSave('CR-1', { kind: 'object', id: 'store' }, 'properties', [{ key: 'new_prop', op: 'delete' }]);
assert.equal(puts.at(-1).rows.length, 0, 'removing a newly added property removes its draft row');
oldRows = [];


let real = true;
let failure;
let requests = 0;
const type = { id: 'type', name: 'Type', sourceRef: 'source', source: 'internal', object: 'Object', api: 'Type' };
const mockEvent = { id: 'EV-1', typeId: 'type', at: '2026-10-26 00:00:00', outcome: '已投递', summary: 'mock', objLabel: 'Object' };
const resolvers = load('components/actionEventResolvers.ts', {
  '@/api/http': http,
  '@/api/action': { actionApi: { isReal: () => real }, dynamicApi: {}, eventTypes: () => [type], getEvent: async () => {
    requests++;
    if (failure) throw failure;
    return { ...mockEvent, summary: context.demoAs };
  } },
  '@/api/fallback': { backend: { down: false }, fallBackIfDown: () => false },
  '@/mock/dynamic': {}, '@/mock/actionEvent': { eventById: () => mockEvent, eventTypeById: () => type },
}).ACTION_EVENT_RESOLVERS;
const summary = () => resolvers.event('EV-1').fields.find((f) => f.label === '载荷摘要')?.value;
assert.equal(summary(), undefined);
assert.equal(resolvers.event('EV-1').status.text, '加载中');
await new Promise(setImmediate);
assert.equal(summary(), 'admin');
assert.equal(resolvers.event('EV-1').timeline.length, 1, 'real event rows must not fabricate retry or delivery stages');
assert.equal(resolvers.event('EV-1').timeline[0].text, '事件记录');
assert.equal(requests, 1, 'in-flight and resolved requests must be reused');
context = { demoAs: 'viewer', ver: 'v1', previewAs: 'other' };
failure = Object.assign(new Error('FORBIDDEN'), { status: 403 });
assert.equal(summary(), undefined, 'changed identity must not reuse privileged results');
await new Promise(setImmediate);
assert.equal(summary(), undefined, 'forbidden request must not reveal mock or earlier real fields');
assert.equal(resolvers.event('EV-1').status.text, '加载失败');
assert.equal(requests, 2);
context = { ...context, ver: 'v2' };
failure = Object.assign(new Error('NOT_FOUND'), { status: 404 });
assert.equal(summary(), undefined);
await new Promise(setImmediate);
assert.equal(summary(), undefined, '404 must not return a mock event');
assert.equal(resolvers.event('EV-1').status.text, '加载失败');
real = false;
assert.equal(summary(), 'mock');

let discardFails = false;
let discardResult = true;
const cancelled = [];
const views = [];
const { useStaticCr } = load('pages/ontology/parts/useStaticCr.ts', {
  '@/api/http': http,
  'ant-design-vue': { message: { error() {}, success() {} } },
  '@/api/static': { isStaticReal: () => false, staticContextKey: () => '', crDiscard: async () => { if (discardFails) throw new Error('failed'); return discardResult; }, crErr: String },
});
const dv = { draft: { value: { id: 'CR-1' } }, cancel: (...args) => cancelled.push(args), setView: (v) => views.push(v) };
const cr = useStaticCr('object', () => ({ id: 'order' }), dv);
assert.equal(await cr.discard(), true);
discardResult = false; // mock mode still needs to cancel its local registration.
assert.equal(await cr.discard(), true);
assert.deepEqual(cancelled, [['CR-1', 'order'], ['CR-1', 'order']]);
assert.deepEqual(views, ['published', 'published']);
discardFails = true;
assert.equal(await cr.discard(), false);
assert.equal(cancelled.length, 2, 'failure must leave the local target registered');
console.log('PDE integration checks passed (registration, changes, locks/conflict, event context isolation, draft discard).');
