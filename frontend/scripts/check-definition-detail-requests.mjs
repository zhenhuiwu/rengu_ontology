/** Execute the four pages' actual detail watchers with Vue and deferred reads; no HTTP. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';
import ts from 'typescript';
import { computed, effectScope, nextTick, reactive, ref, watch } from 'vue';

const pages = [
  ['ObjectTypes', 'objectDetailKey', 'detailSeq'],
  ['LinkTypes', 'linkDetailKey', 'definitionSeq'],
  ['Metrics', 'metricDetailKey', 'detailSeq'],
  ['Dimensions', null, 'definitionSeq'],
];
const settle = async () => { await nextTick(); await Promise.resolve(); await nextTick(); };
function source(page) {
  const filename = `src/pages/ontology/${page}Body.vue`;
  const parsed = parse(readFileSync(filename, 'utf8'), { filename });
  assert.deepEqual(parsed.errors, []);
  const script = compileScript(parsed.descriptor, { id: page });
  const template = compileTemplate({ source: parsed.descriptor.template.content, filename, id: page,
    compilerOptions: { bindingMetadata: script.bindings } });
  assert.deepEqual(template.errors, [], `${page} SFC compiles`);
  const tree = ts.createSourceFile(filename, parsed.descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const nodes = [];
  const visit = node => { nodes.push(node); ts.forEachChild(node, visit); };
  visit(tree);
  const variable = name => nodes.find(node => ts.isVariableStatement(node)
    && node.declarationList.declarations.some(d => d.name.getText(tree) === name));
  const call = (name, predicate) => nodes.find(node => ts.isExpressionStatement(node)
    && ts.isCallExpression(node.expression) && node.expression.expression.getText(tree) === name
    && predicate(node.expression.arguments));
  return { tree, variable, call };
}
function run(state, tree, nodes) {
  assert.ok(nodes.every(Boolean), 'actual page statements found');
  vm.runInContext(ts.transpileModule(nodes.map(node => node.getText(tree)).join('\n'), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.None },
  }).outputText, state);
}
function response(id, name) {
  return { id, name, fields: { name, api: id }, properties: [], def: [], dims: [] };
}

for (const [page, keyName, sequence] of pages) {
  const { tree, variable, call } = source(page);
  const kind = { ObjectTypes: 'object', LinkTypes: 'link', Metrics: 'metric', Dimensions: 'dimension' }[page];
  const requests = [], cleanup = [], context = ref('workspace-8'), selected = ref('T-1');
  const scope = effectScope();
  const state = {
    Error, ref, watch, selected, selectedId: selected, staticContextKey: () => context.value,
    publishedCurrent: computed(() => ({ id: selected.value })), cur: computed(() => ({ id: selected.value })),
    app: reactive({ ontologyVersion: 'v1' }), route: reactive({ query: {} }),
    workflow: { loading: ref(true) }, draft: ref(undefined), newTarget: ref(false), draftOnly: ref(false),
    cr: { loading: ref(false), head: ref(undefined), error: ref('') }, isStaticReal: () => true,
    realDetail: ref({}), realProps: ref({}), OBJECT_PROPS: {}, TYPES: [], runtime: ref(null),
    baseline: ref([]), DEF: ref([]), DIMS: ref([]), VERSIONS: ref([]), REFS: ref([]), LIST: [], rawDefinition: ref({}),
    detailSeq: 0, definitionSeq: 0, runtimeSeq: 0, linksSeq: 0, catalogSeq: 0,
    detailError: ref(''), definitionError: ref(''), metricDetailError: ref(''),
    onBeforeUnmount: fn => cleanup.push(fn),
  };
  const read = path => new Promise((resolve, reject) => requests.push({ path, resolve, reject }));
  state.fetchStaticDetail = read;
  state.definitionDetail = (_kind, id) => read(`/object-types/${id}`);
  vm.createContext(state);
  const requestWatch = call('watch', args => args[0]?.getText(tree) === keyName
    || !keyName && args[1]?.getText(tree) === 'loadDefinition');
  scope.run(() => run(state, tree, [
    keyName ? variable(keyName) : tree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'loadDefinition'),
    requestWatch,
    call('onBeforeUnmount', args => args[0]?.getText(tree).includes(`++${sequence}`)),
  ]));
  const shown = () => page === 'ObjectTypes' ? state.realDetail.value.name
    : page === 'Metrics' ? state.rawDefinition.value.name : state.baseline.value.find(item => item.id === selected.value)?.name;
  assert.equal(requests.length, 1, `${page}: initial detail`);
  state.workflow.loading.value = false;
  await settle();
  assert.equal(requests.length, 1, `${page}: published workflow completion does not read again`);
  requests[0].resolve(response('T-1', 'Initial'));
  await settle();
  assert.equal(shown(), 'Initial');

  state.route.query.view = 'draft'; state.workflow.loading.value = true;
  await settle();
  const draftCount = requests.length;
  assert.equal(draftCount, page === 'Dimensions' ? 2 : 1, `${page}: existing draft-loading behavior`);
  state.workflow.loading.value = false;
  await settle();
  assert.equal(requests.length, 2, `${page}: draft transition retains necessary detail read`);
  state.draft.value = { id: 'CR-1' }; state.cr.loading.value = true;
  await settle();
  assert.equal(requests.length, 2, `${page}: wait for CR load`);
  state.cr.head.value = { targets: [{ kind, id: 'T-1', isNew: true }] };
  state.newTarget.value = true; state.draftOnly.value = true; state.cr.loading.value = false;
  await settle();
  assert.equal(requests.length, 2, `${page}: new draft must not read nonexistent published detail`);
  state.cr.head.value = { targets: [{ kind, id: 'T-1', isNew: false }] };
  state.newTarget.value = false; state.draftOnly.value = false;
  await settle();
  assert.equal(requests.length, 3, `${page}: existing CR target loads`);
  state.cr.error.value = 'CR load failed';
  await settle();
  assert.equal(requests.length, 3, `${page}: CR error blocks detail read`);
  state.cr.error.value = '';
  await settle();
  assert.equal(requests.length, 4, `${page}: CR recovery reloads`);

  context.value = 'workspace-17';
  await settle();
  assert.equal(requests.length, 5, `${page}: context change reads current detail`);
  for (const request of requests.slice(1, 4)) request.resolve(response('T-1', 'Late old context'));
  await settle();
  assert.notEqual(shown(), 'Late old context', `${page}: late result cannot replace current context`);
  requests[4].reject(new Error('Current detail failed'));
  await settle();
  const error = page === 'ObjectTypes' ? state.detailError : page === 'Metrics' ? state.metricDetailError : state.definitionError;
  assert.equal(error.value, 'Current detail failed', `${page}: genuine read errors remain visible`);
  state.cr.loading.value = true;
  await settle();
  state.cr.loading.value = false;
  await settle();
  assert.equal(requests.length, 6, `${page}: CR refresh still reads after writes/reloads`);
  requests[5].resolve(response('T-1', 'Current'));
  await settle();
  assert.equal(shown(), 'Current');
  selected.value = 'T-2';
  await settle();
  assert.equal(requests.length, 7, `${page}: selection change`);
  cleanup.forEach(fn => fn()); scope.stop();
  requests[6].resolve(response('T-2', 'Late unmounted'));
  await settle();
  assert.notEqual(shown(), 'Late unmounted', `${page}: unmount rejects late detail`);
}

// Published catalog is global; only a draft adds selected link/version to the actual request.
const { tree, variable, call } = source('LinkTypes');
const requests = [], context = ref('workspace-8'), scope = effectScope();
const state = {
  watch, staticContextKey: () => context.value, selectedId: ref(''), isDraft: ref(false),
  cr: { head: ref(undefined) }, catalogSeq: 0, catalogLoading: ref(false), catalogError: ref(''), catalog: ref({}),
  isStaticReal: () => true,
  fetchStaticDetail: (path, query) => new Promise(resolve => requests.push({ path, query, resolve })),
};
vm.createContext(state);
scope.run(() => run(state, tree, [variable('catalogKey'), tree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'loadCatalog'),
  call('watch', args => args[0]?.getText(tree) === 'catalogKey')]));
assert.equal(requests.length, 1);
state.selectedId.value = 'L-1'; await settle();
state.selectedId.value = 'L-2'; await settle();
assert.equal(requests.length, 1, 'published initial/changed selection reuses the same global catalog read');
state.isDraft.value = true;
state.cr.head.value = { id: 'CR-1', lockVersion: 1, targets: [{ kind: 'link', id: 'L-2' }] };
await settle();
assert.equal(requests.length, 2);
assert.equal(requests[1].query.crId, 'CR-1');
assert.equal(requests[1].query.linkId, 'L-2');
state.cr.head.value.lockVersion = 2; await settle();
assert.equal(requests.length, 3, 'draft revision reloads catalog');
context.value = 'workspace-17'; await settle();
assert.equal(requests.length, 4);
for (const request of requests.slice(0, 3)) request.resolve({ marker: 'Old catalog' });
await settle();
assert.notEqual(state.catalog.value.marker, 'Old catalog');
requests[3].resolve({ marker: 'Current catalog' }); await settle();
assert.equal(state.catalog.value.marker, 'Current catalog');
scope.stop();
console.log('Definition detail requests passed: four actual watchers, published workflow dedupe, draft/new/error/context/unmount guards, catalog scope; four SFCs compile.');
