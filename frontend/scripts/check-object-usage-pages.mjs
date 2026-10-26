// Actual object tabs + shared loader; HTTP boundary controlled, no writes.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const tick=()=>new Promise(setImmediate);
const context=vue.reactive({demoAs:'first',previewAs:'',ver:''}),real=vue.ref(true),draft=vue.ref({id:'CR-SAVED'});
const requests=[];let cleanups=[];
const api={isStaticReal:()=>real.value,staticContextKey:()=>JSON.stringify(context),fetchStaticPage:(path,query,signal)=>new Promise((resolve,reject)=>requests.push({path,query:{...query},ctx:{...context},signal,resolve,reject}))};
const framework={...vue,onBeforeUnmount:fn=>cleanups.push(fn)};
function load(path,imports={},expose='',globals={}){
 let source=readFileSync(new URL(`../src/${path}`,import.meta.url),'utf8');if(path.endsWith('.vue'))source=source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
 const code=ts.transpileModule(`${source}\n${expose}`,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
 const out={};runInNewContext(code,{exports:out,AbortController,...globals,require:name=>{if(name==='vue')return framework;if(name in imports)return imports[name];if(name.endsWith('.vue'))return {};throw Error(`Missing ${name}`);}});return out;
}
const pager=load('pages/ontology/parts/useStaticPage.ts',{'@/api/static':api});
const pagination=load('mock/paginate.ts');
const mockMetrics=Array.from({length:25},(_,i)=>({m:{id:`MOCK-${i}`,status:'已发布'},base:['object/real'],via:[]}));
const mockViews=Array.from({length:25},(_,i)=>({id:`MOCK-${i}`,kind:'我的页面',name:'Mock view',owner:'Mock owner',props:[]}));
const mockCrs=Array.from({length:25},(_,i)=>({cr:{id:`MOCK-${i}`,title:'Mock CR',status:'草稿'},parts:[]}));
const imports={
 '@/api/static':api,'../useStaticPage':pager,'@/mock/paginate':pagination,
 '@/mock/changes':{CHANGE_REQUESTS:[{id:'MOCK-REVIEW',status:'审核中'}],crDetail:()=>({touches:['metric:DB-0']})},'../useDraftView':{draftOf:()=>draft.value},
 './objectGov':{metricsOf:()=>mockMetrics,crsOf:()=>mockCrs,objLabel:()=> 'Database object'},
 '@/mock/format':{fmtInt:String},'@/mock/objectProps':{OBJECT_PROPS:{},draftPropDiff(){throw Error('real mode read mock draft diff');}},
 '@/mock/objectViews':{viewsOf:()=>mockViews},'@/stores/app':{useAppStore:()=>({ontologyVersion:'v2',ontologyNextVersion:'v3'})},
 '@/api/releases':{RELEASES:[]},'@/mock/objectHistory':{legacyRevs:()=>{if(real.value)throw Error('real mode read legacy mock revisions');return [];}},'@/composables/useVersion':{useVersion:()=>({view(){}})},
};
const row=(kind,id)=>kind==='metric'?{id,name:id,status:'已发布',base:['object/real'],via:[]}:kind==='cr'?{cr:{id,title:id,status:'草稿',owner:'DB owner'},parts:['实际属性']}:{id,name:id,owner:'DB owner',kind:'我的页面',props:['实际字段'],affectedProps:['实际字段']};
for(const [file,kind] of [['MetricsTab.vue','metric'],['ViewsTab.vue','view'],['HistoryTab.vue','cr']]){
 real.value=true;context.demoAs='first';context.ver='';context.previewAs='';draft.value={id:'CR-SAVED'};cleanups=[];
 const props=vue.reactive({objectId:'object/real',readonly:false}),scope=vue.effectScope();const before=requests.length;
 const component=scope.run(()=>load(`pages/ontology/parts/object/${file}`,imports,`export {page,size,pageRows,total,remote,${kind==='cr'?'filter':kind==='view'?'kind,kw,onlyHit,hit':'kind,shown'}};`,{defineProps:()=>props}));
 await tick();assert.equal(requests.length,before+1);let request=requests.at(-1);
 assert.equal(request.path,'/object-types/object%2Freal/usage');assert.equal(request.query.kind,kind);assert.equal(request.query.size,10);assert.equal(component.pageRows.value.length,0,'real initial state never renders samples');
 request.resolve({items:Array.from({length:10},(_,i)=>row(kind,`DB-${i}`)),total:23,page:1,size:10});await tick();assert.equal(component.pageRows.value.length,10);if(kind==='metric')assert.equal(component.shown(component.pageRows.value[0]).text,'已发布');
 component.page.value=2;await tick();request=requests.at(-1);assert.equal(request.query.page,2);assert.equal(component.total.value,23,'paging retains total');
 request.resolve({items:Array.from({length:10},(_,i)=>row(kind,`PAGE2-${i}`)),total:23,page:2,size:10});await tick();assert.equal(component.pageRows.value.length,10,'server page two must not be sliced a second time');
 component.size.value=20;await tick();request=requests.at(-1);assert.equal(request.query.page,1);assert.equal(request.query.size,20);
 request.resolve({items:[row(kind,'SIZE-20')],total:1,page:1,size:20});await tick();
 if(kind==='cr')component.filter.value='进行中';else component.kind.value=kind==='metric'?'base':'我的页面';
 if(kind==='view'){component.kw.value='owner';component.onlyHit.value=true;}
 await tick();request=requests.at(-1);assert.equal(request.query.filter,kind==='cr'?'进行中':kind==='metric'?'base':'我的页面');
 if(kind==='view'){assert.equal(request.query.q,'owner');assert.equal(request.query.crId,'CR-SAVED');assert.equal(request.query.affectedOnly,1);}
 request.resolve({items:[row(kind,'FILTERED')],total:1,page:1,size:20});await tick();
 if(kind==='view'){assert.deepEqual(Array.from(component.hit(component.pageRows.value[0])),['实际字段']);props.readonly=true;await tick();assert.equal(requests.at(-1).query.crId,undefined);assert.equal(requests.at(-1).query.affectedOnly,undefined);requests.at(-1).resolve({items:[],total:0,page:1,size:20});await tick();}
 component.page.value=99;await tick();request=requests.at(-1);request.resolve({items:[row(kind,'LAST')],total:41,page:3,size:20});await tick();assert.equal(component.page.value,3);assert.equal(requests.at(-1).query.page,3);requests.at(-1).resolve({items:[row(kind,'LAST')],total:41,page:3,size:20});await tick();
 const oldLoad=component.remote.reload(),old=requests.at(-1);context.demoAs='second';await tick();const current=requests.at(-1);assert.equal(old.signal.aborted,true);assert.equal(component.total.value,0);old.resolve({items:[row(kind,'PRIVATE')],total:999,page:3,size:20});await oldLoad;
 current.resolve({items:[row(kind,'SAFE')],total:41,page:3,size:20});await tick();assert.ok(JSON.stringify(component.pageRows.value).includes('SAFE'));assert.ok(!JSON.stringify(component.pageRows.value).includes('PRIVATE'));
 const denied=component.remote.reload();requests.at(-1).reject(Error('403'));await denied;assert.equal(component.pageRows.value.length,0);assert.match(component.remote.error.value,/403/);
 const unavailable=component.remote.reload();requests.at(-1).reject(Error('NETWORK_ERROR'));await unavailable;assert.equal(component.pageRows.value.length,0);assert.match(component.remote.error.value,/NETWORK_ERROR/);
 cleanups.forEach(fn=>fn());scope.stop();
 real.value=false;draft.value=undefined;cleanups=[];const mockScope=vue.effectScope(),mockBefore=requests.length;
 const mock=mockScope.run(()=>load(`pages/ontology/parts/object/${file}`,imports,'export {page,size,pageRows,total};',{defineProps:()=>props}));await tick();assert.equal(requests.length,mockBefore,'explicit demo mode makes no requests');assert.equal(mock.total.value,25);mock.page.value=2;await tick();assert.equal(mock.pageRows.value.length,12);cleanups.forEach(fn=>fn());mockScope.stop();
}
console.log('Object usage tabs passed: real pages/size/filter, saved-CR effects, context/403/clamp, and mock isolation.');
