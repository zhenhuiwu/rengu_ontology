// Run: node scripts/check-static-draft-hydration.mjs. Actual loader + Body overlay callbacks; no DB writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const read = (p) => readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8');
function execute(source, globals) {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {}; runInNewContext(outputText, { exports, structuredClone, ...globals }); return exports;
}
const ctx = vue.reactive({ demoAs: 'A', previewAs: '', ver: '' });
const real = vue.ref(true), draft = vue.ref({ id: 'CR-1' }), tid = vue.ref('T');
const deferred = () => { let resolve; const promise = new Promise((r) => resolve = r); return { promise, resolve }; };
const slow = deferred(), calls = [], registered = [], errors = [];
let deny = false, conflict = false;
const row = (section, key, after, op = 'update') => ({ section, key, after, op, target: { kind: 'link', id: 'T' }, updatedAt: 'now' });
const api = {
  isStaticReal: () => real.value, staticContextKey: () => JSON.stringify(ctx), crErr: String,
  fetchCR: async (_id, options) => { calls.push(['head', options]); if (ctx.demoAs === 'A') return slow.promise; if (deny) throw Error('403'); return { id: 'CR-1', status:'草稿', changes: 7, lockVersion: 8, targets: [] }; },
  fetchCRItems: async (_id, _q, options) => { calls.push(['items', options]); return [row('def', 'name', ctx.demoAs)]; },
  registerDraft: (head) => registered.push(head.changes), crSave: async () => { if (conflict) throw Error('409 VERSION_CONFLICT'); },
  crStart: async (t, o) => o, crSubmit: async () => {}, crDiscard: async () => {},
};
const helper = execute(read('pages/ontology/parts/useStaticCr.ts'), { require: (name) => name === 'vue' ? vue : name === '@/api/http' ? { currentApiContext: () => ({ ...ctx }) } : name === 'ant-design-vue' ? { message: { error: (e) => errors.push(e), success() {} } } : api });
// Real Vue dependencies: rebuilding Body rows invalidates target(), but cannot emit a new empty CR collection.
const hydrationScope = vue.effectScope(), hydratedRows = vue.reactive([{ id: 'T', name: 'published' }]);
let emptyCr, rebuilds = 0;
hydrationScope.run(() => {
  emptyCr = helper.useStaticCr('link', () => ({ id: hydratedRows[0].id, name: hydratedRows[0].name }), { draft: vue.computed(() => undefined), start() {}, setView() {}, cancel() {} });
  vue.watch([emptyCr.items, emptyCr.head], () => {
    assert.ok(++rebuilds < 3, 'hydrating page rows must not recursively hydrate the same empty CR');
    hydratedRows.splice(0, hydratedRows.length, { id: 'T', name: 'published' });
  }, { flush: 'sync' });
});
const emptyIdentity = emptyCr.items.value;
hydratedRows.splice(0, hydratedRows.length, { id: 'T', name: 'published' });
await vue.nextTick();
assert.equal(emptyCr.items.value, emptyIdentity);
assert.equal(rebuilds, 0);
hydrationScope.stop();
const cr = helper.useStaticCr('link', () => ({ id: tid.value }), { draft, start() {}, setView() {}, cancel() {} });
ctx.demoAs = 'B'; await vue.nextTick(); await cr.reload();
assert.equal(cr.items.value[0].after, 'B'); assert.equal(registered.at(-1), 7);
slow.resolve({ id: 'CR-1', changes: 99 }); await Promise.resolve(); await vue.nextTick();
assert.equal(cr.items.value[0].after, 'B'); assert.ok(!registered.includes(99));
assert.equal(calls.filter(([kind, o]) => kind === 'items' && o.headers['X-Demo-As'] === 'A').length, 0);
ctx.demoAs = 'C'; assert.equal(cr.head.value, undefined, 'old identity hidden before watcher flush');
deny = true; await vue.nextTick(); await cr.reload(); assert.equal(cr.items.value.length, 0); assert.match(cr.error.value, /403/);
assert.equal(await cr.save('def', []), false, 'failed hydration cannot save an unknown baseline');
deny = false; await cr.reload(); conflict = true;
const input = { name: 'unsaved input' }; assert.equal(await cr.save('def', [{ key: 'name', after: input.name }]), false); assert.equal(input.name, 'unsaved input');
assert.equal(cr.items.value[0].after, 'C', '409 preserves loaded rows and caller input');
const base = [{ id: 'A', name: 'published' }, { id: 'B', name: 'remove' }];
const merged = helper.draftRows(base, [row('x', 'A', { name: 'updated' }), row('x', 'B', null, 'delete'), row('x', 'C', { name: 'added' }, 'add')], 'x', (r) => r.id, (r, previous) => ({ ...previous, ...r.after, id: r.key }));
assert.deepEqual(JSON.parse(JSON.stringify(merged)), [{ id: 'A', name: 'updated' }, { name: 'added', id: 'C' }]);
assert.equal(base[0].name, 'published');
const objectSource = read('pages/ontology/ObjectTypesBody.vue').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const objectAst = ts.createSourceFile('object.ts', objectSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const propsStatement = objectAst.statements.find((s) => ts.isVariableStatement(s) && s.declarationList.declarations[0].name.getText(objectAst) === 'PROPS');
const objectDraft = vue.ref(true), objectItems = vue.ref([row('properties', 'new_api', { api: 'new_api', name: 'new field' }, 'add')]);
const objects = execute(`${propsStatement.getText(objectAst)}; export { PROPS };`, { computed: vue.computed, isStaticReal: () => true, current: vue.ref({ id: 'T' }), isDraft: objectDraft, cr: { items: objectItems }, realProps: vue.ref({ T: [{ api: 'old_api', name: 'published field' }] }), OBJECT_PROPS: {}, ...helper });
assert.equal(objects.PROPS.value.length, 2); objectDraft.value = false; assert.equal(objects.PROPS.value.length, 1);

// Execute the real top-level overlay watcher from each Body against controlled published records.
function overlay(file, globals) {
  const script = read(`pages/ontology/${file}`).match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const ast = ts.createSourceFile(file, script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const statement = ast.statements.find((s) => ts.isExpressionStatement(s) && ts.isCallExpression(s.expression) && s.expression.expression.getText(ast) === 'watch' && s.expression.arguments[0]?.getText(ast) === '[baseline, cr.items, cr.head, isDraft, cr.loading]');
  assert.ok(statement, file);
  let callback; execute(statement.getText(ast), { watch: (_sources, fn) => callback = fn, isStaticReal: () => true, ...helper, ...globals }); return callback;
}
const draftRows = vue.ref([]), head = vue.ref({ id: 'CR-1', targets: [], changes: 2 }), isDraft = vue.ref(true);
const common = { cr: { items: draftRows, head, loading: vue.ref(false) }, isDraft, targetId: () => 'T', draft: vue.ref({ id: 'CR-1' }) };
const links = [{ id: 'T', name: 'published', card: 'N : 1', fromProp: 'from', toProp: 'to', to: 'O' }];
const linkList = structuredClone(links), linkRestore = overlay('LinkTypesBody.vue', { ...common, baseline: vue.shallowRef(links), links: linkList });
draftRows.value = [row('def', 'name', 'draft')]; linkRestore(); assert.equal(linkList[0].name, 'draft');
isDraft.value = false; linkRestore(); assert.equal(linkList[0].name, 'published'); assert.equal(links[0].name, 'published');
isDraft.value = true;
const entries = [{ id: 'T', name: 'term', status: '已发布' }], entryList = structuredClone(entries);
const knowledgeRestore = overlay('GlossaryBody.vue', { ...common, baseline: vue.shallowRef(entries), entries: entryList });
draftRows.value = [row('content', 'name', 'new term'), row('status', 'status', '已停用')]; knowledgeRestore();
assert.equal(entryList[0].draft.name, 'new term'); assert.equal(entryList[0].draftStatus, '已停用');
isDraft.value = false; knowledgeRestore(); assert.equal(entryList[0].draft, undefined);
isDraft.value = true;
const dimensions = [{ id: 'T', desc: 'published', qa: { note: 'old' }, values: [{ key: 'v1', name: 'old', code: 'v1', synonyms: [] }], mappings: [] }], dimensionList = structuredClone(dimensions);
const dimRestore = overlay('DimensionsBody.vue', { ...common, baseline: vue.shallowRef(dimensions), items: dimensionList });
draftRows.value = [row('qa', 'note', 'new'), row('values', 'v1', { name: 'changed', code: 'v1', synonyms: [] }), row('values', 'v2', { name: 'added', code: 'v2', synonyms: [] }, 'add'), row('mappings', 'p>v2', { phrase: 'p', targets: ['v2'] }, 'add')]; dimRestore();
assert.equal(dimensionList[0].qa.note, 'new'); assert.equal(dimensionList[0].values[0].pub.name, 'old'); assert.equal(dimensionList[0].values[1].diff, '新增'); assert.equal(dimensionList[0].mappings.length, 1);
isDraft.value = false; dimRestore(); assert.equal(dimensionList[0].qa.note, 'old'); assert.equal(dimensionList[0].values.length, 1); assert.equal(dimensionList[0].mappings.length, 0);
real.value = false; const before = calls.length; await cr.reload(); assert.equal(calls.length, before, 'mock hydration never calls backend');

function statements(file, names) {
  const source = read(`pages/ontology/${file}`).match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  return ast.statements.filter((s) => ts.isVariableStatement(s) && names.includes(s.declarationList.declarations[0].name.getText(ast)) || ts.isFunctionDeclaration(s) && names.includes(s.name?.text)).map((s) => s.getText(ast)).join('\n');
}
let realDisplay = true;
const hits = execute(`${statements('DimensionsBody.vue', ['hitsText'])}; export { hitsText };`, { isStaticReal: () => realDisplay, fmtInt: (n) => String(n) });
assert.equal(hits.hitsText({ auto: '', hits: 0 }), '—'); assert.equal(hits.hitsText({ hits: 51 }), '51');
// Real trial handlers execute saved definitions only; delayed contexts and 403 never reveal old rows.
let trialKey='old', trialDeny=false; const oldTrial=deferred(), tried=vue.ref(''), tryQ=vue.ref('input');
const trialGlobals={ ref:vue.ref, computed:vue.computed, isStaticReal:()=>true, tried, tryQ,
  knowledgeTrialKey:vue.computed(()=>trialKey), cur:vue.ref({id:'K-DB'}), isDraft:vue.ref(false), draft:vue.ref({id:'CR-1'}), cr:{head:vue.ref({lockVersion:1}),loading:vue.ref(false),error:vue.ref('')},
  resolveKnowledge:async()=>{if(trialDeny)throw Error('403');return trialKey==='old'?oldTrial.promise:{rows:[{k:'DB',v:'current'}]};} };
const knowledgeTry=execute(`${statements('GlossaryBody.vue',['trialRows','trialSeq','runTry','tryRows'])}; export {runTry,tryRows,trialError};`,trialGlobals);
assert.equal(knowledgeTry.tryRows.value.length,0,'real initial state has no demo result');
const oldRun=knowledgeTry.runTry();trialKey='new';await knowledgeTry.runTry();oldTrial.resolve({rows:[{k:'PRIVATE',v:'old'}]});await oldRun;
assert.equal(knowledgeTry.tryRows.value[0].v,'current');trialDeny=true;await knowledgeTry.runTry();assert.equal(knowledgeTry.tryRows.value.length,0);assert.match(knowledgeTry.trialError.value,/403/);
trialGlobals.isDraft.value=true;trialGlobals.cr.loading.value=true;await knowledgeTry.runTry();assert.match(knowledgeTry.trialError.value,/加载草稿/);

let dimKey='old', dimDeny=false;const oldDim=deferred();
const dimTrial=execute(`${statements('DimensionsBody.vue',['realTrial','trialSeq','loadTrial','tryResult'])}; export {loadTrial,tryResult,trialError};`,{
 ref:vue.ref,computed:vue.computed,trialKey:vue.computed(()=>dimKey),tryText:vue.ref('沪'),q:()=> '沪',isStaticReal:()=>true,
 cur:vue.ref({id:'DB-DIM'}),tryAll:vue.ref(true),isDraft:vue.ref(false),draft:vue.ref(null),cr:{head:vue.ref(null),loading:vue.ref(false),error:vue.ref('')},
 matchDimension:async()=>{if(dimDeny)throw Error('403');return dimKey==='old'?oldDim.promise:{exact:[{item:'DB-DIM',value:'current'}],contains:[],groups:[],more:0};}
});
const oldDimRun=dimTrial.loadTrial();dimKey='new';await dimTrial.loadTrial();oldDim.resolve({exact:[{value:'PRIVATE'}]});await oldDimRun;
assert.equal(dimTrial.tryResult.value.exact[0].value,'current');dimDeny=true;await dimTrial.loadTrial();assert.equal(dimTrial.tryResult.value,null);assert.match(dimTrial.trialError.value,/403/);
// HistoryTab's new real page/context coverage lives in check-object-usage-pages.mjs.
let catalogKey = 'old'; const oldCatalog = deferred();
const catalog = execute(`${statements('GlossaryBody.vue', ['realCandidates','candidateSeq','loadCandidates'])}; export { realCandidates,loadCandidates };`, { ref: vue.ref, staticContextKey: () => catalogKey, isStaticReal: () => true, fetchKnowledgeChoices: async () => catalogKey === 'old' ? oldCatalog.promise : [{ kind:'object-type', id:'VALID', name:'DB object' }] });
const oldCatalogLoad = catalog.loadCandidates(); catalogKey='new'; await catalog.loadCandidates(); oldCatalog.resolve([{kind:'object-type',id:'PRIVATE',name:'old user'}]); await oldCatalogLoad;
assert.equal(catalog.realCandidates.value[0].id,'VALID');
const persisted = { kind:'object-type', id:'INACTIVE', name:'Stored reference' }, stored = { id:'T',name:'Stored',type:'术语',definition:'',calc:'',synonyms:[],applies:[persisted],period:'',owner:'DB user' };
const form = vue.reactive({ ...stored, name:'Edited', applies:['object-type:INACTIVE','object-type:VALID'] }); const saved = [];
const refs = execute(`${statements('GlossaryBody.vue', ['APPLY_ALL','unavailableApplies','APPLY_OPTIONS','FIELD_LABEL','save'])}; export { APPLY_OPTIONS,unavailableApplies,save };`, { computed:vue.computed, isStaticReal:()=>true, realCandidates:catalog.realCandidates, view:vue.ref(stored), cur:vue.ref(stored), form, candidateLoading:vue.ref(false),candidateError:vue.ref(''), draftOnly:vue.ref(false), editing:vue.ref(true), draft:vue.ref({id:'CR-1'}), cr:{save:async (_section,rows)=>{saved.push(rows);return true;}}, message:{error(e){throw Error(e);},success(){}} });
assert.equal(refs.unavailableApplies.value[0].id,'INACTIVE'); assert.equal(refs.APPLY_OPTIONS.value.at(-1).options[0].disabled,true);
await refs.save(); assert.deepEqual(Array.from(saved[0].find((r)=>r.key==='applies').after,(r)=>r.id),['INACTIVE','VALID']);
form.applies=['object-type:VALID']; await refs.save(); assert.deepEqual(Array.from(saved[1].find((r)=>r.key==='applies').after,(r)=>r.id),['VALID']);

// Actual page-local CR workflow reader never merges identities or historical/current records.
real.value=true;ctx.demoAs='cr-old';ctx.ver='';const oldRecords=deferred();let recordCalls=0,recordDenied=false;
api.fetchWorkflowRecords=async(options)=>{recordCalls++;if(options.headers['X-Demo-As']==='cr-old')return oldRecords.promise;if(recordDenied)throw Error('403');return [{id:'CR-CURRENT',status:'审核中',detail:{touches:['knowledge:T']}}];};
const workflow=helper.useRealCrRecords();ctx.demoAs='cr-current';await vue.nextTick();await workflow.reload();oldRecords.resolve([{id:'PRIVATE-CR',status:'草稿',detail:{touches:['knowledge:T']}}]);await new Promise(setImmediate);assert.equal(workflow.rows.value[0].id,'CR-CURRENT');assert.equal(helper.activeTargetCr(workflow.rows.value,'knowledge','T').status,'审核中');
ctx.previewAs='other-user';assert.equal(workflow.rows.value.length,0,'workflow rows are hidden before context watcher flush');recordDenied=true;await vue.nextTick();await workflow.reload();assert.equal(workflow.rows.value.length,0);assert.match(workflow.error.value,/403/);ctx.ver='historical';await vue.nextTick();const reads=recordCalls;await workflow.reload();assert.equal(recordCalls,reads,'historical definitions never mix current workflow rows');
console.log('static draft hydration: delayed identity, errors, conflicts, actual Body reset/overlays passed');

// Existing metric rollback modal stays open on conflicts and only creates a definition CR.
let metricKey='ctx', rollbackDenied=true, rollbackCalls=0, refreshed=0;
const rollbackReadonly=vue.ref(false), rollbackErrors=[];
const metric=execute(`${statements('MetricsBody.vue',['rollbackPending','rollbackBusy','changeOpen','changeTarget','openChange','start','rollback'])}; export {rollbackPending,rollbackBusy,changeOpen,start,rollback};`,{
 ref:vue.ref,isStaticReal:()=>true,readonly:rollbackReadonly,staticContextKey:()=>metricKey,cur:vue.ref({id:'gmv-db',name:'DB metric',api:'gmv-db',version:'v2'}),
 cr:{start(){throw Error('must not use ordinary edit CR for rollback');}},confirm:{ask:async()=>true},VERSIONS:vue.ref([{status:'生效中',ver:'v2'}]),
 rollbackMetric:async(id,version,opts)=>{rollbackCalls++;assert.equal(id,'gmv-db');assert.equal(version,'v1');assert.equal(opts.desc,'keep input');if(rollbackDenied)throw Error('409');return {crId:'CR-DB',lockVersion:3};},
 message:{error:e=>rollbackErrors.push(e),success(){}},definitionSaved:async()=>refreshed++
});
await metric.rollback({ver:'v1',release:'db-release',canRollback:true});assert.equal(metric.changeOpen.value,true);
const rollbackInput={join:'CR-EXISTING',desc:'keep input'};await metric.start(rollbackInput);assert.equal(metric.changeOpen.value,true);assert.equal(metric.rollbackPending.value.ver,'v1');assert.equal(rollbackInput.join,'CR-EXISTING');assert.match(rollbackErrors[0],/409/);
rollbackDenied=false;await metric.start(rollbackInput);assert.equal(metric.changeOpen.value,false);assert.equal(refreshed,1);
rollbackReadonly.value=true;await metric.rollback({ver:'v1',release:'r',canRollback:true});assert.equal(rollbackCalls,2);
