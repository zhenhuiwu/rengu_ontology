/** Actual Releases.vue setup/watch/load/publish functions, with memory request spies only. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { computed, effectScope, nextTick, reactive, ref, watch } from 'vue';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';

const filename = 'src/pages/ontology/Releases.vue';
const { descriptor, errors } = parse(readFileSync(filename, 'utf8'), { filename });
assert.deepEqual(errors, []);
const script = compileScript(descriptor, { id: 'release-history' });
assert.deepEqual(compileTemplate({ filename, id: 'release-history', source: descriptor.template.content,
  compilerOptions: { bindingMetadata: script.bindings } }).errors, []);
const tree = ts.createSourceFile(filename, descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true);
assert.ok(!tree.statements.filter(ts.isImportDeclaration).some(node => /change.requests|\/cr\b/i.test(node.getText())));
const code = ts.transpileModule(tree.statements.filter(node => !ts.isImportDeclaration(node))
  .map(node => node.getText(tree)).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;
const settle = async () => { await nextTick(); await Promise.resolve(); await nextTick(); };

function harness() {
  const context = reactive({ ontologyId: 8, ver: '' }), route = reactive({ query: {} });
  const app = reactive({ ontologyVersion: '', ontologyNextVersion: '', ontologyPublishedAt: '' });
  const requests = [], cleanup = [], timers = new Map(), messages = [], scope = effectScope();
  let timerId = 0;
  const request = (kind, args = {}, signal) => new Promise((resolve, reject) => {
    requests.push({ kind, args, signal, context: JSON.stringify(context), resolve, reject });
  });
  const router = { replace: async ({ query }) => { route.query = query; }, push: async value => { router.pushed = value; } };
  const state = {
    Error, AbortController, ref, watch, computed, currentApiContext: () => context,
    useRoute: () => route, useRouter: () => router, useAppStore: () => app,
    useReadonly: () => ({ readonly: ref(false), reason: ref('') }),
    useDebouncedRef: source => {
      const value = ref(source.value); let id;
      watch(source, next => { timers.delete(id); id = ++timerId; timers.set(id, () => { value.value = next; }); });
      return value;
    },
    usePagedQuery: options => {
      const page = computed({ get: () => Number(route.query.page || 1),
        set: value => { void router.replace({ query: { ...route.query, page: value > 1 ? value : undefined } }); } });
      const size = computed({ get: () => Number(route.query.size || options.defaultSize),
        set: value => { void router.replace({ query: { ...route.query, page: undefined, size: value } }); } });
      watch(options.filters, () => { if (page.value !== 1) page.value = 1; });
      return { page, size, mode: ref(20) };
    },
    onBeforeUnmount: callback => cleanup.push(callback),
    loadMyProfile: () => request('profile'),
    fetchReleasePage: (query, signal) => request('page', { ...query }, signal),
    fetchCurrentAfterWrite: () => request('base'),
    publishFormalRelease: (lockVersion, note) => request('publish', { lockVersion, note }),
    fetchCurrentRelease: () => request('current'), loadReleases: () => request('directory'),
    isApiError: error => typeof error?.code === 'string',
    message: Object.fromEntries(['success', 'warning', 'error', 'info'].map(kind => [kind, text => messages.push({ kind, text })])),
  };
  vm.createContext(state);
  scope.run(() => vm.runInContext(code + '\nglobalThis.pageState = { reload, openPublish, publish, viewVersion, records, total, current, loading, error, mayPublish, publishOpen, note, publishing, issues, search, keyword, pq };', state));
  const api = state.pageState;
  const calls = kind => requests.filter(item => item.kind === kind);
  const latest = kind => { const value = calls(kind).at(-1); assert.ok(value, `missing ${kind} request`); return value; };
  const dispose = () => { cleanup.forEach(callback => callback()); scope.stop(); timers.clear(); };
  const flushSearch = async () => { await settle(); for (const callback of timers.values()) callback(); timers.clear(); await settle(); };
  return { api, context, app, route, router, messages, requests, calls, latest, dispose, flushSearch };
}

const pageResponse = (items = [], page = 1, size = 20) => ({ items, current: items[0]?.ver ?? null, total: items.length, page, size });
async function ready(h, publish = false) {
  h.latest('profile').resolve({ capabilities: { publish } });
  h.latest('page').resolve(pageResponse());
  await settle();
}

const h = harness();
assert.deepEqual(h.requests.map(r => r.kind).sort(), ['page', 'profile']);
await ready(h);
assert.equal(h.api.records.value.length, 0);
h.api.openPublish();
assert.equal(h.api.publishOpen.value, false);
h.api.publishOpen.value = true;
await h.api.publish();
assert.equal(h.calls('publish').length + h.calls('base').length, 0);
h.api.publishOpen.value = false;
h.api.pq.page.value = 3; await settle();
assert.deepEqual(h.latest('page').args, { page: 3, size: 20, kw: undefined });
h.latest('page').resolve({ ...pageResponse([{ ver: 'v3' }], 3), total: 100 }); await settle();
h.api.pq.size.value = 25; await settle();
assert.deepEqual(h.latest('page').args, { page: 1, size: 25, kw: undefined });
h.latest('page').resolve({ ...pageResponse([], 1, 25), total: 100 }); await settle();
h.api.search.value = 'old'; h.api.search.value = 'needle'; await h.flushSearch();
assert.deepEqual(h.latest('page').args, { page: 1, size: 25, kw: 'needle' });
h.latest('page').reject(new Error('fixture read failed')); await settle();
assert.equal(h.api.records.value.length, 0);
assert.match(h.api.error.value, /fixture read failed/);
void h.api.reload();
const oldPage = h.latest('page');
h.context.ontologyId = 9; await settle();
const newPage = h.latest('page');
assert.equal(oldPage.signal.aborted, true);
oldPage.resolve(pageResponse([{ ver: 'old-scope' }])); await settle();
assert.equal(h.api.records.value.length, 0);
newPage.resolve(pageResponse([{ ver: 'new-scope' }], 1, 25));
h.latest('profile').resolve({ capabilities: { publish: false } }); await settle();
assert.equal(h.api.records.value[0].ver, 'new-scope');
h.api.viewVersion({ ver: 'v7' });
assert.equal(h.router.pushed.query.ontology, 9);
assert.equal(h.router.pushed.query.ver, 'v7');
void h.api.reload();
const unmounted = h.latest('page');
h.dispose(); unmounted.resolve(pageResponse([{ ver: 'after-unmount' }])); await settle();
assert.equal(h.api.records.value[0].ver, 'new-scope');

async function startPublish(h) {
  await ready(h, true);
  h.api.openPublish(); h.api.note.value = '  fixture note  ';
  const result = h.api.publish();
  h.latest('base').resolve({ lockVersion: 7 }); await settle();
  assert.deepEqual(h.latest('publish').args, { lockVersion: 7, note: 'fixture note' });
  return { result };
}
const successful = harness();
const success = await startPublish(successful);
successful.latest('publish').resolve({ ver: 'v8' }); await settle();
assert.equal(successful.api.publishOpen.value, false);
successful.latest('current').resolve({ current: 'v8', next: 'v9', formalRelease: { publishedAt: 'fixture-date' } });
successful.latest('directory').resolve([]); await settle();
successful.latest('page').resolve(pageResponse([{ ver: 'v8' }]));
await success.result;
assert.equal(successful.app.ontologyVersion, 'v8');
assert.equal(successful.app.ontologyNextVersion, 'v9');
assert.equal(successful.app.ontologyPublishedAt, 'fixture-date');
assert.equal(successful.api.records.value[0].ver, 'v8');
assert.equal(successful.api.publishing.value, false);
successful.dispose();

const failedRefresh = harness();
const receipt = await startPublish(failedRefresh);
failedRefresh.latest('publish').resolve({ ver: 'v8' }); await settle();
failedRefresh.latest('current').reject(new Error('fixture refresh failed'));
failedRefresh.latest('directory').resolve([]); await receipt.result; await settle();
assert.equal(failedRefresh.api.publishOpen.value, false);
assert.equal(failedRefresh.app.ontologyVersion, 'v8');
assert.ok(failedRefresh.messages.some(m => m.kind === 'success'));
assert.ok(!failedRefresh.messages.some(m => m.kind === 'error'), 'acknowledged publication was reported as failed');
assert.ok(failedRefresh.messages.some(m => m.kind === 'warning'), 'refresh failure was silently hidden');
failedRefresh.dispose();

const blocked = harness();
const rejected = await startPublish(blocked);
blocked.latest('publish').reject(Object.assign(new Error('fixture blocked'), {
  code: 'FORMAL_RELEASE_BLOCKED', details: { issues: [{ section: 'object', id: 'fixture', message: 'incomplete' }] },
}));
await rejected.result;
assert.equal(blocked.api.publishOpen.value, true);
assert.equal(blocked.api.issues.value.length, 1);
assert.equal(blocked.calls('current').length, 0);
blocked.dispose();

const switched = harness(); await ready(switched, true);
switched.api.openPublish(); const pendingPublish = switched.api.publish();
const oldBase = switched.latest('base');
switched.context.ontologyId = 10; await settle();
oldBase.resolve({ lockVersion: 7 }); await pendingPublish;
assert.equal(switched.calls('publish').length, 0);
assert.equal(switched.api.mayPublish.value, false);
switched.dispose();
console.log('Release history page watchers/publish/SFC passed: paged reads, capability/scope guards, no demo/CR, durable success distinct from refresh errors.');
