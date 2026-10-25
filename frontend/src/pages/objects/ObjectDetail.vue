<script setup lang="ts">
/**
 * 对象详情（/use/objects/detail?kind=store&id=SH0047&tab=overview）。参考 Object View。
 * 数据全部来自 objectRegistry.resolveObject（与抽屉同一份），补充数据见 mock/objects-qa.ts。
 * 页签：概览 / 关联对象 / 时间线 / 动作记录；右栏：可执行动作（公共确认卡）+ 对它提问（当前页浮层）。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router';
import { message } from 'ant-design-vue';
import { LeftOutlined, MessageOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import StatusTimeline from '@/components/StatusTimeline.vue';
import ObjRef from '@/components/ObjRef.vue';
import { resolveObject } from '@/components/objectRegistry';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { paginate } from '@/mock/paginate';
import {
  KIND_LABEL, FIELD_GROUPS, OBJ_CUTOFF, caliberOf, storeExtraKpi, relationGroups, extraTimeline, actionRecords, objActions, quickQuestions, objType,
  type AskScope, type ObjAction,
} from '@/mock/objects-qa';
import ObjAskModal from './parts/ObjAskModal.vue';

const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const { readonly, reason } = useReadonly();
const qs = (k: string) => { const v = route.query[k]; return String(Array.isArray(v) ? v[0] ?? '' : v ?? ''); };
const patch = (p: Record<string, string | null>) => {
  const q: LocationQueryRaw = { ...route.query };
  Object.entries(p).forEach(([k, v]) => { if (v == null || v === '') delete q[k]; else q[k] = v; });
  router.replace({ query: q });
};

const kind = computed(() => qs('kind') || 'store');
const id = computed(() => qs('id') || 'SH0047');
const view = computed(() => resolveObject(kind.value, id.value));
const TABS = [{ key: 'overview', title: '概览' }, { key: 'related', title: '关联对象' }, { key: 'timeline', title: '时间线' }, { key: 'actions', title: '动作记录' }];
const tab = computed({ get: () => qs('tab') || 'overview', set: (v: string) => patch({ tab: v === 'overview' ? null : v, apage: null, asize: null }) });

/* 面包屑：回到搜索结果，保留筛选 */
const back = computed(() => {
  let q: LocationQueryRaw = {};
  try { q = JSON.parse(sessionStorage.getItem('obj-search:last') || '{}'); } catch { q = {}; }
  const fromType = String(q.type ?? 'store');
  if (fromType !== kind.value && kind.value in { store: 1, product: 1, order: 1 }) q = kind.value === 'store' ? {} : { type: kind.value };
  return q;
});
const backLabel = computed(() => {
  const n = Object.keys(back.value).filter((k) => !['type', 'page', 'size'].includes(k)).length;
  return `对象搜索 · ${objType(String(back.value.type ?? 'store')).label}${n ? `（${n} 个筛选）` : ''}`;
});
function goBack() { router.push({ path: '/use/objects/search', query: back.value }); }

/* 概览：属性分组 + 关键指标（带口径版本） */
const groups = computed(() => {
  const v = view.value; if (!v) return [];
  const defs = FIELD_GROUPS[v.kind] ?? [];
  const used = new Set<string>();
  const out = defs.map((g) => ({ title: g.title, fields: g.labels.map((l) => v.fields.find((f) => f.label === l)).filter((f): f is { label: string; value: string } => !!f && !used.has(f.label) && !!used.add(f.label)) }));
  const rest = v.fields.filter((f) => !used.has(f.label));
  if (rest.length) out.push({ title: '其他', fields: rest });
  return out.filter((g) => g.fields.length);
});
const kpis = computed(() => {
  const v = view.value; if (!v) return [];
  const list = (v.tabs ?? []).flatMap((t) => t.kpis ?? []).map((k) => ({ ...k, caliber: caliberOf(k.label) }));
  if (v.kind === 'store') list.unshift(storeExtraKpi(v.id));
  return list.slice(0, 5);
});

/* 关联对象：按关系类型分组，每组各自分页 */
const rels = computed(() => (view.value ? relationGroups(view.value.kind, view.value.id, view.value.related ?? []) : []));
const relPage = reactive<Record<string, number>>({});
watch(() => `${kind.value}:${id.value}`, () => { Object.keys(relPage).forEach((k) => delete relPage[k]); });
const REL_SIZE = 8;
const relItems = (key: string, items: unknown[]) => paginate(items, { page: relPage[key] ?? 1, size: REL_SIZE }).items as { kind: string; id: string; label: string; note: string }[];

/* 时间线 */
const timeline = computed(() => (view.value ? extraTimeline(view.value.kind, view.value.id, view.value.timeline ?? []) : []));
const tlPage = ref(1);
watch(() => `${kind.value}:${id.value}`, () => { tlPage.value = 1; });
const TL_SIZE = 8;
const tlItems = computed(() => paginate(timeline.value, { page: tlPage.value, size: TL_SIZE }).items);

/* 动作记录 */
const records = computed(() => (view.value ? actionRecords(view.value.kind, view.value.id) : []));
const apq = usePagedQuery({ defaultSize: 14, prefix: 'a', filters: computed(() => `${kind.value}:${id.value}`) });
const recPaged = computed(() => paginate(records.value, { page: apq.page.value, size: apq.size.value }));
const recCols = [
  { title: '记录号', dataIndex: 'id', key: 'id', width: 180 },
  { title: '动作', dataIndex: 'action', key: 'action' },
  { title: '发起', dataIndex: 'by', key: 'by', width: 140 },
  { title: '审批', dataIndex: 'approver', key: 'approver', width: 100 },
  { title: '时间', dataIndex: 'time', key: 'time', width: 168 },
  { title: '结果', dataIndex: 'result', key: 'result', width: 150 },
];
const resTone = (r: string) => (r === '失败' ? 'tl-failed' : r === '结果未知' ? 'tl-attention' : 'c-2');

/* 可执行动作：公共确认卡；确认卡打开时显示“待确认”，取消/关闭恢复“发起”，提交后“待审批/执行中” */
const actions = computed(() => (view.value ? objActions(view.value.kind, view.value.id) : []));
const pending = ref('');
const done = reactive<Record<string, string>>({});
watch(() => `${kind.value}:${id.value}`, () => { pending.value = ''; Object.keys(done).forEach((k) => delete done[k]); });
async function run(a: ObjAction) {
  if (readonly.value || a.blocked || !view.value) return;
  pending.value = a.key;
  const ok = await confirm.ask({
    title: a.name, target: `${view.value.kindLabel} ${view.value.title}（${view.value.code}）`,
    fields: a.fields, impact: a.impact, irreversible: a.irreversible, tag: '待确认',
    okText: a.approver ? '提交审批' : '确认执行',
  });
  pending.value = '';
  if (!ok) return;
  done[a.key] = a.approver ? '待审批' : '执行中';
  message.success(a.approver ? `已提交，等待 ${a.approver} 审批` : '已开始执行');
}
const stateOf = (a: ObjAction) => done[a.key] || (pending.value === a.key ? '待确认' : '');

/* 对它提问：当前页浮层 */
const askOpen = ref(false);
const askInitial = ref('');
const askSugs = computed(() => (view.value ? quickQuestions(view.value.kind, view.value.title) : []));
const askScope = computed<AskScope>(() => ({
  kind: kind.value, label: `${view.value?.kindLabel ?? ''} ${view.value?.title ?? ''}`, conds: [], ids: [id.value],
  single: { kind: kind.value, id: id.value, title: view.value?.title ?? '' },
}));
function ask(q = '') { askInitial.value = q; askOpen.value = true; }
</script>

<template>
  <div v-if="!view" class="panel od-missing">
    <a-empty :description="`没有找到对象 ${kind}:${id}`"><a-button @click="goBack">返回对象搜索</a-button></a-empty>
  </div>
  <div v-else class="od">
    <div class="panel od-head">
      <a class="od-back t-12" @click="goBack"><LeftOutlined /> {{ backLabel }}</a>
      <div class="od-title">
        <span class="t-20 fw-600">{{ view.title }}</span>
        <a-tag class="od-kind">{{ view.kindLabel }}</a-tag>
        <span v-if="view.status" class="c-2">{{ view.status.text }}</span>
        <span class="c-3 num">{{ view.code }}</span>
        <span class="od-cut t-12 c-3">数据截至 <span class="num">{{ OBJ_CUTOFF }}</span></span>
      </div>
      <div class="od-brief">
        <span v-for="b in view.brief" :key="b.label"><span class="c-3">{{ b.label }}</span> {{ b.value }}</span>
      </div>
    </div>

    <div class="od-row">
      <div class="panel od-main">
        <a-tabs v-model:active-key="tab" class="od-tabs">
          <a-tab-pane v-for="t in TABS" :key="t.key" :tab="t.title" />
        </a-tabs>
        <div class="od-body">
          <template v-if="tab === 'overview'">
            <div v-if="kpis.length" class="od-sec">
              <div class="t-16 fw-600 od-sec-title">关键指标</div>
              <div class="od-kpis">
                <div v-for="k in kpis" :key="k.label" class="od-kpi">
                  <div class="c-3">{{ k.label }}</div>
                  <div><span class="t-20 fw-600 num">{{ k.value }}</span> <span class="c-3">{{ k.unit }}</span></div>
                  <div class="t-12" :class="k.tone === 'down' ? 'c-error' : 'c-3'">{{ k.compare || ' ' }}</div>
                  <div class="t-12 c-3">口径：{{ k.caliber || '未登记口径' }}</div>
                </div>
              </div>
            </div>
            <div v-for="g in groups" :key="g.title" class="od-sec">
              <div class="t-16 fw-600 od-sec-title">{{ g.title }}</div>
              <a-descriptions bordered size="small" :column="3" :label-style="{ width: '120px' }">
                <a-descriptions-item v-for="f in g.fields" :key="f.label" :label="f.label">{{ f.value }}</a-descriptions-item>
              </a-descriptions>
            </div>
          </template>

          <div v-else-if="tab === 'related'" class="od-rels">
            <a-empty v-if="!rels.length" description="暂无关联对象" />
            <div v-for="g in rels" :key="g.key" class="od-rel">
              <div class="od-rel-head"><span class="fw-600">{{ g.title }}</span>
                <AppPagination v-model:page="relPage[g.key]" variant="simple" :total="g.items.length" :size="REL_SIZE" :empty="false" unit="个" /></div>
              <div v-for="it in relItems(g.key, g.items)" :key="it.kind + it.id" class="od-rel-row">
                <ObjRef :kind="it.kind" :id="it.id">{{ it.label }}</ObjRef>
                <span class="c-3 od-ell">{{ it.note }}</span>
              </div>
            </div>
          </div>

          <template v-else-if="tab === 'timeline'">
            <div class="od-tl"><StatusTimeline :items="tlItems" /></div>
            <AppPagination v-model:page="tlPage" variant="simple" :total="timeline.length" :size="TL_SIZE" unit="条" empty-text="暂无记录" />
          </template>

          <template v-else>
            <template v-if="records.length">
              <div class="paged-scroll">
                <a-table :columns="recCols" :data-source="recPaged.items" row-key="id" size="small" :pagination="false">
                  <template #bodyCell="{ column, record }">
                    <span v-if="column.key === 'id' || column.key === 'time'" class="num">{{ record[column.key] }}</span>
                    <span v-else-if="column.key === 'result'" :class="resTone(record.result)" class="od-nowrap">{{ record.result === '结果未知' ? '结果未知 · 待对账' : record.result }}</span>
                  </template>
                </a-table>
              </div>
              <AppPagination v-model:page="apq.page.value" v-model:size="apq.size.value" variant="full" :total="records.length" :default-size="apq.defaultSize" />
            </template>
            <a-empty v-else description="暂无动作记录" />
          </template>
        </div>
      </div>

      <div class="od-side">
        <div class="panel">
          <div class="panel-head"><span class="t-16 fw-600">可执行动作</span></div>
          <div class="od-acts">
            <div v-for="a in actions" :key="a.key" class="od-act" :class="{ hl: pending === a.key }">
              <div class="od-act-main">
                <div>{{ a.name }}</div>
                <div class="t-12 c-3 od-ell">{{ a.blocked || a.desc }}</div>
              </div>
              <span v-if="stateOf(a)" class="c-3 od-act-state">{{ stateOf(a) }}</span>
              <a-tooltip v-else :title="readonly ? reason : a.blocked">
                <a-button size="small" :disabled="readonly || !!a.blocked" @click="run(a)">发起</a-button>
              </a-tooltip>
            </div>
          </div>
        </div>
        <div class="panel">
          <div class="panel-head"><span class="t-16 fw-600">对它提问</span></div>
          <div class="od-asks">
            <a-tooltip v-for="q in askSugs" :key="q" :title="readonly ? reason : undefined">
              <a-button class="od-ask" :disabled="readonly" @click="ask(q)">{{ q }}</a-button>
            </a-tooltip>
            <a-tooltip :title="readonly ? reason : undefined">
              <a-button type="link" class="od-ask-free" :disabled="readonly" @click="ask()"><MessageOutlined /> 自己输入问题</a-button>
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
.od-brief { display: flex; gap: 24px; flex-wrap: wrap; }
.od-row { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.od-main { flex: 1; }
.od-tabs { flex: none; padding: 0 16px; }
.od-tabs :deep(.ant-tabs-nav) { margin: 0; }
.od-body { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 12px 16px; }
.od-sec + .od-sec { margin-top: 16px; }
.od-sec-title { margin-bottom: 8px; }
.od-kpis { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); border: var(--border); border-radius: var(--radius); }
.od-kpi { padding: 12px 16px; display: flex; flex-direction: column; gap: 2px; }
.od-kpi + .od-kpi { border-left: var(--border); }
.od-rels { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-content: start; }
.od-rel { border: var(--border); border-radius: var(--radius); padding: 4px 12px 8px; }
.od-rel-head { display: flex; justify-content: space-between; align-items: center; height: 36px; border-bottom: var(--border); }
.od-rel-row { display: flex; align-items: center; gap: 12px; height: 32px; }
.od-ell { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.od-tl { flex: 1; min-height: 0; }
.od-side { width: 340px; flex: none; display: flex; flex-direction: column; gap: var(--gap); }
.od-acts { padding: 4px 0; }
.od-act { display: flex; align-items: center; gap: 12px; padding: 8px 16px; }
.od-act.hl { background: var(--brand-bg); }
.od-act-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.od-act-state { flex: none; }
.od-nowrap { white-space: nowrap; }
.od-asks { padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; align-items: stretch; }
.od-ask { text-align: left; white-space: normal; height: auto; padding: 4px 12px; }
.od-ask-free { align-self: flex-start; padding: 0; }
</style>
