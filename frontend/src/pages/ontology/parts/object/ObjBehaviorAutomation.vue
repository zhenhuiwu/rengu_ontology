<script setup lang="ts">
/**
 * 对象类型 · 行为 · 自动化：作用在该对象上的规则（rules.ts，按 objKind）与该对象的事件类型，
 * 下方是这些规则近 7 天的命中（与 规则与自动化 页的命中记录同源）。
 * 规则在“规则与自动化”页维护、走自己的变更单；这里只读汇总。
 * URL：?aupage= 命中列表页码，?aurule= 命中列表按规则筛选。
 */
import { computed } from 'vue';
import { useRoute, useRouter, RouterLink } from 'vue-router';
import ObjRef from '@/components/ObjRef.vue';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { paginate } from '@/mock/paginate';
import { ACTION_TYPES, EVENT_TYPES, eventTypeById, subscribersOf } from '@/mock/actionEvent';
import { RULE_DEFS, hitsOf, stagedOf, type Hit, type RuleDef } from '@/mock/rules';
import { OBJ_TYPE_KIND, OBJ_TYPE_NAME, SINCE_7D } from '@/mock/lifecycle';
import { useDraftView, draftOf } from '../useDraftView';
import { CHANGE_REQUESTS } from '@/mock/changes';
import { InfoCircleOutlined } from '@ant-design/icons-vue';

const props = defineProps<{ objectId: string; readonly: boolean }>();
const route = useRoute();
const router = useRouter();
const { readonly: histRo, reason } = useDraftView('object', () => props.objectId);

const kind = computed(() => OBJ_TYPE_KIND[props.objectId]);
const objName = computed(() => OBJ_TYPE_NAME[props.objectId] ?? props.objectId);
const rules = computed(() => (kind.value ? RULE_DEFS.filter((r) => r.objKind === kind.value) : []));
const events = computed(() => EVENT_TYPES.filter((e) => e.object === objName.value));
const hits7 = (id: string) => hitsOf(id).filter((h) => h.at >= SINCE_7D);
const actName = (id: string) => ACTION_TYPES.find((a) => a.id === id)?.name ?? id;
const fmt = (n: number) => n.toLocaleString('zh-CN');
/** 规则状态按所在变更单推导：审核中 → 橙色“审核中”，只有草稿 → 灰色“草稿中”，其余用规则自身状态（灰） */
function ruleStatus(r: RuleDef): { text: string; badge: 'warning' | 'default'; cr?: string } {
  const d = draftOf('rule', r.id);
  if (!d) return { text: r.status === '变更中' ? '草稿中' : r.status, badge: 'default' };
  const st = CHANGE_REQUESTS.find((c) => c.id === d.id)?.status;
  return st === '审核中' ? { text: '审核中', badge: 'warning', cr: d.id } : { text: '草稿中', badge: 'default', cr: d.id };
}
const pendingOf = (id: string) => stagedOf(id).filter((a) => a.status === '待确认').length;
const totals = computed(() => ({
  hits: rules.value.reduce((s, r) => s + hits7(r.id).length, 0),
  alerts: rules.value.reduce((s, r) => s + hits7(r.id).filter((h) => h.alertId && h.result !== '已去重').length, 0),
  pending: rules.value.reduce((s, r) => s + pendingOf(r.id), 0),
}));

const ruleCols = [
  { title: '规则', key: 'name', width: 200, ellipsis: true },
  { title: '负责人', key: 'owner', width: 72 },
  { title: '状态', key: 'status', width: 84 },
  { title: '触发', key: 'trigger', width: 170, ellipsis: true },
  { title: '条件', key: 'expr', ellipsis: true },
  { title: '输出', key: 'output', width: 56 },
  { title: '自动暂存动作', key: 'stage', width: 130, ellipsis: true },
  { title: '确认人', key: 'confirmer', width: 140, ellipsis: true },
  { title: '近 7 天命中', key: 'hits', width: 96, align: 'right' as const },
];
const evCols = [
  { title: '事件类型', key: 'name', ellipsis: true },
  { title: '来源', key: 'source', width: 140 },
  { title: '订阅', key: 'subs', width: 60, align: 'right' as const },
  { title: '近 7 天', key: 'weekly', width: 90, align: 'right' as const },
];

/* 命中列表 */
const ruleFilter = computed({
  get: () => (rules.value.some((r) => r.id === route.query.aurule) ? (route.query.aurule as string) : '全部'),
  set: (v: string) => router.replace({ query: { ...route.query, aurule: v === '全部' ? undefined : v, aupage: undefined } }),
});
const pq = usePagedQuery({ defaultSize: 10, prefix: 'au', filters: [() => props.objectId] });
const hitRows = computed(() => (ruleFilter.value === '全部' ? rules.value.flatMap((r) => hits7(r.id)) : hits7(ruleFilter.value)).sort((a, b) => b.at.localeCompare(a.at)));
const paged = computed(() => paginate(hitRows.value, { page: pq.page.value, size: pq.size.value }));
const hitCols = [
  { title: '命中时间', key: 'at', width: 170 },
  { title: '规则', key: 'rule', width: 230 },
  { title: '对象', key: 'obj' },
  { title: '值 / 阈值', key: 'value', width: 300, ellipsis: true },
  { title: '结果', key: 'result', width: 110 },
  { title: '告警', key: 'alert', width: 170 },
];
const ruleName = (id: string) => RULE_DEFS.find((r) => r.id === id)?.name ?? id;
const resultBadge = (s: string) => (s === '待处理' ? 'warning' : 'default');
const triggerText = (r: RuleDef) => (r.trigger.kind === '事件' ? `事件 · ${eventTypeById(r.trigger.eventId!)?.name ?? r.trigger.eventId}` : `定时 · ${r.trigger.text}`);
</script>

<template>
  <div class="ot-body bu">
    <template v-if="rules.length || events.length">
      <div class="bu-bar">
        <span class="t-14 fw-600 c-1">作用在{{ objName }}上的规则 {{ rules.length }} · 事件类型 {{ events.length }}</span>
        <a-tooltip :title="`规则在“规则与自动化”页维护、走自己的变更单，这里只读汇总${histRo ? `（${reason}）` : ''}`"><InfoCircleOutlined class="c-3 t-12" /></a-tooltip>
        <span class="t-12 c-2">近 7 天命中 <span class="num">{{ fmt(totals.hits) }}</span> 次，产生告警 <span class="num">{{ fmt(totals.alerts) }}</span> 条，暂存动作待确认
          <span class="num" :class="totals.pending ? 'tl-attention' : ''">{{ totals.pending }}</span></span>
        <span class="bu-sp" />
        <RouterLink class="t-14" to="/build/ontology/rules">去规则与自动化</RouterLink>
      </div>
      <div class="bu-top">
        <div class="bu-rules">
          <a-table size="small" :columns="ruleCols" :data-source="rules" row-key="id" :pagination="false" :locale="{ emptyText: `还没有作用在${objName}上的规则` }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'name'">
                <RouterLink :to="{ path: '/build/ontology/rules', query: { rule: record.id } }" :title="`${record.id} ${record.name} · v${record.version} · ${record.level}级`"><span class="num">{{ record.id }}</span> {{ record.name }}</RouterLink>
              </template>
              <template v-else-if="column.key === 'owner'">{{ record.owner }}</template>
              <template v-else-if="column.key === 'status'"><a-tooltip :title="ruleStatus(record).cr ? `所在变更单 ${ruleStatus(record).cr}` : undefined"><a-badge :status="ruleStatus(record).badge" :text="ruleStatus(record).text" /></a-tooltip></template>
              <template v-else-if="column.key === 'trigger'">
                <RouterLink v-if="record.trigger.kind === '事件'" :to="{ path: '/build/ontology/event-types', query: { event: record.trigger.eventId } }">{{ triggerText(record) }}</RouterLink>
                <span v-else class="c-2">{{ triggerText(record) }}</span>
              </template>
              <template v-else-if="column.key === 'expr'"><span class="c-2 bu-expr" :title="record.expr">{{ record.expr }}</span></template>
              <template v-else-if="column.key === 'output'">{{ record.output }}</template>
              <template v-else-if="column.key === 'stage'">
                <RouterLink v-if="record.autoStage.enabled" :to="{ path: '/build/ontology/action-types', query: { action: record.autoStage.actionTypeId } }">{{ actName(record.autoStage.actionTypeId) }}</RouterLink>
                <span v-else class="c-3">不暂存</span>
              </template>
              <template v-else-if="column.key === 'confirmer'">
                <span v-if="record.autoStage.enabled" class="bu-nowrap">{{ record.autoStage.confirmer }}<template v-if="pendingOf(record.id)"> · <RouterLink :to="{ path: '/use/ops/actions', query: { tab: 'pending' } }"><span class="tl-attention">待确认 {{ pendingOf(record.id) }}</span></RouterLink></template></span>
                <span v-else class="c-3">--</span>
              </template>
              <template v-else-if="column.key === 'hits'"><span class="num">{{ fmt(hits7(record.id).length) }}</span></template>
            </template>
          </a-table>
        </div>
        <div class="bu-events">
          <a-table size="small" :columns="evCols" :data-source="events" row-key="id" :pagination="false" :locale="{ emptyText: `还没有${objName}的事件类型` }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'name'">
                <RouterLink :to="{ path: '/build/ontology/event-types', query: { event: record.id } }" :title="record.api">{{ record.name }}</RouterLink>
              </template>
              <template v-else-if="column.key === 'source'"><span class="c-2">{{ record.source }}{{ record.ruleId ? ` · ${record.ruleId}` : '' }}</span></template>
              <template v-else-if="column.key === 'subs'"><span class="num">{{ subscribersOf(record).length }}</span></template>
              <template v-else-if="column.key === 'weekly'"><span class="num">{{ fmt(record.weekly) }}</span></template>
            </template>
          </a-table>
        </div>
      </div>

      <div class="bu-bar">
        <span class="t-14 fw-600 c-1">近 7 天命中</span>
        <a-tooltip title="与“规则与自动化”页的命中记录同源；被去重的命中不产生告警"><InfoCircleOutlined class="c-3 t-12" /></a-tooltip>
        <span class="bu-sp" />
        <a-select v-model:value="ruleFilter" size="small" class="bu-sel" :options="[{ value: '全部', label: '全部规则' }, ...rules.map((r) => ({ value: r.id, label: `${r.id} ${r.name}` }))]" />
      </div>
      <div class="bu-hits">
        <a-table size="small" :columns="hitCols" :data-source="paged.items" row-key="id" :pagination="false" :locale="{ emptyText: rules.length ? '近 7 天没有命中' : `没有作用在${objName}上的规则，不会产生命中` }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'at'"><span class="num">{{ record.at }}</span></template>
            <template v-else-if="column.key === 'rule'"><RouterLink :to="{ path: '/build/ontology/rules', query: { rule: record.ruleId } }"><span class="num">{{ record.ruleId }}</span> {{ ruleName(record.ruleId) }}</RouterLink></template>
            <template v-else-if="column.key === 'obj'">
              <ObjRef :kind="(record as Hit).objKind" :id="record.objId">{{ record.objLabel }}</ObjRef>
              <span v-if="record.storeLabel && record.objKind !== 'store'" class="c-3"> · {{ record.storeLabel }}</span>
            </template>
            <template v-else-if="column.key === 'value'"><span :title="`${record.value} / ${record.threshold}`"><span class="num">{{ record.value }}</span><span class="c-3"> / {{ record.threshold }}</span></span></template>
            <template v-else-if="column.key === 'result'"><a-badge :status="resultBadge(record.result)" :text="record.result" /></template>
            <template v-else-if="column.key === 'alert'">
              <RouterLink v-if="record.alertId && record.result !== '已去重'" class="num" :to="{ path: '/use/ops/alerts', query: { alert: record.alertId } }">{{ record.alertId }}</RouterLink>
              <span v-else class="c-3">—</span>
            </template>
          </template>
        </a-table>
      </div>
      <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="paged.total" :default-size="pq.defaultSize" :empty="false" />
    </template>
    <div v-else class="bu-empty">
      <div class="t-14 c-2">还没有作用在{{ objName }}上的规则或事件类型。</div>
      <div class="t-12 c-3">在“规则与自动化”页新建规则、对象选“{{ objName }}”后会出现在这里。</div>
      <RouterLink class="t-14" to="/build/ontology/rules">去规则与自动化</RouterLink>
    </div>
  </div>
</template>

<style scoped>
.bu-bar { display: flex; align-items: center; gap: 12px; flex: none; }
.bu-sp { flex: 1; }
.bu-top { display: flex; gap: 16px; flex: none; }
.bu-rules { flex: 3; min-width: 0; }
.bu-events { flex: 1.3; min-width: 0; }
.bu-expr { display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden; }
.bu-sel { width: 300px; }
.bu-hits { flex: 1; min-height: 0; overflow: auto; }
.bu-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; }
.bu-nowrap { white-space: nowrap; }
</style>
