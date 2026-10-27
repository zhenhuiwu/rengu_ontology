// Run: node scripts/check-real-object-resolver.mjs (shared real resolver and drawer guard, no server writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { parse, compileTemplate } from '@vue/compiler-sfc';

function load(file, imports, expose = '', globals = {}) {
  let source = readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8');
  if (file.endsWith('.vue')) {
    const parsed = parse(source, { filename: file });
    assert.equal(parsed.errors.length, 0);
    assert.equal(compileTemplate({ source: parsed.descriptor.template.content, filename: file, id: file }).errors.length, 0);
    source = parsed.descriptor.scriptSetup.content;
  }
  source = source.replaceAll('import.meta.env.DEV', 'false');
  const { outputText } = ts.transpileModule(`${source}\n${expose}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  runInNewContext(outputText, { exports, Error, ...globals, require: (name) => {
    if (name in imports) return imports[name];
    if (name.endsWith('.vue')) return {};
    throw new Error(`Missing dependency ${name}`);
  } });
  return exports;
}
const tick = () => new Promise(setImmediate);
const defer = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const context = vue.reactive({ demoAs: 'admin', previewAs: null, ver: null });
let real = true;
let calls = [];
let get = async (module, path) => {
  calls.push({ module, path });
  return { name: '数据库定义', api: 'DbApi', version: 'v8', owner: '数据库负责人', status: '已发布',
    def: [{ k: '定义', v: '数据库公式' }], expr: 'db.expr', trigger: { kind: '定时', text: '每日' } };
};
const property = (key, extra = {}) => ({ key, name: key, format: 'text', visibility: 'normal', ...extra });
let meta = { name: '数据库对象', titleProp: 'title', properties: [property('title'), property('secret', { sensitive: true }), property('hidden', { visibility: 'hidden' })] };
let rowRead = async () => ({ row: { title: '数据库标题', values: { title: '数据库标题', secret: 'NEVER SHOW', hidden: 'HIDDEN' } }, bound: true, denied: false });
const resolver = load('components/realObjectResolver.ts', {
  vue,
  '@/api/http': { currentApiContext: () => context, apiClient: (module) => ({ isReal: () => real, get: (path) => get(module, path) }) },
  '@/pages/objects/objMeta': { loadMeta: async () => meta, propLabel: (p) => p.name, fmtVal: (_p, v) => String(v ?? '--') },
  '@/pages/objects/objRows': { getRow: (...args) => { assert.equal(args[2], true); return rowRead(); } },
});
const resolve = resolver.resolveRealObject;
assert.equal(resolve('store', 'DB-1').status.text, '加载中');
await tick();
const view = resolve('store', 'DB-1');
assert.equal(view.title, '数据库标题');
assert.equal(view.kindLabel, '数据库对象');
assert.equal(view.fields.some((f) => f.label === 'hidden'), false);
assert.equal(view.fields.find((f) => f.label === 'secret').value, '已脱敏');
assert.equal(JSON.stringify(view).includes('NEVER SHOW'), false);
assert.equal(JSON.stringify(view).includes('HIDDEN'), false);
assert.equal(view.timeline, undefined);
assert.equal(view.tabs, undefined);
assert.equal(view.related, undefined);
meta = { ...meta, properties: [property('title', { sensitive: true })] };
resolve('store', 'sensitive-title'); await tick();
assert.equal(resolve('store', 'sensitive-title').title, 'sensitive-title');

for (const kind of ['metric', 'object-type', 'rule']) {
  resolve(kind, 'DB-code'); await tick();
  assert.equal(resolve(kind, 'DB-code').title, '数据库定义');
  assert.equal(resolve(kind, 'DB-code').timeline, undefined);
}
assert.ok(calls.some((c) => c.module === 'static' && c.path === '/metrics/DB-code'));
assert.ok(calls.some((c) => c.module === 'static' && c.path === '/object-types/DB-code'));
assert.ok(calls.some((c) => c.module === 'dynamic' && c.path === '/rules/DB-code'));
assert.equal(resolve('metric', 'DB-code').fields.some((f) => f.value === '数据库公式'), true);

const old = defer();
rowRead = () => old.promise;
resolve('order', 'stale'); await tick();
rowRead = async () => ({ row: null, bound: true, denied: true });
context.demoAs = 'viewer'; context.demoAs = 'admin';
resolve('order', 'stale'); await tick();
old.resolve({ row: { title: 'STALE PRIVATE', values: {} }, bound: true, denied: false });
await tick();
assert.equal(resolve('order', 'stale').status.text, '加载失败');
assert.ok(resolve('order', 'stale').fields[0].value.includes('无权'));
rowRead = async () => ({ row: null, bound: false, denied: false });
resolve('product', 'unbound'); await tick();
assert.ok(resolve('product', 'unbound').fields[0].value.includes('未接入'));
get = async () => { throw new Error('403 rejected'); };
resolve('metric', 'forbidden'); await tick();
assert.ok(resolve('metric', 'forbidden').fields[0].value.includes('403 rejected'));
real = false;
assert.equal(resolve('order', 'MOCK'), undefined, 'explicit mock keeps the old resolver path');
real = true;

const registry = load('components/objectRegistry.ts', {
  '@/mock': { storeById() { assert.fail('must not read a demo store in real mode'); } },
  './actionEventResolvers': { ACTION_EVENT_RESOLVERS: {} }, './pdeResolvers': { PDE_RESOLVERS: {} },
  './realObjectResolver': resolver,
});
assert.equal(registry.resolveObject('store', 'DB-1').title, 'DB-1'); // Context changed; loading/errors still bypass mock.

const drawer = load('components/DrawerPanel.vue', {
  vue: { ...vue, provide() {} }, '@ant-design/icons-vue': {},
  'ant-design-vue': { message: { success() { assert.fail('no fake writeback success'); } } },
  '@/api/config': { anyReal: () => true },
  '@/composables/useDrawer': { useDrawer: () => ({}) },
  '@/stores/confirm': { useConfirmStore: () => ({ ask() { assert.fail('no fake writeback confirmation'); } }) },
  './objectRegistry': { resolveObject: () => ({ kind: 'order', id: 'DB-1', fields: [] }) },
  '@/mock/paginate': { paginate: (items) => ({ items, total: items.length }) },
  '@/mock': { orderById() { assert.fail('no demo order or fake resend'); } },
}, 'export { resendOrder, resend };', { defineProps: () => ({ target: { kind: 'order', id: 'DB-1' }, level: 1 }) });
assert.equal(drawer.resendOrder.value, null);
await drawer.resend();
// The reused metadata module must itself reject real errors and never invent questions/actions.
let metadataFailure = { code: 'NETWORK_ERROR', status: 0 };
let failurePath = '/object-types';
const metadataCalls = [];
const metadataApi = { isReal: () => true, get: async (path, options) => {
  metadataCalls.push({ path, page: options.query.page });
  if (metadataFailure && path === failurePath) throw metadataFailure;
  if (path === '/object-types') return options.query.page === 1
    ? { items: [{ id: 'first', name: 'First', group: 'DB' }], total: 2 }
    : { items: [{ id: 'late', name: 'Late', group: 'DB' }], total: 2 };
  if (path.endsWith('/properties')) return { items: [{ api: options.query.page === 1 ? 'title' : 'secret', name: 'DB property', type: 'string' }], total: 2 };
  return { titleProp: 'title', pkProp: 'title', properties: [{ key: 'title', name: '数据库标题', dataType: 'string' }], metrics: [] };
} };
const metaApi = load('pages/objects/objMeta.ts', {
  '@/api/http': { apiClient: () => metadataApi, currentApiContext: () => context, isBackendDown: (e) => e.code === 'NETWORK_ERROR' },
  '@/api/static': { loadStatic: async () => {} },
  '@/mock/links': { LINK_OBJECTS: [{ id: 'DEMO', name: 'Demo', group: 'Demo' }] },
  '@/mock/objectProps': { OBJECT_PROPS: {}, objectMeta() { assert.fail('no demo question suggestions'); } },
  '@/mock/actionEvent': { ACTION_TYPES: [{ targetKind: 'late', name: 'DEMO ACTION' }] },
  '@/mock/metrics': { METRICS: { filter() { assert.fail('no demo metrics'); } } }, '@/mock/format': {},
});
await assert.rejects(metaApi.listTypes(), (e) => e === metadataFailure);
metadataFailure = null;
assert.equal((await metaApi.listTypes()).length, 2);
assert.equal(await metaApi.loadMeta('DEMO'), null, 'missing DB type must not be restored from LINK_OBJECTS');
const loaded = await metaApi.loadMeta('late');
assert.equal(loaded.name, 'Late');
assert.equal(loaded.sampleQuestions.length, 0);
assert.equal(loaded.metrics.length, 0);
assert.equal(metaApi.actionsOf({ id: 'late', name: 'Late' }).length, 0);
assert.ok(!metadataCalls.some((c) => c.path.endsWith('/properties')), 'use-area metadata never requires build-area property pages');
for (const [suffix, error] of [['/meta', { status: 403, code: 'FORBIDDEN' }], ['/meta', { status: 404, code: 'NOT_FOUND' }], ['/meta', { status: 0, code: 'NETWORK_ERROR' }]]) {
  context.ver = `error-${suffix}-${error.code}`;
  failurePath = `/object-types/late${suffix}`; metadataFailure = error;
  await assert.rejects(metaApi.loadMeta('late'), (e) => e === error);
}
console.log('Shared real object resolver checks passed (DB fields, sensitive/hidden isolation, context changes, failures, no fake resend).');
