// Runs the actual Vue setup with in-memory responses, without backend tests or source writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { computed, effectScope, nextTick, reactive, ref, watch } from 'vue';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';

const filename = 'src/pages/data/ExternalReadBindingModal.vue';
const { descriptor, errors } = parse(readFileSync(filename, 'utf8'), { filename });
assert.deepEqual(errors, []);
const compiled = compileScript(descriptor, { id: 'external-binding-check' });
assert.deepEqual(compileTemplate({ filename, id: 'external-binding-check', source: descriptor.template.content,
  compilerOptions: { bindingMetadata: compiled.bindings } }).errors, []);
const tree = ts.createSourceFile(filename, descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true);
const source = ts.transpileModule(tree.statements.filter(s => !ts.isImportDeclaration(s)).map(s => s.getText(tree)).join('\n'),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const settle = async () => { await nextTick(); await Promise.resolve(); await nextTick(); };

function harness({ kind = 'table', enabled = false, version = 4 } = {}) {
  const props = reactive({ objectId: 'Device' }), open = ref(false), readonly = ref(false);
  const context = reactive({ ontologyId: '8', ver: '' }), calls = [], events = [], scope = effectScope();
  const model = { version, datasourceId: 'DS-001', schema: 'MixedCase', table: 'DeviceRows',
    availableColumns: ['ID', 'Score'], requiredColumn: 'ID', readColumns: ['ID', 'Score'],
    sourceKind: kind, declaredUniqueKey: kind === 'view' ? 'ID' : null };
  const sandbox = {
    computed, ref, watch, JSON, Error,
    defineProps: () => props, defineModel: () => open,
    defineEmits: () => name => events.push(['emit', name]), currentApiContext: () => context,
    useReadonly: () => ({ readonly }), getObjectMirror: async () => ({ complete: true }),
    getObjectCdc: async () => ({ enabled }),
    apiClient: () => ({ get: async () => structuredClone(model),
      put: (path, body) => new Promise((resolve, reject) => calls.push({ path, body, resolve, reject })) }),
    message: { success: text => events.push(['success', text]), info: text => events.push(['info', text]) },
  };
  vm.createContext(sandbox);
  scope.run(() => vm.runInContext(source + '\nthis.modal = { binding, columns, valid, hasChanges, error, busy, save };', sandbox));
  return { ...sandbox.modal, open, props, readonly, context, calls, events,
    async start() { open.value = true; await settle(); }, dispose() { scope.stop(); } };
}

for (const kind of ['table', 'view']) {
  const h = harness({ kind, enabled: kind === 'table' }); await h.start();
  assert.equal(h.valid.value, true, 'unchanged grant can be checked while CDC is active');
  h.columns.value.reverse(); assert.equal(h.hasChanges.value, false, 'order is not a grant change');
  const saving = h.save(); assert.equal(h.calls.length, 1, 'all saves reach the shared server contract');
  assert.equal(h.calls[0].body.version, 4);
  if (kind === 'view') assert.equal(h.calls[0].body.declaredUniqueKey, 'ID');
  h.calls[0].resolve({ changed: false }); await saving;
  assert.deepEqual(h.events, [['info', '读取授权未变化']]); assert.equal(h.open.value, false);
  h.dispose();
}
{
  const h = harness({ enabled: true }); await h.start();
  h.columns.value = ['ID']; assert.equal(h.hasChanges.value, true); assert.equal(h.valid.value, false);
  await h.save(); assert.equal(h.calls.length, 0, 'active CDC still blocks grant changes');
  h.dispose();
}
{
  const h = harness(); await h.start(); h.columns.value = ['ID'];
  const saving = h.save(); h.calls[0].resolve({ changed: true }); await saving;
  assert.deepEqual(h.events.map(e => e[0]), ['success', 'emit']); assert.equal(h.open.value, false);
  h.dispose();
}
{
  const h = harness(); await h.start(); const saving = h.save();
  h.calls[0].reject(new Error('绑定版本冲突')); await saving;
  assert.equal(h.open.value, true); assert.equal(h.error.value, '绑定版本冲突');
  assert.deepEqual([...h.columns.value], ['ID', 'Score']); assert.equal(h.busy.value, false);
  h.dispose();
}
{
  const h = harness(); await h.start(); const saving = h.save();
  h.context.ontologyId = '9'; await settle(); h.calls[0].resolve({ changed: true }); await saving;
  assert.deepEqual(h.events, []); assert.equal(h.open.value, true, 'old response cannot close a new workspace form');
  h.dispose();
}
{
  const h = harness(); await h.start(); h.readonly.value = true;
  await h.save(); assert.equal(h.calls.length, 0); h.dispose();
}
console.log('external binding save: table/view no-op, active CDC, real change, conflict, workspace switch and readonly passed');
