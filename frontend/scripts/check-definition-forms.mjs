// Run: node scripts/check-definition-forms.mjs. Actual API/SFC modules, delayed reads and CR writes mocked.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const read = (p) => readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8');
function load(file, deps, names = '', globals = {}) {
  let source = read(file); if (file.endsWith('.vue')) source = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const { outputText } = ts.transpileModule(`${source}\n${names ? `export { ${names} };` : ''}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {}; runInNewContext(outputText, { exports, structuredClone, URLSearchParams, ...globals, require: (name) => name === 'vue' ? { ...vue, watch() {} } : name.endsWith('.vue') ? {} : name in deps ? deps[name] : (() => { throw Error(name); })() }); return exports;
}
const ctx = vue.reactive({ demoAs: 'real-user', previewAs: '', ver: '' });
const key = () => JSON.stringify(ctx);
const calls = [], writes = [], starts = [];
let conflict = false, delay = null;
const api = { isReal: () => true, get: async (path, options) => {
  calls.push([path, options]);
  if (delay && path === '/object-types') { const pending = delay; delay = null; return pending; }
  if (path === '/build/owners') return [{ id: 'U-1', name: 'DB User' }];
  if (path === '/object-types') { const page = options.query.page; return { items: Array.from({ length: page === 1 ? 200 : 1 }, (_, i) => ({ id: `O-${(page-1)*200+i}`, name: 'DB object', api: `object_${i}` })), total: 201 }; }
  return { items: [], total: 0 };
} };
const staticApi = { staticContextKey: key, crStart: async (target, opts) => { starts.push({ target, opts }); return { join: 'CR-DB' }; }, crSave: async (crId, target, section, rows) => { if (conflict) throw Error('409 VERSION_CONFLICT'); writes.push({ crId, target, section, rows }); }, fetchCR: async () => ({}), fetchCRItems: async () => [] };
const forms = load('api/definitionForms.ts', { './http': { apiClient: () => api, currentApiContext: () => ({ ...ctx }) }, './static': staticApi });
const choices = await forms.definitionChoices('object'); assert.equal(choices.objects.length, 201); assert.equal(choices.owners[0].id, 'U-1');
assert.ok(calls.every(([, options]) => options.headers['X-Demo-As'] === 'real-user'));
let finish; delay = new Promise((resolve) => finish = resolve); const old = forms.definitionChoices('object'); ctx.demoAs = 'other'; finish({ items: [{ id: 'PRIVATE' }], total: 1 }); await assert.rejects(old, /身份或版本/);
const values = { name: 'New object', api: 'new_object', group: 'Trade', owner: 'U-1', pk: 'id' };
const property = { api: 'id', name: 'ID', type: 'string', source: 'id', desc: '', group: '标识' };
await forms.saveDefinition('object', values, [property], undefined, [], { title: 'New definition' });
assert.equal(starts.at(-1).target.id, 'new_object'); assert.equal(starts.at(-1).target.isNew, true); assert.equal(writes.at(-1).section, 'properties'); assert.equal(writes.at(-1).rows[0].op, 'add');
await forms.saveDefinition('object', { ...values, api: 'immutable_api', name: 'Edited' }, [], { ...values, id: 'EXISTING-ID' }, [property], { join: 'CR-EXISTING' }, 'CR-EXISTING');
assert.equal(writes.at(-1).target.id, 'EXISTING-ID'); assert.equal(writes.at(-1).rows[0].op, 'delete'); assert.ok(writes.at(-2).rows.every((r) => r.key !== 'api'));
conflict = true; const startCount = starts.length;
await assert.rejects(forms.saveDefinition('metric', { ...values, dims: [], variants: [] }, [], undefined, [], {}, 'CR-RETRY'), (error) => error.crId === 'CR-RETRY'); assert.equal(starts.length, startCount, 'retry resumes existing CR'); conflict = false;
// Actual form load, explicit variants, 409 input preservation and read-only function guard.
const readonly = vue.ref(false), open = vue.ref(true), props = { kind: 'metric', targetId: 'gmv', crId: 'CR-1' }, emits = [];
let saves = 0, failSave = true;
const editor = load('pages/ontology/parts/DefinitionEditor.vue', {
  '@/composables/useUnsavedGuard': { useUnsavedGuard: () => (fn) => fn(), confirmDiscard: async () => true }, 'ant-design-vue': { message: { error() {}, success() {}, info() {} } }, '@/stores/confirm': { useConfirmStore: () => ({ ask: async () => true }) }, '@/composables/useReadonly': { useReadonly: () => ({ readonly, reason: vue.ref('read-only') }) }, '@/api/http': { currentApiContext: () => ctx },
  '@/api/definitionForms': { definitionChoices: async () => ({ owners: [{ id: 'U-1', name: 'DB User' }], objects: [], dimensions: [{ id: 'D1', name: 'Region', api: 'region' }], metrics: [{ id: 'gmv', name: 'GMV', api: 'gmv_api', version: 'v3', status: '已发布' }] }), definitionDetail: async () => ({ fields: { id: 'gmv', name: 'Database metric', api: 'gmv_api', owner: 'U-1', group: 'Sales', dims: ['Region'], formula: 'DB formula', variants: [{ code: 'gmv', label: 'Net', default: true, api: 'gmv_api', version: 'v3' }] }, properties: [], items: [] }), definitionProperties: async () => [], definitionContext: () => ({ options: {} }), saveDefinition: async () => { saves++; if (failSave) throw Object.assign(Error('409'), { crId: 'CR-1' }); return { id: 'gmv', crId: 'CR-1' }; } },
  '@/api/static': { isStaticReal: () => true, fetchCR: async () => ({}), fetchCRItems: async () => [] }, './useStaticCr': { draftFields: () => ({}), draftRows: (rows) => rows },
}, 'load,values,variants,validation,save,resume,defaultRangeMode,defaultRangeDays', { defineProps: () => props, defineModel: () => open, defineEmits: () => (...args) => emits.push(args) });
await editor.load(); assert.equal(editor.values.formula, 'DB formula'); assert.equal(editor.variants.value[0].code, 'gmv'); assert.equal(editor.validation.value, '');
assert.equal(editor.defaultRangeMode.value, 'none', 'new/old definitions do not invent a default window');
editor.defaultRangeMode.value = 'rolling_days'; assert.match(editor.validation.value, /1至93天/);
editor.defaultRangeDays.value = 7; assert.equal(editor.validation.value, '');
assert.equal(editor.values.defaultRange.days, 7);
editor.defaultRangeDays.value = 94; assert.match(editor.validation.value, /1至93天/);
editor.defaultRangeMode.value = 'none'; assert.equal(editor.values.defaultRange, null);
const setDefault = forms.execChanges({ ...values, defaultRange: { kind: 'rolling_days', days: 7 } });
assert.equal(setDefault.find(row => row.key === 'defaultRange').after.days, 7);
assert.equal(setDefault.find(row => row.key === 'defaultRange').op, 'add');
const clearDefault = forms.execChanges({ ...values, defaultRange: null }, { ...values, defaultRange: { kind: 'rolling_days', days: 7 } });
assert.equal(clearDefault.find(row => row.key === 'defaultRange').op, 'delete');
assert.ok(!forms.execChanges({ ...values }, { ...values }).some(row => row.key === 'defaultRange'), 'ordinary edits cannot create a default window');
editor.values.name = 'Unsaved name'; await editor.save(); assert.equal(editor.values.name, 'Unsaved name'); assert.equal(editor.resume.value, 'CR-1'); assert.equal(emits.length, 0);
readonly.value = true; await editor.save(); assert.equal(saves, 1); readonly.value = false;
editor.variants.value.push({ code: 'gmv', label: 'Duplicate', default: false }); assert.match(editor.validation.value, /不能重复/); editor.variants.value.pop();
failSave = false; await editor.save(); assert.equal(emits[0][0], 'saved'); assert.equal(open.value, false);
// Build guide mock sends zero requests; stale real completion cannot overwrite the new identity.
let isReal = false, guideCalls = 0;
const guide = load('pages/ontology/parts/BuildGuideDrawer.vue', {
  'vue-router': { useRouter: () => ({ push() {} }) }, '@/api/static': { isStaticReal: () => isReal }, '@/api/http': { currentApiContext: () => ctx, isApiError: () => false, apiClient: () => ({ isReal: () => true, get: async (path) => { guideCalls++; return path === '/ontology/overview' ? { cards: [{ key: 'obj', count: 2, unit: '个', title: '对象类型' }, { key: 'link', count: 1, unit: '条', title: '关系类型' }] } : { total: 0, items: [] }; } }) },
}, 'load,steps', { defineModel: () => vue.ref(true) });
await guide.load(); assert.equal(guideCalls, 0); assert.equal(guide.steps.value[0].state, '演示模式'); isReal = true; await guide.load(); assert.equal(guide.steps.value[1].state, '已配置'); assert.match(guide.steps.value[1].detail, /2 个对象类型/); const fnStep = guide.steps.value.find((s) => s.title === '函数'); assert.equal(fnStep.state, '未配置'); assert.match(fnStep.detail, /0 个函数/); assert.equal(fnStep.paths.map((p) => p.path).join(), '/build/ontology/functions'); assert.equal(guide.steps.value[0].state, '未配置');

// Actual import contract: stable ids, whitelisted relationship selection and lock join; no property/SQL payload.
const importPosts = [];
const importApi = load('api/objectImport.ts', { './http': { apiClient: () => ({ post: async (path, body, options) => { importPosts.push({ path, body, options }); return { crId: 'CR-IMPORT', targets: [{ kind: 'object', id: 'Imported' }] }; } }) }, './definitionForms': { definitionContext: () => ({ key: key(), options: { headers: { 'X-Demo-As': ctx.demoAs }, query: { previewAs: ctx.previewAs, ver: ctx.ver } } }) }, './static': { staticContextKey: key, fetchCR: async () => ({ lockVersion: 17 }) } });
const importPreview = { datasourceId: 'DS-DB', schema: 'public', fingerprint: 'database-fingerprint', generator: '规则', warnings: [], tables: [{ name: 'orders', pk: ['id'], columns: [], fks: [] }], proposal: { objectTypes: [{ table: 'orders', id: 'orders', api: 'Orders', name: 'Orders', pk: 'id', group: 'Trade', datasource: 'DS-DB', desc: '', props: [property] }], linkTypes: [{ id: 'FK-1', name: 'belongs to', from: 'orders', to: 'stores' }] } };
await importApi.commitObjectImport(importPreview, ['orders'], importPreview.proposal.objectTypes, true, { join: 'CR-IMPORT', desc: 'Real reason' }, ['FK-1']);
assert.equal(importPosts[0].body.lockVersion, 17); assert.equal(importPosts[0].body.reason, 'Real reason'); assert.deepEqual(Array.from(importPosts[0].body.linkIds), ['FK-1']); assert.equal(importPosts[0].body.objectTypes[0].props, undefined);
let importWrites = 0, importEmits = [];
const importOpen = vue.ref(true);
const importer = load('pages/ontology/parts/ObjectImportDrawer.vue', {
  '@/composables/useUnsavedGuard': { useUnsavedGuard: () => (fn) => fn(), confirmDiscard: async () => true }, 'ant-design-vue': { message: { info() {}, success() {} } }, '@/api/http': { currentApiContext: () => ctx }, '@/api/datasources': { listDatasources: async () => [{ id: 'DS-DB', name: 'Database' }], listDsTables: async () => [], isDataReal: () => true }, '@/api/definitionForms': { definitionChoices: async () => ({ owners: [{ id: 'U-1', name: 'DB User' }] }) }, '@/composables/useReadonly': { useReadonly: () => ({ readonly, reason: vue.ref('read-only') }) }, '@/api/objectImport': { previewObjectImport: async () => structuredClone(importPreview), commitObjectImport: async (_preview, _tables, rows, _include, _opts, linkIds) => { importWrites++; assert.equal(rows[0].id, rows[0].api); assert.deepEqual(Array.from(linkIds), []); return { crId: 'CR-IMPORT', targets: [{ kind: 'object', id: rows[0].id }] }; } },
}, 'load,tables,datasource,schema,selected,buildPreview,objects,selectedLinks,validation,commit', { defineModel: () => importOpen, defineEmits: () => (...args) => importEmits.push(args) });
await importer.load(); importer.tables.value = [{ schema: 'public', name: 'orders', comment: '' }]; importer.datasource.value = 'DS-DB'; importer.schema.value = 'public'; importer.selected.value = ['orders']; await importer.buildPreview();
assert.equal(importer.objects.value[0].id, 'Orders'); assert.equal(importer.selectedLinks.value.length, 0); importer.objects.value[0].owner = 'U-1'; assert.equal(importer.validation.value, '');
readonly.value = true; await importer.commit({ title: 'Import' }); assert.equal(importWrites, 0); readonly.value = false; await importer.commit({ title: 'Import' }); assert.equal(importWrites, 1); assert.equal(importEmits[0][1].crId, 'CR-IMPORT');

// Mapping uses actual GET choices/detail, old join lock and CR-only PUT, never a display-marker update.
const mappingPuts = [];
const mappingApi = load('api/objectImport.ts', { './http': { apiClient: () => ({ isReal: () => true, get: async () => ({ items: [{ id: 'OBJ-REAL', name: 'Object', api: 'Object' }], total: 1 }), put: async (path, body, options) => { mappingPuts.push({ path, body, options }); return { crId: 'CR-MAP', lockVersion: 19 }; } }) }, './definitionForms': { definitionContext: () => ({ key: key(), options: { headers: { 'X-Demo-As': ctx.demoAs } } }) }, './static': { staticContextKey: key, fetchCR: async () => ({ lockVersion: 18 }) } });
await mappingApi.commitTableMapping('DS-DB', 'public', 'orders', 'OBJ-REAL', { join: 'CR-MAP', desc: 'Mapping reason' });
assert.equal(mappingPuts[0].body.objectId, 'OBJ-REAL'); assert.equal(mappingPuts[0].body.lockVersion, 18); assert.equal(mappingPuts[0].body.reason, 'Mapping reason'); assert.equal(mappingPuts[0].body.objectType, undefined);
let mappingWrites = 0, mappingFails = true;
const mappingOpen = vue.ref(true), mappingEmits = [];
const mapper = load('pages/ontology/parts/ObjectMappingModal.vue', {
  'ant-design-vue': { message: { info() {}, success() {}, error() {} } }, '@/composables/useUnsavedGuard': { useUnsavedGuard: () => (fn) => fn() }, '@/composables/useReadonly': { useReadonly: () => ({ readonly, reason: vue.ref('read-only') }) }, '@/api/http': { currentApiContext: () => ctx }, '@/api/datasources': { isDataReal: () => true }, '@/api/definitionForms': { definitionDetail: async () => ({ fields: { source: 'old DS', table: 'old.table' } }) }, '@/api/objectImport': { mappingObjectChoices: async () => [{ id: 'OBJ-REAL', name: 'Object', api: 'Object' }], commitTableMapping: async () => { mappingWrites++; if (mappingFails) throw Error('409'); return { crId: 'CR-MAP' }; } },
}, 'load,loadObject,objectId,save', { defineProps: () => ({ datasourceId: 'DS-DB', datasourceName: 'DB', schema: 'public', table: 'orders', currentObjectId: 'OBJ-REAL' }), defineModel: () => mappingOpen, defineEmits: () => (...args) => mappingEmits.push(args) });
await mapper.load(); await mapper.loadObject(); await mapper.save({ title: 'Mapping' }); assert.equal(mapper.objectId.value, 'OBJ-REAL'); assert.equal(mappingEmits.length, 0);
readonly.value = true; await mapper.save({ title: 'Mapping' }); assert.equal(mappingWrites, 1); readonly.value = false; mappingFails = false; await mapper.save({ title: 'Mapping' }); assert.equal(mappingEmits[0][1], 'CR-MAP');

// Existing CR reviewer editor: actual functions, delayed identity, stable code + old lock, 409 preserves choice.
const releaseScript = read('pages/ontology/Releases.vue').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const releaseAst = ts.createSourceFile('Releases.vue', releaseScript, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const reviewerNames = ['canSetReviewer','reviewerOpen','reviewerOptions','reviewerSeq','reviewerKey','loadReviewers','openReviewer','canSaveReviewer','saveReviewer'];
const reviewerSource = releaseAst.statements.filter((s) => ts.isVariableStatement(s) && reviewerNames.includes(s.declarationList.declarations[0].name.getText(releaseAst)) || ts.isFunctionDeclaration(s) && reviewerNames.includes(s.name?.text)).map((s) => s.getText(releaseAst)).join('\n');
const identitySource=releaseAst.statements.filter((s)=>ts.isVariableStatement(s)&&['isOwner','isReviewer','canManage'].includes(s.declarationList.declarations[0].name.getText(releaseAst))).map((s)=>s.getText(releaseAst)).join('\n');
const identityCode=ts.transpileModule(`${identitySource}; export {isOwner,isReviewer,canManage};`,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const idCr=vue.ref({owner:'Same name',reviewer:'Same name',ownerId:'OWNER-ID',reviewerId:'REVIEWER-ID'}), meId=vue.ref('OTHER-ID'), identityVm={};
runInNewContext(identityCode,{exports:identityVm,computed:vue.computed,realMode:true,cur:idCr,realMeId:meId,me:vue.ref('Same name')});
assert.equal(identityVm.isOwner.value,false);assert.equal(identityVm.isReviewer.value,false,'same name must not grant another stable user reviewer access');meId.value='REVIEWER-ID';assert.equal(identityVm.isReviewer.value,true);meId.value='OWNER-ID';assert.equal(identityVm.isOwner.value,true);assert.equal(identityVm.isReviewer.value,false);delete idCr.value.ownerId;delete idCr.value.reviewerId;assert.equal(identityVm.isOwner.value,true,'legacy seed without stable ids retains compatibility');
let reviewerFinish, reviewerFail = false, emptyReviewers = false, sameNameReviewers = false, reviewHead = { lockVersion: 11, reviewer: '' }; const reviewerDelay = new Promise((r) => reviewerFinish = r), reviewerPatches=[];
let reviewerContext = { demoAs:'old-owner' }; const owner = vue.ref(true);
const reviewerCode = ts.transpileModule(`${reviewerSource}; export { reviewerOpen,reviewerOptions,reviewerId,reviewerError,loadReviewers,saveReviewer,canSaveReviewer };`, { compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020} }).outputText;
const reviewerVm = {};
runInNewContext(reviewerCode, { exports:reviewerVm,computed:vue.computed,ref:vue.ref,realMode:true,crFallback:vue.ref(false),readonly,cur:vue.ref({id:'CR-REAL',status:'草稿',reviewer:''}),isOwner:owner,currentApiContext:()=>reviewerContext,
  fetchCR:async()=>reviewHead,fetchReviewerOptions:async(_id,options)=> options.headers['X-Demo-As']==='old-owner' ? reviewerDelay : emptyReviewers ? [] : sameNameReviewers ? [{id:'U-A',name:'Same'},{id:'U-B',name:'Same'}] : [{id:'U-OTHER',name:'Actual reviewer'}],
  patchCR:async(id,body,options)=>{reviewerPatches.push({id,body,options});if(reviewerFail)throw Error('409 VERSION_CONFLICT');},loadCRs:async()=>true,message:{success(){}} });
reviewerVm.reviewerOpen.value=true; const olderReviewers=reviewerVm.loadReviewers(); await new Promise(setImmediate); reviewerContext={demoAs:'current-owner'}; await reviewerVm.loadReviewers(); reviewerFinish([{id:'PRIVATE',name:'Old identity'}]); await olderReviewers;
assert.equal(reviewerVm.reviewerOptions.value[0].id,'U-OTHER'); reviewerVm.reviewerId.value='U-OTHER'; reviewerFail=true; await reviewerVm.saveReviewer(); assert.equal(reviewerVm.reviewerId.value,'U-OTHER'); assert.match(reviewerVm.reviewerError.value,/409/);
readonly.value=true; await reviewerVm.saveReviewer(); assert.equal(reviewerPatches.length,1); readonly.value=false; await reviewerVm.loadReviewers(); reviewerFail=false; await reviewerVm.saveReviewer(); assert.equal(reviewerPatches.at(-1).body.reviewer,'U-OTHER'); assert.equal(reviewerPatches.at(-1).body.lockVersion,11); assert.equal(reviewerPatches.at(-1).options.headers['X-Demo-As'],'current-owner'); assert.equal(reviewerVm.reviewerOpen.value,false);
reviewerVm.reviewerOpen.value=true; emptyReviewers=true; await reviewerVm.loadReviewers(); assert.equal(reviewerVm.canSaveReviewer.value,false); owner.value=false; await reviewerVm.saveReviewer(); assert.equal(reviewerPatches.length,2);
owner.value=true;emptyReviewers=false;sameNameReviewers=true;reviewHead={lockVersion:11,reviewer:'Same',reviewerId:'U-B'};reviewerVm.reviewerId.value=undefined;await reviewerVm.loadReviewers();assert.equal(reviewerVm.reviewerId.value,'U-B','default selection must use reviewerId when display names collide');

console.log('definition forms: full choices/context, new/existing targets, CR retry, real fields/variants, read-only and guide facts passed');
