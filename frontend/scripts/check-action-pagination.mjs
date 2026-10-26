// Executes both actual SFC scripts and real draft sections; requests are controlled locally.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import {parse,compileTemplate} from '@vue/compiler-sfc';
const tick=async()=>{for(let i=0;i<10;i++){await Promise.resolve();await vue.nextTick();}};
const defer=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function load(file,deps,names=''){
 let source=readFileSync(new URL('../src/'+file,import.meta.url),'utf8');
 if(file.endsWith('.vue')){const {descriptor,errors}=parse(source);assert.equal(errors.length,0);assert.equal(compileTemplate({source:descriptor.template.content,filename:file,id:file}).errors.length,0);source=descriptor.scriptSetup.content;}
 const js=ts.transpileModule(source+`\nexport {${names}};`,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
 const exports={};runInNewContext(js,{exports,console,require:name=>name.endsWith('.vue')?{}:name in deps?deps[name]:(()=>{throw Error('Missing '+name);})()});return exports;
}
for(const kind of ['action','event']){
 const ctx=vue.reactive({demoAs:'admin',ver:'',previewAs:''}),key=()=>JSON.stringify(ctx),route=vue.reactive({query:{tab:kind==='action'?'params':'payload'}}),scope=vue.effectScope(),calls=[],errors=[];
 const isDraft=vue.ref(false),draft=vue.ref(undefined),detailReady=vue.ref(false);
 const head={id:'TYPE-1',name:'Database type',api:'DbType',version:'v1',group:'Group',usedBy:[],specific:[],objKind:'order',owner:'DB'};
 let detailRead=async()=>{detailReady.value=true;},pageRead=async(page,size)=>({items:[{api:'field'+page,name:'Field',envelope:false}],total:25,page:Math.min(page,3),size}),runRead=async q=>({items:[{id:'run'+q.page}],total:50,page:Math.min(q.page,5)}),listRead=async q=>({items:[head],total:30,page:Math.min(q.page,3),groups:[],sources:[]});
 const baseline=vue.ref([{api:'a',name:'A'}]);
 const api={actionApi:{isReal:()=>true},actionContextKey:key,snapshotReady:()=>true,loadSnapshot:async()=>{},actionTypes:()=>[head],eventTypes:()=>[head],actionDetailReady:()=>detailReady.value,eventDetailReady:()=>detailReady.value,loadActionDetail:()=>detailRead(),loadEventDetail:()=>detailRead(),
 listActionTypes:q=>listRead(q),listEventTypes:q=>listRead(q),loadWritebackSystems:async()=>{},crWriteOn:()=>false,crWriter:()=>({on:()=>false}),
 paramsOf:()=>baseline.value,condsOf:()=>[],writebackOf:()=>({edits:[],effects:[]}),payloadOf:()=>[],subscribersOf:()=>[],
 listActionParams:async(_id,page,size)=>{calls.push(['params',page,size]);return pageRead(page,size);},listEventPayload:async(_id,page,size)=>{calls.push(['payload',page,size]);return pageRead(page,size);},
 getActionRunStats:async()=>({total:1000,ok:900,rate:.9,avgMs:5}),getActionUsedBy:async()=>[],getRunDaily:async()=>[],
 listActionRuns:async(_id,q)=>{calls.push(['runs',q]);return runRead(q);},runsOf:()=>[],getEventStats:async()=>({daily:[]}),listEvents:async()=>({items:[],total:0}),
 };
 const deps={vue,'vue-router':{useRoute:()=>route,useRouter:()=>({replace:({query})=>{route.query=query;return Promise.resolve();}})},'ant-design-vue':{message:{error:e=>errors.push(String(e)),success(){},warning(){}}},'@ant-design/icons-vue':{},'@/api/action':api,'@/api/http':{currentApiContext:()=>ctx,isApiError:()=>false},
 '@/composables/useDrawer':{useDrawer:()=>({})},'@/composables/usePagedQuery':{usePagedQuery:({defaultSize})=>({page:vue.ref(1),size:vue.ref(defaultSize),defaultSize})},'@/composables/useUnsavedGuard':{useUnsavedGuard:()=>f=>f()},'@/stores/confirm':{useConfirmStore:()=>({})},
 './parts/useDraftView':{useDraftView:()=>({draft,isDraft,readonly:vue.ref(false),editTip:vue.ref(''),reason:vue.ref('')})},
 '@/mock/paginate':{paginate:(rows,{page,size})=>({items:rows.slice((page-1)*size,page*size),total:rows.length})},'@/mock/actionEvent':{ACTION_TYPES:[],EVENT_TYPES:[],withSystem:x=>x},'@/mock/changes':{},'@/mock/dynamic':{},'@/mock/extsys':{},'@/mock/format':{},'@/theme':{},'@/mock/releases':{},
 };
 deps['./useDraftEdits']=load('pages/ontology/useDraftEdits.ts',{vue,'ant-design-vue':deps['ant-design-vue'],'vue-router':deps['vue-router'],'@/composables/useUnsavedGuard':deps['@/composables/useUnsavedGuard'],'@/stores/confirm':deps['@/stores/confirm']});
 const names=kind==='action'?'ready,cur,LPAGE,lpage,listTotal,params,pq,runs,rq,stats,paramSec,canEdit':'ready,cur,LPAGE,lpage,listTotal,fields,fq,ready,canEdit';
 let page=scope.run(()=>load(`pages/ontology/${kind==='action'?'ActionTypes':'EventTypes'}.vue`,deps,names));await tick();assert.equal(page.LPAGE,10);assert.equal(page.ready.value,true);
 const q=kind==='action'?page.pq:page.fq,table=kind==='action'?page.params:page.fields;
 assert.equal(q.size.value,10);assert.equal(table.value.total,25);assert.equal(calls.filter(c=>c[0]===(kind==='action'?'params':'payload')).length,1);
 const held=defer();pageRead=()=>held.promise;q.page.value=2;assert.equal(table.value.total,25,'paging retains total');held.resolve({items:[{api:'page2',envelope:false}],total:25,page:2});await tick();assert.equal(table.value.items[0].api,'page2');
 pageRead=async()=>({items:[{api:'last',envelope:false}],total:25,page:3});q.page.value=99;await tick();assert.equal(q.page.value,3);
 const listHeld=defer();listRead=()=>listHeld.promise;page.lpage.value=2;assert.equal(page.listTotal.value,30);assert.equal(page.cur.value.id,'TYPE-1');listHeld.resolve({items:[],total:30,page:2});await tick();assert.equal(page.cur.value.id,'TYPE-1');
 if(kind==='action'){
  route.query.tab='runs';await tick();assert.equal(page.stats.value.total,1000,'summary is backend aggregate, not page count');assert.equal(page.runs.value.total,50);
  const delayed=defer();runRead=()=>delayed.promise;page.rq.page.value=2;assert.equal(page.runs.value.total,50);delayed.resolve({items:[{id:'run2'}],total:50,page:2});await tick();assert.equal(page.runs.value.items[0].id,'run2');
  detailReady.value=false;const editDetail=defer();detailRead=async()=>{await editDetail.promise;detailReady.value=true;};draft.value={id:'CR-1'};isDraft.value=true;assert.equal(page.ready.value,false);assert.equal(page.canEdit.value,false,'incomplete edit baseline cannot be edited');editDetail.resolve();await tick();assert.equal(page.canEdit.value,true);
  page.paramSec.update('a',{api:'a',name:'Unsaved'});const before=page.paramSec.rows.value;q.page.value=2;await tick();assert.equal(page.paramSec.rows.value,before,'paging retains draft working buffer');assert.equal(page.paramSec.rows.value[0].data.name,'Unsaved');
 }
 const late=defer();pageRead=()=>late.promise;isDraft.value=false;route.query.tab=kind==='action'?'params':'payload';q.page.value=2;ctx.previewAs='viewer';assert.equal(table.value.total,0,'new identity clears page immediately');scope.stop();late.resolve({items:[{api:'PRIVATE'}],total:999,page:2});await tick();assert.notEqual(table.value.total,999);
}
console.log('Action/Event SFC paging passed: 10 default, backend params/payload/runs pages, aggregate stats, total retention/clamp, stable selection and draft baseline/buffer, stale/unmounted guards.');
