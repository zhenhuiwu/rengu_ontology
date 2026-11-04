// Executes the actual editor setup with memory requests; no browser, database or backend tests.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { computed, effectScope, nextTick, reactive, ref, watch } from 'vue';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';

const filename = 'src/pages/ontology/parts/object/LifecycleEditor.vue';
const { descriptor, errors } = parse(readFileSync(filename, 'utf8'), { filename });
assert.deepEqual(errors, []);
const compiled = compileScript(descriptor, { id: 'lifecycle-check' });
assert.deepEqual(compileTemplate({ filename, id: 'lifecycle-check', source: descriptor.template.content,
  compilerOptions: { bindingMetadata: compiled.bindings } }).errors, []);
const tree = ts.createSourceFile(filename, descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true);
const source = ts.transpileModule(tree.statements.filter(s => !ts.isImportDeclaration(s)).map(s => s.getText(tree)).join('\n'),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const settle = async () => { await nextTick(); await Promise.resolve(); await nextTick(); };
const state = (key, kind) => ({ key, name: key, kind, desc: '', count: 0, x: 40, y: 80 });
function harness(lifecycle = null) {
  const props = reactive({ open: false, objectId: 'Device', lifecycle, readonly: false });
  const context = reactive({ ontologyId: '8', ver: '' }), calls = [], events = [], versions = [], cleanup = [];
  const scope = effectScope();
  const request = (kind, args) => new Promise((resolve, reject) => calls.push({ kind, args, resolve, reject }));
  const bindings = {
    ref, reactive, computed, watch, AbortController, Error,
    defineProps: () => props, defineEmits: () => (name, value) => {
      events.push([name, value]); if (name === 'update:open') props.open = value;
    },
    currentApiContext: () => context, useReadonly: () => ({ readonly: ref(false) }),
    useRoute: () => ({ query: {} }), useRouter: () => ({ replace: async () => undefined }),
    apiClient: () => ({ get: (path, options) => request('get', { path, ...options }) }),
    fetchCurrentAfterWrite: () => request('head'), saveLifecycle: (id, body) => request('save', { id, body }),
    invalidateStaticAfterWrite: ver => versions.push(ver), message: { success: text => events.push(['success', text]) },
    useUnsavedGuard: () => undefined, onBeforeRouteUpdate: () => undefined,
    onBeforeUnmount: fn => cleanup.push(fn), confirmDiscard: async () => true,
  };
  vm.createContext(bindings);
  scope.run(() => vm.runInContext(source + '\nthis.editor = { form, loading, saving, loadError, saveError, problem, stateFields, originalStates, addState, removeState, addTransition, save, close, loadCatalog, selectReference, catalog };', bindings));
  const latest = kind => { const call = calls.filter(c => c.kind === kind).at(-1); assert.ok(call, kind); return call; };
  const open = async () => {
    props.open = true; await settle();
    latest('head').resolve({ lockVersion: 15 });
    latest('get').resolve({ items: [{ api: 'id', name: '编号', type: 'bigint' }, { api: 'status', name: '状态', type: 'string' }], total: 2 });
    await settle();
  };
  return { props, context, calls, events, versions, latest, open, editor: bindings.editor,
    dispose() { cleanup.forEach(fn => fn()); scope.stop(); } };
}

{
  const h = harness(); await h.open(); const e = h.editor;
  assert.equal(e.loading.value, false);
  assert.equal(h.calls.filter(c => c.kind === 'get').length, 1, 'no eager full capability catalog');
  assert.equal(e.stateFields.value.length, 1);
  assert.equal(e.form.states[0].kind, '初始');
  Object.assign(e.form.states[0], { key: 'ready', name: '待机' }); e.form.field = 'status';
  e.addState(); Object.assign(e.form.states[1], { key: 'active', name: '运行', kind: '初始' });
  assert.equal(e.problem.value, '请选择一个初始状态');
  await e.save(); assert.equal(h.calls.filter(c => c.kind === 'save').length, 0);
  e.form.states[1].kind = '中间'; e.addTransition();
  Object.assign(e.form.transitions[0], { from: 'ready', to: 'active', by: '启用' });
  assert.equal(e.problem.value, '');
  const saving = e.save(); const call = h.latest('save');
  assert.equal(call.args.body.expectedVersion, 0); assert.equal(call.args.body.releaseLockVersion, 15);
  assert.equal(call.args.id, 'Device');
  call.resolve({ ver: 'v-new', version: 1 }); await saving; await settle();
  assert.equal(h.props.open, false); assert.deepEqual(h.versions, ['v-new']);
  assert.ok(h.events.some(([name]) => name === 'saved')); h.dispose();
}
{
  const original = { field: 'status', desc: '', version: 1,
    states: [state('ready', '初始'), state('active', '中间')],
    transitions: [{ id: 'T1', from: 'ready', to: 'active', trigger: '人工', by: '启用', guard: '', effect: '', weekly: 0 }] };
  const h = harness(original); await h.open(); const e = h.editor;
  assert.equal(new Set(e.form.states.map(s => `${s.x}:${s.y}`)).size, 2, 'overlapping legacy coordinates are repaired');
  e.addState(); e.form.states[2].key = 'ready';
  assert.equal(e.originalStates.value.has(e.form.states[2]), false, 'a duplicate value must remain editable');
  e.removeState(2); assert.equal(e.form.transitions.length, 1, 'removing a duplicate draft row preserves real transitions');
  e.form.states[1].name = '运行中'; assert.equal(original.states[1].name, 'active', 'editing never mutates the loaded source');
  h.props.readonly = true; await e.save(); assert.equal(h.calls.filter(c => c.kind === 'save').length, 0);
  h.props.readonly = false;
  let pending = e.save(); h.latest('save').reject(new Error('VERSION_CONFLICT')); await pending;
  assert.equal(e.saveError.value, 'VERSION_CONFLICT'); assert.equal(h.props.open, true); assert.equal(e.form.states[1].name, '运行中');
  pending = e.save(); const stale = h.latest('save');
  h.context.ontologyId = '9'; await settle();
  stale.resolve({ ver: 'wrong-workspace', version: 2 }); await pending;
  assert.deepEqual(h.versions, []); assert.ok(!h.events.some(([name]) => name === 'saved'));
  h.dispose();
}
{
  const h = harness(); await h.open(); const e = h.editor;
  Object.assign(e.form.states[0], { key: 'ready', name: '待机' }); e.form.field = 'status';
  e.addState(); Object.assign(e.form.states[1], { key: 'done', name: '完成', kind: '终态' }); e.addTransition();
  Object.assign(e.form.transitions[0], { from: 'done', to: 'ready', by: '错误迁移' });
  assert.equal(e.problem.value, '终态不能设置离开的迁移');
  e.removeState(1); assert.equal(e.form.transitions.length, 0);
  h.dispose();
}
{
  const h = harness(); await h.open(); const e = h.editor;
  e.addTransition(); e.form.transitions[0].trigger = '事件'; await settle();
  const catalog = h.latest('get'); assert.equal(catalog.args.path, '/meta/capabilities');
  catalog.resolve({ events: [{ id: 'E1', name: '就绪事件', object: 'Device' }, { id: 'E2', name: '其他对象事件', object: 'Other' }] });
  await settle(); assert.equal(e.catalog.value.events.length, 1);
  e.form.transitions[0].eventId = 'E1'; e.selectReference(e.form.transitions[0], 'E1');
  assert.equal(e.form.transitions[0].by, '就绪事件'); h.dispose();
}
console.log('Lifecycle editor: SFC, create/edit, validation, readonly, conflict and stale-workspace checks passed.');
