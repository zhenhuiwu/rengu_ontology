/** Actual Roles loaders with Vue/request spies plus isolated Python function checks; no HTTP/DB. */
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import vm from 'node:vm';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';
import ts from 'typescript';
import { computed, effectScope, nextTick, reactive, ref, watch } from 'vue';

const filename = 'src/pages/admin/Roles.vue';
const parsed = parse(readFileSync(filename, 'utf8'), { filename });
assert.deepEqual(parsed.errors, []);
const compiled = compileScript(parsed.descriptor, { id: 'roles' });
assert.deepEqual(compileTemplate({ filename, id: 'roles', source: parsed.descriptor.template.content,
  compilerOptions: { bindingMetadata: compiled.bindings } }).errors, []);
const tree = ts.createSourceFile(filename, parsed.descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const variable = name => tree.statements.find(node => ts.isVariableStatement(node)
  && node.declarationList.declarations.some(d => d.name.getText(tree) === name));
const fn = name => tree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name);
const call = (name, arg) => tree.statements.find(node => ts.isExpressionStatement(node)
  && ts.isCallExpression(node.expression) && node.expression.expression.getText(tree) === name
  && node.expression.arguments[0]?.getText(tree).includes(arg));
const requests = [], cleanup = [], context = reactive({ ontologyId: 8, ver: '' });
const read = (kind, values) => new Promise((resolve, reject) => requests.push({ kind, values, resolve, reject }));
const scope = effectScope();
let posts = 0;
const state = {
  Error, computed, ref, watch, realRoles: true, remote: ref(true), loaded: ref(false),
  currentApiContext: () => context, tab: ref('func'), route: reactive({ query: {} }),
  q: key => state.route.query[key] ?? '', isApiError: () => false,
  roles: ref([]), members: ref({}), allUsers: ref([]), permObjects: ref([]), scopeProps: ref([]),
  loading: ref(false), loadError: ref(''), scopeValueSeq: 0,
  scopeValues: ref([]), scopeValuesError: ref(''), scopeValuesLoading: ref(false), scopeValuesMore: ref(false),
  scopeForm: reactive({ key: 'Fixture.region', values: ['West'] }), locked: ref(false), lockReason: ref(''),
  role: ref({ id: 'R-1', scopes: [] }),
  loadRolesState: () => read('state'), loadRolesMeta: (withScopeValues = true) => read('meta', withScopeValues),
  relationPaths: () => read('paths'), addRoleScope: async () => { ++posts; return { id: 'S-1' }; },
  setQuery: () => {}, message: { warning: () => {}, error: () => {}, success: () => {} },
  onBeforeUnmount: callback => cleanup.push(callback),
};
state.scopeProp = computed(() => state.scopeProps.value[0]);
vm.createContext(state);
const nodes = [variable('scopeMetaLoading'), variable('scopeMetaReady'), variable('scopeMetaSeq'),
  fn('resetScopeMeta'), fn('loadScopeMeta'), variable('reloadSeq'), fn('reload'), fn('call'), fn('addScope'),
  variable('scopeMetaNeeded'), call('watch', '[loaded, scopeMetaNeeded]'), call('onBeforeUnmount', 'disposed = true')];
assert.ok(nodes.every(Boolean));
scope.run(() => vm.runInContext(ts.transpileModule(nodes.map(node => node.getText(tree)).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.None },
}).outputText, state));
const invoke = expression => vm.runInContext(expression, state);
const settle = async () => { await nextTick(); await Promise.resolve(); await nextTick(); };
const meta = () => ({ permObjects: [{ name: 'Fixture', api: 'Fixture', actions: [] }],
  scopeProps: [{ objType: 'Fixture', objectTypeCode: 'Fixture', prop: 'region', values: ['West'] }] });
function base(at, withScopeValues = false) {
  assert.equal(requests[at].kind, 'state');
  assert.equal(requests[at + 1].kind, 'meta');
  assert.equal(requests[at + 1].values, withScopeValues);
  requests[at].resolve({ roles: [{ id: 'R-1' }], members: {}, users: [] });
  if (withScopeValues) full(at + 1);
  else requests[at + 1].resolve({ ...meta(), scopeProps: [] });
}
function full(at) {
  assert.equal(requests[at].kind, 'meta');
  assert.equal(requests[at].values, true);
  assert.equal(requests[at + 1].kind, 'paths');
  requests[at].resolve(meta()); requests[at + 1].resolve({ items: [] });
}
const initial = invoke('reload()');
assert.equal(requests.length, 2, 'default func view reads only roles and thin metadata');
base(0); await initial; await settle();
assert.equal(state.loaded.value, true);
state.tab.value = 'obj'; await settle();
assert.equal(requests.length, 2, 'object view does not read scope values or paths');
state.tab.value = 'row'; await settle();
assert.equal(requests.length, 4);
assert.equal(invoke('scopeMetaReady.value'), false);
await invoke('addScope()'); assert.equal(posts, 0, 'loading scope metadata blocks submission');
requests[2].reject(new Error('Scope values denied')); requests[3].resolve({ items: [] });
await settle();
assert.match(invoke('scopeMetaError.value'), /Scope values denied/);
assert.equal(invoke('scopeMetaReady.value'), false);
assert.equal(state.scopeProps.value.length, 0);
await invoke('addScope()'); assert.equal(posts, 0, 'failure cannot become an empty successful scope catalog');
const retry = invoke('loadScopeMeta()');
await invoke('loadScopeMeta()');
assert.equal(requests.length, 6, 'concurrent scope loads are deduplicated');
full(4); await retry; await settle();
assert.equal(invoke('scopeMetaReady.value'), true);
state.tab.value = 'func'; await settle();
state.tab.value = 'obj'; state.route.query.form = 'scope'; await settle();
assert.equal(requests.length, 6, 'same-context loaded scope data is reused by drawer');

let at = requests.length;
const written = invoke('call(async () => true)'); await settle();
assert.equal(requests.length, at + 3, 'write in scope drawer refreshes roles, full metadata and paths directly');
assert.equal(invoke('scopeMetaReady.value'), false);
base(at, true); await written; await settle();
assert.equal(requests.length, at + 3, 'scope write refresh does not also read thin metadata');
assert.equal(invoke('scopeMetaReady.value'), true);

for (const [tab, form] of [['row', undefined], ['func', 'scope']]) {
  state.tab.value = tab; state.route.query.form = form; await settle();
  at = requests.length;
  const refresh = invoke('reload()');
  assert.equal(requests.length, at + 3, `direct ${tab}/${form ?? ''} reads state/full/paths`);
  assert.equal(invoke('scopeMetaReady.value'), false);
  base(at, true); await refresh; await settle();
  assert.equal(requests.length, at + 3, 'direct scope refresh performs only one full metadata read');
  assert.equal(invoke('scopeMetaReady.value'), true);
}

// Intent may change while the initial thin request is already in flight.
state.tab.value = 'func'; state.route.query.form = undefined; await settle();
at = requests.length;
const changedTab = invoke('reload()');
state.tab.value = 'row'; await settle();
assert.equal(requests.length, at + 2, 'scope read waits until the initial role state is ready');
base(at); await settle();
assert.equal(requests.length, at + 4, 'func-to-row during loading adds one necessary full read and paths');
full(at + 2); await changedTab; await settle();
assert.equal(requests.length, at + 4);
assert.equal(invoke('scopeMetaReady.value'), true);

state.tab.value = 'func'; await settle();
at = requests.length;
const changedDrawer = invoke('reload()');
state.route.query.form = 'scope'; await settle();
base(at); await settle();
requests[at + 2].reject(new Error('Late scope load denied'));
requests[at + 3].resolve({ items: [] });
await changedDrawer; await settle();
assert.equal(state.loaded.value, true, 'thin role state remains usable');
assert.equal(invoke('scopeMetaReady.value'), false);
assert.match(invoke('scopeMetaError.value'), /Late scope load denied/);
await invoke('addScope()'); assert.equal(posts, 0, 'mid-load drawer failure cannot enable a write');

// Direct full-metadata failure must not be accepted as an empty ready scope.
at = requests.length;
const failed = invoke('reload()').catch(error => error);
requests[at].resolve({ roles: [{ id: 'R-1' }], members: {}, users: [] });
requests[at + 1].reject(new Error('Direct scope denied'));
requests[at + 2].resolve({ items: [] });
assert.match(String(await failed), /Direct scope denied/);
await settle();
assert.equal(state.loaded.value, false);
assert.equal(invoke('scopeMetaReady.value'), false);
assert.match(state.loadError.value, /Direct scope denied/);
await invoke('addScope()'); assert.equal(posts, 0);
at = requests.length;
const recovered = invoke('reload()'); base(at, true); await recovered; await settle();
assert.equal(invoke('scopeMetaReady.value'), true);

at = requests.length;
invoke('resetScopeMeta(); void loadScopeMeta()');
assert.equal(requests.length, at + 2);
context.ontologyId = 17; context.ver = 'v2';
const current = invoke('reload()');
assert.equal(requests.length, at + 5);
full(at); await settle();
assert.equal(invoke('scopeMetaReady.value'), false, 'late prior context/version cannot ready the new scope');
assert.equal(state.scopeProps.value.length, 0);
base(at + 2, true); await current; await settle();
assert.equal(requests.length, at + 5);
assert.equal(invoke('scopeMetaReady.value'), true);
at = requests.length;
invoke('resetScopeMeta(); void loadScopeMeta()');
cleanup.forEach(fn => fn()); scope.stop();
full(at); await settle();
assert.equal(invoke('scopeMetaReady.value'), false, 'unmounted response is ignored');
assert.equal(state.scopeProps.value.length, 0);

// Execute the real client wrapper to retain default-true compatibility and explicit false query.
const apiTree = ts.createSourceFile('roles.ts', readFileSync('src/api/roles.ts', 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const apiFn = apiTree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'loadRolesMeta');
const queries = [], client = vm.createContext({ api: { isReal: () => true, get: async (path, options) => { queries.push({ path, options }); return {}; } } });
vm.runInContext(ts.transpileModule(apiFn.getText(apiTree).replace(/^export /, ''), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.None },
}).outputText, client);
await vm.runInContext('loadRolesMeta()', client);
await vm.runInContext('loadRolesMeta(false)', client);
assert.equal(queries[0].options.query.withScopeValues, true);
assert.equal(queries[1].options.query.withScopeValues, false);
assert.ok(queries.every(query => query.path === '/roles/meta'));

const python = process.platform === 'win32' ? '../backend/.venv/Scripts/python.exe' : '../backend/.venv/bin/python';
execFileSync(existsSync(python) ? resolve(python) : process.platform === 'win32' ? 'python' : 'python3', ['-c', String.raw`
import ast
import asyncio
from inspect import signature
from pathlib import Path
from typing import Annotated, Any, get_args
from unittest.mock import AsyncMock
from fastapi import Query

def extract(path, name, namespace):
    tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), namespace)
    return namespace[name]

async def check():
    po = [{"name": "Fixture", "actions": []}]
    values = AsyncMock(return_value=[{"values": ["West"]}])
    ns = {"AsyncSession": object, "CurrentUser": object, "Any": Any,
          "load_perm_objects": AsyncMock(return_value=po), "func_pages": lambda: [], "load_scope_props": values}
    meta_fn = extract("app/modules/admin/roles.py", "roles_meta", ns)
    session, user = object(), object()
    thin = await meta_fn(session, user, with_scope_values=False)
    assert thin["perm_objects"] == po and thin["scope_props"] == []
    values.assert_not_awaited()
    assert (await meta_fn(session, user))["scope_props"] == [{"values": ["West"]}]
    assert (await meta_fn(session, user, True))["scope_props"] == [{"values": ["West"]}]
    assert values.await_count == 2
    values.side_effect = RuntimeError("scope denied")
    try:
        await meta_fn(session, user)
    except RuntimeError as error:
        assert str(error) == "scope denied"
    else:
        raise AssertionError("scope failure must propagate")
    class Out:
        model_validate = staticmethod(lambda value: value)
    handler_ns = {"SessionDep": object, "CurrentUserDep": object, "Annotated": Annotated,
                  "Query": Query, "RolesMetaOut": Out, "roles_meta": AsyncMock(return_value=thin)}
    handler = extract("app/modules/admin/router.py", "get_roles_meta", handler_ns)
    parameter = signature(handler).parameters["with_scope_values"]
    assert parameter.default is True and get_args(parameter.annotation)[1].alias == "withScopeValues"
    await handler(session, user, False)
    handler_ns["roles_meta"].assert_awaited_once_with(session, user, with_scope_values=False)
    print("Backend role metadata default/full/thin/error/route checks passed.")

asyncio.run(check())
`], { cwd: resolve('../backend'), stdio: 'inherit' });
console.log('Roles metadata passed: func/obj thin, direct row/drawer single full read, mid-load tab/drawer changes, failure/retry/submission/write/context/unmount guards, default client, SFC compile.');
