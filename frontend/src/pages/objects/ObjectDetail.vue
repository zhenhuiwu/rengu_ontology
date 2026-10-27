<script setup lang="ts">
/**
 * 对象详情（/use/objects/detail?type=&id=&tab=），元数据驱动（backend/docs/backend/generic-object-explorer.md），旧链接 ?kind= 照样认。
 * 标题行 = 标题属性 + 类型名 + 显著属性（visibility=prominent）；概览按属性分组（prop_group）列出其余属性，hidden 不显示、敏感属性只显示“已脱敏”；
 * 关联对象 = 本类型的已发布关系，按关系两端的属性取对端对象（对端是主键 → 取单个，否则按对端属性筛选）；
 * 相关指标 = 维度里含本类型的已发布指标（只给名称，点开在当前页提问，不编造数值）；
 * 右栏 = 作用对象是本类型的已发布动作 + 对它提问。页面里不写具体对象类型或字段。
 * 取数：objMeta.ts + objRows.ts；真实关联由后端读取已发布关系，失败直接提示。
 * 预览：对象不在被预览用户范围内时整页显示无权查看；动作只读，提问照常。动作执行接口还没有：真接口模式下不假装成功（按钮锁住并说明）。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router';
import { currentApiContext } from '@/api/http';
import { message } from 'ant-design-vue';
import { LeftOutlined, MessageOutlined } from '@ant-design/icons-vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { usePreview } from '@/composables/usePreview';
import type { AskScope } from '@/mock/objects-qa';
import ObjAskModal from './parts/ObjAskModal.vue';
import AppPagination from '@/components/AppPagination.vue';
import { exploreRelated, type ExploreRow } from '@/api/qaObjects';
import { qaReal } from '@/api/qa';
import { errText } from '../qa/useQaSession';
import { listTypes, loadMeta, metaContextKey, linksOf, actionsOf, metricsOf, fmtVal, propLabel, type TypeMeta, type LinkMeta, type PropMeta } from './objMeta';
import { getRow, searchRows, type GetResult, type ScopeFn } from './objRows';

const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const { readonly, reason } = useReadonly();
const pv = usePreview();
const qaLive = computed(qaReal), qaWriteReal = computed(qaReal);
const qs = (k: string) => { const v = route.query[k]; return String(Array.isArray(v) ? v[0] ?? '' : v ?? ''); };
const contextQuery = () => Object.fromEntries(['ontology', 'ver', 'previewAs', 'as'].map((key) => [key, route.query[key]]));
const typeId = computed(() => qs('type') || qs('kind'));
const id = computed(() => qs('id'));
const tab = computed({ get: () => qs('tab') || 'overview', set: (v: string) => router.replace({ query: { ...route.query, tab: v === 'overview' ? undefined : v } }) });
const scope: ScopeFn = (n) => pv.scopeOf(n);

/* 元数据 + 行：仅显式 mock 模式使用本地数据。 */
const meta = ref<TypeMeta | null>(null);
const got = ref<GetResult | null>(null);
const loading = ref(false);
const loadError = ref('');
let seq = 0;
async function inCurrentMode<T>(f: (live: boolean) => Promise<T>): Promise<T> {
  return f(qaLive.value);
}
watch([typeId, id, qaLive, metaContextKey, () => pv.userName.value], async () => {
  const my = ++seq; got.value = null; meta.value = null; loadError.value = ''; loading.value = false;
  if (!typeId.value || !id.value) return;
  loading.value = true;
  try {
    const m = await loadMeta(typeId.value);
    if (my !== seq) return;
    meta.value = m;
    if (m) { const g = await inCurrentMode((live) => getRow(m, id.value, live, scope)); if (my === seq) got.value = g; }
  } catch (e) { if (my === seq) loadError.value = `加载对象失败：${errText(e)}`; } finally { if (my === seq) loading.value = false; }
}, { immediate: true });
const row = computed(() => got.value?.row ?? null);
const props = computed(() => meta.value?.properties ?? []);
const pmap = computed(() => new Map(props.value.map((p) => [p.key, p])));
const shown = (p: PropMeta) => p.visibility !== 'hidden' && p.key !== meta.value?.titleProp;
const val = (p: PropMeta) => (p.sensitive ? '已脱敏' : fmtVal(p, row.value?.values[p.key]));
const prominent = computed(() => props.value.filter((p) => shown(p) && p.visibility === 'prominent'));
const groups = computed(() => {
  const g = new Map<string, PropMeta[]>();
  props.value.filter((p) => shown(p) && p.visibility !== 'prominent').forEach((p) => g.set(p.group, [...(g.get(p.group) ?? []), p]));
  return [...g].map(([title, fields]) => ({ title, fields }));
});

/* 面包屑：回到搜索结果，保留筛选（上次搜的是别的类型就回到本类型） */
const back = computed<LocationQueryRaw>(() => {
  let q: LocationQueryRaw = {};
  try { q = JSON.parse(sessionStorage.getItem(`obj-search:last:${currentApiContext().ontologyId}`) || '{}'); } catch { q = {}; }
  return { ...(q.type === typeId.value ? q : { type: typeId.value }), ...contextQuery() };
});
const backLabel = computed(() => {
  const f = back.value.f || back.value.kw ? '（带筛选）' : '';
  return `对象搜索${meta.value ? ` · ${meta.value.name}` : ''}${f}`;
});
const goBack = () => router.push({ path: '/use/objects/search', query: typeId.value ? back.value : {} });

/* 关联对象：每条关系取前 REL_SIZE 个 */
const REL_SIZE = qaReal() ? 10 : 5;
interface RelGroup { link: LinkMeta; meta: TypeMeta | null; items: ExploreRow[]; total: number; page?: number; state: 'ok' | 'unbound' | 'unsupported' | 'nokey' | 'denied' | 'error' }
const rels = ref<RelGroup[]>([]);
const relatedLoading = ref(false), relatedError = ref('');
let relatedSeq = 0;
const typeNames = ref<Record<string, string>>({});
let namesSeq = 0;
watch(metaContextKey, async () => {
  const my = ++namesSeq;
  typeNames.value = {};
  try { const ts = await listTypes(); if (my === namesSeq) typeNames.value = Object.fromEntries(ts.map((t) => [t.id, t.name])); }
  catch (e) { if (my === namesSeq) message.error(`对象类型加载失败：${errText(e)}`); }
}, { immediate: true });
watch([row, tab, metaContextKey], async () => {
  const ticket = ++relatedSeq;
  rels.value = [];
  relatedError.value = ''; relatedLoading.value = false;
  if (tab.value !== 'related' || !row.value || !meta.value) return;
  const my = seq; const r = row.value;
  if (qaLive.value) {
    relatedLoading.value = true;
    try {
      const groups = await exploreRelated(meta.value.id, r.id, { size: REL_SIZE });
      if (ticket === relatedSeq) rels.value = groups.map((g) => ({ link: { id: g.linkId, label: g.label, other: g.other, thisProp: '', otherProp: '', many: true }, meta: null, items: g.items, total: g.total, page: g.page, state: g.state }));
    } catch (e) { if (ticket === relatedSeq) relatedError.value = `关联对象加载失败：${errText(e)}`; }
    finally { if (ticket === relatedSeq) relatedLoading.value = false; }
    return;
  }
  const out = await Promise.all(linksOf(meta.value.id).map(async (link): Promise<RelGroup> => {
    const g: RelGroup = { link, meta: null, items: [], total: 0, state: 'ok' };
    const v = r.values[link.thisProp];
    if (v == null) return { ...g, state: 'nokey' };
    try {
      const om = await loadMeta(link.other);
      g.meta = om;
      if (!om) return { ...g, state: 'unbound' };
      if (link.otherProp === om.pkProp) {
        const one = await inCurrentMode((live) => getRow(om, String(v), live, scope));
        return { ...g, items: one.row ? [one.row] : [], total: one.row ? 1 : 0, state: !one.bound ? 'unbound' : one.denied ? 'denied' : 'ok' };
      }
      if (!om.properties.find((p) => p.key === link.otherProp)?.filterable) return { ...g, state: 'nokey' };
      const res = await inCurrentMode((live) => searchRows(om, { kw: '', filters: { [link.otherProp]: [String(v)] }, sort: '', page: 1, size: REL_SIZE }, live, scope));
      return { ...g, items: res.items, total: res.total, state: res.bound ? 'ok' : 'unbound' };
    } catch { return { ...g, state: 'error' }; }
  }));
  if (my === seq && ticket === relatedSeq) rels.value = out;
}, { immediate: true });
const relNote: Record<RelGroup['state'], string> = { ok: '暂无', unbound: '对端类型未接入数据', unsupported: '此关系暂不可查询', nokey: '没有关联键值，或对端属性不支持按值查找', denied: '范围外，无权查看', error: '加载失败' };
async function relatedPage(group: RelGroup, page: number) {
  const ticket = relatedSeq;
  if (!qaLive.value || !meta.value || !row.value || relatedLoading.value) return;
  relatedLoading.value = true; relatedError.value = '';
  try {
    const result = await exploreRelated(meta.value.id, row.value.id, { link: group.link.id, page, size: REL_SIZE });
    if (ticket === relatedSeq && result[0]) { const g = result[0]; Object.assign(group, { items: g.items, total: g.total, page: g.page, state: g.state }); }
  } catch (e) { if (ticket === relatedSeq) relatedError.value = `关联对象加载失败：${errText(e)}`; }
  finally { if (ticket === relatedSeq) relatedLoading.value = false; }
}
const openRel = (g: RelGroup, r: ExploreRow) => router.push({ path: '/use/objects/detail', query: { ...contextQuery(), type: g.link.other, id: r.id } });
const moreRel = (g: RelGroup) => router.push({ path: '/use/objects/search', query: { ...contextQuery(), type: g.link.other, f: JSON.stringify({ [g.link.otherProp]: [String(row.value?.values[g.link.thisProp])] }) } });

/* 动作：公共确认卡。演示模式提示“已提交”；真接口模式动作执行接口还没有，不假装成功 */
const actions = computed(() => (meta.value ? actionsOf(meta.value) : []));
const metrics = computed(() => (meta.value ? metricsOf(meta.value) : []));
const done = reactive<Record<string, string>>({});
watch(() => `${typeId.value}:${id.value}`, () => { Object.keys(done).forEach((k) => delete done[k]); });
const actOff = computed(() => (readonly.value ? reason.value : qaWriteReal.value ? '动作执行接口还没接入，暂时不能从这里发起' : ''));
async function run(a: { id: string; name: string; desc: string; approval: string }) {
  if (actOff.value || !meta.value || !row.value) return;
  const ok = await confirm.ask({ title: a.name, target: `${meta.value.name} ${row.value.title}（${row.value.id}）`, impact: `${a.desc}　审批：${a.approval}`, tag: '待确认', okText: a.approval.startsWith('免审') ? '确认执行' : '提交审批' });
  if (!ok) return;
  done[a.id] = '已提交（演示）';
  message.success('演示数据：已提交，不会真的执行');
}

/* 对它提问：当前页浮层 */
const askOpen = ref(false);
const askInitial = ref('');
const askSugs = computed(() => (meta.value && row.value ? [
  ...(qaLive.value ? ['对象数量'] : []),
  ...metrics.value.slice(0, 3).map((m) => `${row.value!.title}的${m.name}是多少？`),
  ...meta.value.sampleQuestions.slice(0, 2),
] : []));
const askScope = computed<AskScope>(() => ({
  kind: typeId.value, label: `${meta.value?.name ?? ''} ${row.value?.title ?? ''}`, conds: [], ids: [id.value],
  single: { kind: typeId.value, id: id.value, title: row.value?.title ?? '' },
}));
const askOff = computed(() => readonly.value && !pv.active.value);
function ask(q = '') { askInitial.value = q; askOpen.value = true; }
</script>

<template>
  <div v-if="!typeId || !id" class="panel od-missing">
    <a-empty description="没有指定对象，请从对象搜索里选择一个对象"><a-button type="primary" @click="goBack">去对象搜索</a-button></a-empty>
  </div>
  <div v-else-if="loading" class="panel od-missing"><a-spin /></div>
  <div v-else-if="loadError" class="panel od-missing"><a-alert type="error" :message="loadError" show-icon /><a-button @click="goBack">返回对象搜索</a-button></div>
  <div v-else-if="!meta" class="panel od-missing"><a-empty :description="`没有这个对象类型：${typeId}`"><a-button @click="goBack">返回对象搜索</a-button></a-empty></div>
  <div v-else-if="got && !got.bound" class="panel od-missing"><a-empty :description="`未接入数据：「${meta.name}」还没有绑定数据表`"><a-button @click="goBack">返回对象搜索</a-button></a-empty></div>
  <div v-else-if="got?.denied" class="panel od-missing">
    <a-empty :description="pv.active.value ? `${pv.userName.value} 无权查看这个${meta.name}（${pv.scopeLabel(meta.name)}）` : `这个${meta.name}不在你的数据范围内`"><a-button @click="goBack">返回对象搜索</a-button></a-empty>
  </div>
  <div v-else-if="!row" class="panel od-missing"><a-empty :description="`没有找到${meta.name} ${id}`"><a-button @click="goBack">返回对象搜索</a-button></a-empty></div>
  <div v-else class="od">
    <div class="panel od-head">
      <a class="od-back t-12" @click="goBack"><LeftOutlined /> {{ backLabel }}</a>
      <div class="od-title">
        <span class="t-20 fw-600">{{ row.title }}</span>
        <a-tag class="od-kind">{{ meta.name }}</a-tag>
        <span v-if="currentApiContext().ver" class="c-3 t-12" title="按该版本定义查询当前数据">当前实例</span>
        <span class="od-brief od-ell" :title="prominent.map((p) => `${p.name} ${val(p)}`).join('　')">
          <span v-for="p in prominent" :key="p.key"><span class="c-2">{{ p.name }}</span> <span class="num">{{ val(p) }}</span></span>
        </span>
        <span v-if="pv.active.value" class="od-cut t-12 c-2">{{ pv.userName.value }} 的{{ pv.scopeLabel(meta.name) }}</span>
      </div>
    </div>

    <div class="od-row">
      <div class="panel od-main">
        <a-tabs v-model:active-key="tab" class="od-tabs">
          <a-tab-pane key="overview" tab="概览" />
          <a-tab-pane key="related" tab="关联对象" />
        </a-tabs>
        <div class="od-body">
          <template v-if="tab === 'overview'">
            <div v-if="metrics.length" class="od-sec">
              <div class="t-16 fw-600 od-sec-title">相关指标</div>
              <div class="od-mets">
                <a-tooltip v-for="m in metrics" :key="m.id" :title="askOff ? reason : '在当前页提问，回答带口径'">
                  <a-button size="small" :disabled="askOff" @click="ask(`${row.title}的${m.name}是多少？`)">{{ m.name }}<span v-if="m.unit" class="c-3">（{{ m.unit }}）</span></a-button>
                </a-tooltip>
              </div>
            </div>
            <div v-for="g in groups" :key="g.title" class="od-sec">
              <div class="t-16 fw-600 od-sec-title">{{ g.title }}</div>
              <a-descriptions bordered size="small" :column="3" :label-style="{ width: '120px' }">
                <a-descriptions-item v-for="p in g.fields" :key="p.key" :label="propLabel(p)">
                  <a-tooltip v-if="p.desc" :title="p.desc"><span :class="{ 'c-3': p.sensitive }">{{ val(p) }}</span></a-tooltip>
                  <span v-else :class="{ 'c-3': p.sensitive }">{{ val(p) }}</span>
                </a-descriptions-item>
              </a-descriptions>
            </div>
          </template>
          <div v-else class="od-rels">
            <a-alert v-if="relatedError" type="error" :message="relatedError" show-icon />
            <a-spin v-if="relatedLoading" />
            <a-empty v-else-if="!relatedError && !rels.length" description="这个类型没有已发布的关系" />
            <div v-for="g in rels" :key="g.link.id" class="od-rel">
              <div class="od-rel-head"><span class="fw-600">{{ g.link.label }} <span class="c-3 t-12">{{ typeNames[g.link.other] ?? g.link.other }}</span></span>
                <a v-if="!qaLive && g.state === 'ok' && g.total > g.items.length" class="t-12" @click="moreRel(g)">全部 {{ g.total }} 个</a></div>
              <div v-for="r in g.items" :key="r.id" class="od-rel-row"><a class="od-ell" @click="openRel(g, r)">{{ r.title }}</a><span class="c-3 num t-12">{{ r.id }}</span></div>
              <div v-if="!g.items.length" class="od-rel-deny t-12 c-3">{{ relNote[g.state] }}</div>
              <AppPagination v-if="qaLive && g.state === 'ok' && g.total" :page="g.page || 1" :size="REL_SIZE" :total="g.total" variant="simple" :empty="false" :inert="relatedLoading || undefined" @update:page="(p: number) => relatedPage(g, p)" />
            </div>
          </div>
        </div>
      </div>

      <div class="od-side">
        <div class="panel">
          <div class="panel-head"><span class="t-16 fw-600">可执行动作</span></div>
          <div class="od-acts">
            <div v-if="!actions.length" class="c-3 t-12 od-act">这个类型没有已发布的动作</div>
            <div v-for="a in actions" :key="a.id" class="od-act">
              <a-tooltip :title="a.desc" placement="left"><span class="od-act-main od-ell">{{ a.name }}</span></a-tooltip>
              <span v-if="done[a.id]" class="c-3 od-act-state">{{ done[a.id] }}</span>
              <a-tooltip v-else :title="actOff || undefined"><a-button size="small" :disabled="!!actOff" @click="run(a)">发起</a-button></a-tooltip>
            </div>
          </div>
        </div>
        <div class="panel">
          <div class="panel-head"><span class="t-16 fw-600">对它提问</span></div>
          <div class="od-asks">
            <a-tooltip v-for="q in askSugs" :key="q" :title="askOff ? reason : undefined">
              <a-button class="od-ask" :disabled="askOff" @click="ask(q)">{{ q }}</a-button>
            </a-tooltip>
            <a-tooltip :title="askOff ? reason : undefined">
              <a-button type="link" class="od-ask-free" :disabled="askOff" @click="ask()"><MessageOutlined /> 自己输入问题</a-button>
            </a-tooltip>
          </div>
        </div>
      </div>
    </div>

    <ObjAskModal v-model:open="askOpen" :scope="askScope" :suggestions="askSugs" :initial="askInitial" />
  </div>
</template>

<style scoped>
.od { display: flex; flex-direction: column; gap: var(--gap); height: 100%; min-height: 0; }
.od-missing { height: 100%; justify-content: center; }
.od-head { padding: 12px 16px; gap: 4px; flex: none; }
.od-back { align-self: flex-start; }
.od-title { display: flex; align-items: center; gap: 12px; }
.od-kind { margin: 0; }
.od-cut { margin-left: auto; }
.od-brief { flex: 0 1 auto; min-width: 0; white-space: nowrap; }
.od-brief > span + span { margin-left: 16px; }
.od-row { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.od-main { flex: 1; min-width: 0; }
.od-tabs { flex: none; padding: 0 16px; }
.od-tabs :deep(.ant-tabs-nav) { margin: 0; }
.od-body { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; padding: 12px 16px; }
.od-sec + .od-sec { margin-top: 16px; }
.od-sec-title { margin-bottom: 8px; }
.od-mets { display: flex; flex-wrap: wrap; gap: 8px; }
.od-rels { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-content: start; }
.od-rel { border: var(--border); border-radius: var(--radius); padding: 4px 12px 8px; }
.od-rel-head { display: flex; justify-content: space-between; align-items: center; height: 36px; border-bottom: var(--border); }
.od-rel-deny { padding: 8px 0; }
.od-rel-row { display: flex; align-items: center; gap: 12px; height: 32px; }
.od-ell { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.od-side { width: 340px; flex: none; display: flex; flex-direction: column; gap: var(--gap); }
.od-acts { padding: 4px 0; }
.od-act { display: flex; align-items: center; gap: 12px; padding: 8px 16px; }
.od-act-main { flex: 1; min-width: 0; }
.od-act-state { flex: none; }
.od-asks { padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; align-items: stretch; }
.od-ask { text-align: left; white-space: normal; height: auto; padding: 4px 12px; }
.od-ask-free { align-self: flex-start; padding: 0; }
</style>
