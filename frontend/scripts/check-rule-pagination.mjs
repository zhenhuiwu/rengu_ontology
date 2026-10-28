// Run: node scripts/check-rule-pagination.mjs. Executes the actual Rules SFC; no HTTP/DB writes.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import {parse,compileTemplate} from '@vue/compiler-sfc';
const tick=async()=>{for(let i=0;i<8;i++){await Promise.resolve();await vue.nextTick();}};
const defer=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const ctx=vue.reactive({demoAs:'admin',ver:'',previewAs:''}), key=()=>JSON.stringify(ctx);
const route=vue.reactive({query:{}}), errors=[],calls=[];
const def=id=>({id,name:id,version:1,status:'已发布',displayStatus:'已发布',owner:'DB',desc:'',objKind:'order',level:'中',output:'告警',conds:[],logic:'且',expr:'',dedupe:'',cooldown:'',trigger:{kind:'定时',text:''},autoStage:{enabled:false,actionTypeId:'',mapping:[],confirmer:''},draft:{id,version:2,conds:[{value:'saved'}],autoStage:{enabled:false,actionTypeId:'',mapping:[],confirmer:''}}});
let listRead=async()=>({items:[],total:0,page:1}),detailRead=async id=>def(id),replayRead=async()=>({samples:[]});
const dyn={dynamicApi:{isReal:()=>true},dynamicContextKey:key,CR_WRITE:true,emptyRuleMeta:()=>({confirmers:[],mappingSources:[],statuses:[],triggers:[],objects:[],metrics:[],events:[],runAsUserId:''}),
 listRules:async q=>{calls.push(['list',{...q}]);return listRead(q);},
 getRule:async id=>{assert.ok(id,'empty ID cannot request detail');calls.push(['detail',id]);return detailRead(id);},
 getRulesMeta:async()=>({confirmers:[],mappingSources:[],statuses:[]}),
 listRuleHits:async(id,_range,page,size)=>{assert.ok(id);return {items:[],total:30,page:Math.min(page,3),size};},
 listRuleVersions:async id=>{assert.ok(id);return [];},listRuleActionRuns:async id=>{assert.ok(id);return {items:[]};},
 getRuleHit:async id=>{assert.ok(id);return {id};},replayRule:async id=>{assert.ok(id);return replayRead();},crErrText:String,
};
const draft=vue.ref({id:'CR-DB'}),isDraft=vue.ref(true);
const deps={
 vue,'vue-router':{useRoute:()=>route,useRouter:()=>({replace:({query})=>{route.query=query;return Promise.resolve();}})},
 'ant-design-vue':{message:{error:e=>errors.push(String(e)),success(){},warning(){}}},'@ant-design/icons-vue':{},
 '@/api/fallback':{fallBackIfDown:()=>{assert.fail('real rules must not fall back');}},
 '@/api/http':{isApiError:()=>false},'@/api/dynamic':dyn,
 '@/api/action':{actionApi:{isReal:()=>false},actionTypes:()=>[],eventTypes:()=>[],paramsOf:()=>[]},
 '@/api/static':{isStaticReal:()=>false,OBJECT_TYPE_LIST:[]},
 '@/composables/usePagedQuery':{usePagedQuery:()=>({page:vue.ref(1),size:vue.ref(10),defaultSize:10})},
 '@/composables/useReadonly':{useReadonly:()=>({readonly:vue.computed(()=>!!ctx.ver||!!ctx.previewAs),reason:vue.ref('只读')})},
 './parts/useDraftView':{useDraftView:()=>({draft,isDraft,editTip:vue.ref(''),setView(){},start(){},cancel(){}}),draftOf:()=>undefined},
 '@/stores/confirm':{useConfirmStore:()=>({})},
 '@/mock/paginate':{paginate:(items)=>({items,total:items.length})},'@/mock/format':{fmtInt:String},
 '@/mock/rules':{RULE_DEFS:[def('DEMO')],RULE_DRAFTS:{},objKindLabel:String},
 '@/mock/changes':{CHANGE_REQUESTS:[]},'@/mock/dynamic':{},'@/mock/metrics':{},'@/mock/metricCatalog':{},'@/mock/actions':{ACTIONS:[]},
};
const sfc=parse(readFileSync(new URL('../src/pages/ontology/Rules.vue',import.meta.url),'utf8'));
assert.equal(sfc.errors.length,0);assert.equal(compileTemplate({source:sfc.descriptor.template.content,filename:'Rules.vue',id:'rules'}).errors.length,0);
const js=ts.transpileModule(sfc.descriptor.scriptSetup.content+'\nexport {remote,list,listTotal,base,LPAGE,lpage,loadList,loadDetail,fetchReplay,running,edits,ensureEdits};',{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const scope=vue.effectScope();let page;
scope.run(()=>{const exports={};runInNewContext(js,{exports,setTimeout,console,require:name=>name.endsWith('.vue')?{}:name in deps?deps[name]:(()=>{throw Error('Missing '+name);})()});page=exports;});
await tick();assert.equal(page.LPAGE,10);assert.equal(page.list.value.length,0);assert.equal(page.base.value.id,'');assert.equal(calls.some(c=>c[0]==='detail'),false);
listRead=async q=>({items:[def(q.page===1?'R-1':'R-11')],total:21,page:q.page});await page.loadList();await tick();assert.equal(page.base.value.id,'R-1');
page.ensureEdits().thresholds[0]='unsaved';const selected=page.remote.detail;
const next=defer();listRead=()=>next.promise;page.lpage.value=2;assert.equal(page.listTotal.value,21);assert.equal(page.remote.detail,selected);
next.resolve({items:[def('R-11')],total:21,page:2});await tick();assert.equal(page.base.value.id,'R-1');assert.equal(page.remote.detail,selected);assert.equal(page.edits['R-1'].thresholds[0],'unsaved');
listRead=async()=>({items:[def('R-21')],total:21,page:3});page.lpage.value=99;await tick();assert.equal(page.lpage.value,3);assert.equal(page.listTotal.value,21);
const old=defer();detailRead=()=>old.promise;const oldPending=page.loadDetail();assert.equal(page.remote.detail,null);
detailRead=async()=>{throw Error('current detail failed');};await page.loadDetail();old.resolve(def('R-1'));await oldPending;assert.equal(page.remote.detail,null);assert.ok(errors.at(-1).includes('current detail failed'));
listRead=async()=>({items:[],total:0,page:1});detailRead=async id=>def(id);const staleList=defer();const staleReplay=defer();listRead=()=>staleList.promise;const pending=page.loadList();
for(const field of ['demoAs','ver','previewAs']){const original=ctx[field];listRead=async()=>({items:[],total:0,page:1});ctx[field]='other';assert.equal(page.remote.detail,null);assert.equal(page.listTotal.value,0);assert.equal(Object.keys(page.edits).length,0);ctx[field]=original;await tick();}
staleList.resolve({items:[def('STALE')],total:500,page:1});await pending;assert.equal(page.list.value.length,0);assert.equal(page.base.value.id,'');
listRead=async()=>({items:[def('R-1')],total:1,page:1});await page.loadList();await tick();
const older=defer(),newer=defer();replayRead=()=>older.promise;const first=page.fetchReplay();replayRead=()=>newer.promise;const last=page.fetchReplay();
older.resolve({samples:[],marker:'older'});await first;assert.equal(page.running.value,true);assert.equal(page.remote.replay,null);newer.resolve({samples:[],marker:'newer'});await last;assert.equal(page.remote.replay.marker,'newer');assert.equal(page.running.value,false);
const unmounted=defer();replayRead=()=>unmounted.promise;const dying=page.fetchReplay();const oldErrors=errors.length;scope.stop();unmounted.reject(Error('after unmount'));await dying;assert.equal(errors.length,oldErrors);assert.equal(page.remote.replay,null,'a new replay clears the previous result and unmounted failures cannot restore it');
assert.match(sfc.descriptor.template.content,/disabled>对比/);assert.match(sfc.descriptor.template.content,/disabled>回滚/);
console.log('Rules SFC passed: 10/page, empty DB, selected draft buffer, totals/clamp, identity/version/preview guards, failed detail, latest replay and unmount.');
