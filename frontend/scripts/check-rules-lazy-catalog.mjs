/** Execute the actual Rules.vue watchers with deferred request spies; no HTTP/DB/browser. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { computed, nextTick, reactive, ref, watch } from 'vue';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';

const filename = new URL('../src/pages/ontology/Rules.vue', import.meta.url);
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename: filename.pathname });
assert.equal(errors.length, 0);
const syntax = ts.createSourceFile('Rules.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true);
const selected = syntax.statements.filter(node =>
  ts.isFunctionDeclaration(node) && ['needsActionCatalog', 'watchRuleCatalog'].includes(node.name?.text)
  || ts.isIfStatement(node) && (node.getText().includes('loadStatic(') || node.getText().includes('await loadActionParams(')));
assert.equal(selected.length, 4);
const context = ref('workspace8'), history = ref('');
const catalogs = [], parameters = [], staticCalls = [], pendingCatalog = [], pendingParams = [];
const sandbox = {
  ref, watch, computed, REAL_API: true, mounted: true,
  catalogSequence: 0, parameterSequence: 0,
  actionApi: { isReal: () => true }, isStaticReal: () => true,
  actionContextKey: () => context.value, dynamicContextKey: () => context.value,
  currentApiContext: () => ({ ver: history.value }),
  tab: ref('cond'), rule: ref({ trigger: { kind: '定时' } }), isDraft: ref(false),
  stageEdit: ref({ enabled: false }), newOpen: ref(false), assistantNew: ref(null), assistantPending: ref(null),
  catalogKey: ref(''), catTick: ref(0), paramsKey: ref(''), stageType: ref({ id: 'A', version: 'v1' }),
  loadSnapshot: () => { catalogs.push(context.value); return new Promise(resolve => pendingCatalog.push(resolve)); },
  loadActionParams: id => { parameters.push([context.value, id]); return new Promise(resolve => pendingParams.push(resolve)); },
  loadStatic: sets => { staticCalls.push([context.value, [...sets]]); return Promise.resolve(); },
  fail: error => { throw error; }, fallBackIfDown: () => false, console,
};
sandbox.actionCatalogReady = computed(() => sandbox.catalogKey.value === context.value);
vm.createContext(sandbox);
const functions = selected.filter(ts.isFunctionDeclaration).map(node => node.getText()).join('\n');
const watchers = selected.filter(ts.isIfStatement).map(node => node.getText()).join('\n');
const evaluate = text => vm.runInContext(ts.transpileModule(text, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText, sandbox);
evaluate(functions);
evaluate(watchers);
const stop = sandbox.watchRuleCatalog();
const settle = async () => { await nextTick(); await Promise.resolve(); await nextTick(); };
await settle();
assert.equal(catalogs.length, 0);
assert.equal(parameters.length, 0);
assert.equal(staticCalls.length, 0);
sandbox.newOpen.value = true;
await settle();
assert.equal(catalogs.length, 0); // Ordinary new-rule object choices already come from rules/meta.
sandbox.assistantNew.value = {};
await settle();
assert.equal(catalogs.length, 1);
context.value = 'workspace9';
await settle();
assert.equal(catalogs.length, 2);
pendingCatalog.shift()();
await settle();
assert.equal(sandbox.catalogKey.value, '');
assert.equal(parameters.length, 0);
pendingCatalog.shift()();
await settle();
assert.equal(sandbox.catalogKey.value, 'workspace9');
assert.equal(parameters.length, 1);
sandbox.stageType.value = { id: 'A', version: 'v2' };
await settle();
assert.equal(parameters.length, 2);
pendingParams.shift()();
await settle();
assert.equal(sandbox.paramsKey.value, ''); // Old version cannot release the save guard.
pendingParams.shift()();
await settle();
assert.equal(sandbox.paramsKey.value, 'workspace9:A:v2');
sandbox.newOpen.value = false; sandbox.assistantNew.value = null;
sandbox.tab.value = 'trigger'; sandbox.rule.value.trigger.kind = '定时';
await settle();
assert.equal(catalogs.length, 2);
sandbox.rule.value.trigger.kind = '事件';
await settle();
assert.equal(catalogs.length, 3);
pendingCatalog.shift()(); await settle();
sandbox.tab.value = 'cond'; sandbox.isDraft.value = true; sandbox.stageEdit.value.enabled = true;
await settle();
assert.equal(sandbox.needsActionCatalog(), true);
history.value = 'v1'; context.value = 'workspace9@v1';
await settle();
assert.equal(staticCalls.length, 1);
assert.equal(staticCalls[0][0], 'workspace9@v1');
stop();

// Execute the actual overview/tab loaders and guard, including publication refresh and late failures.
const loaderNames = new Set(['guard', 'loadOverview', 'ruleTabDataKey', 'loadStaged', 'loadVersions']);
const loaderNodes = syntax.statements.filter(node =>
  ts.isVariableStatement(node) && node.declarationList.declarations.some(d => loaderNames.has(d.name.getText()))
  || ts.isFunctionDeclaration(node) && node.name?.text === 'watchRuleTabData');
assert.equal(loaderNodes.length, 6);
const scope = ref('workspace8'), id = ref('R1'), activeTab = ref('cond');
const actionReads = [], versionReads = [], hitReads = [], failures = [];
const tabRemote = reactive({ detail: null, hits7: 0, recent: [], staged: [], versions: [] });
const loaderSandbox = { watch, mounted: true, realId: id, tab: activeTab, remote: tabRemote,
  dynamicContextKey: () => scope.value, readFail: error => failures.push(error),
  listRuleHits: async (...args) => { hitReads.push(args); return { total: 4, items: ['recent'] }; },
  listRuleActionRuns: rule => new Promise((resolve, reject) => actionReads.push({ scope: scope.value, rule, resolve, reject })),
  listRuleVersions: rule => new Promise((resolve, reject) => versionReads.push({ scope: scope.value, rule, resolve, reject })),
};
vm.createContext(loaderSandbox);
vm.runInContext(ts.transpileModule(loaderNodes.map(node => node.getText()).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText, loaderSandbox);
const stopTabs = loaderSandbox.watchRuleTabData();
await vm.runInContext('loadOverview()', loaderSandbox);
assert.equal(hitReads.length, 2);
assert.equal(actionReads.length + versionReads.length, 0);
assert.equal(tabRemote.hits7, 4);
tabRemote.detail = { id: 'R1', version: 1, draft: null, displayStatus: '已发布', cr: null };
await settle();
assert.equal(actionReads.length + versionReads.length, 0);
activeTab.value = 'action';
await settle();
assert.equal(actionReads.length, 1);
assert.equal(versionReads.length, 0);
tabRemote.detail = { ...tabRemote.detail, version: 2 };
await settle();
assert.equal(actionReads.length, 2); // Publishing while this tab is open reloads its own data.
actionReads[0].resolve({ items: ['old-version'] });
await settle();
assert.equal(tabRemote.staged.length, 0);
actionReads[1].resolve({ items: ['current-version'] });
await settle();
assert.equal(tabRemote.staged[0], 'current-version');
activeTab.value = 'versions';
await settle();
assert.equal(versionReads.length, 1);
tabRemote.detail = { ...tabRemote.detail, version: 3 };
await settle();
assert.equal(versionReads.length, 2);
versionReads[1].reject(new Error('active read failed'));
await settle();
assert.equal(failures.length, 1); // Current failures remain visible through the page error handler.
tabRemote.detail = null; scope.value = 'workspace9'; id.value = 'R2';
tabRemote.detail = { id: 'R2', version: 1, draft: null, displayStatus: '已发布', cr: null };
await settle();
assert.equal(versionReads.at(-1).scope, 'workspace9');
assert.equal(versionReads.at(-1).rule, 'R2');
versionReads[0].resolve(['old-rule']);
await settle();
assert.equal(tabRemote.versions.length, 0);
versionReads.at(-1).resolve(['current-rule']);
await settle();
assert.equal(tabRemote.versions[0], 'current-rule');
stopTabs();
const script = compileScript(descriptor, { id: 'rules-lazy' });
const template = compileTemplate({ source: descriptor.template.content, filename: filename.pathname,
  id: 'rules-lazy', compilerOptions: { bindingMetadata: script.bindings } });
assert.equal(template.errors.length, 0);
console.log('Rules lazy catalogs/tab reads/SFC passed: overview hits only; demand, write refresh, errors, scope, version and history preserved.');
