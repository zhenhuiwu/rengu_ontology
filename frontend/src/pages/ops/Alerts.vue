<script setup lang="ts">
/**
 * 告警处理（动态本体）：左侧告警列表（分页），右侧当前告警详情：触发依据、处理记录、可执行动作。
 * 当前选中写进 ?alert=，状态页签写进 ?status=。对象点开走公共抽屉，不跳转。
 * 开关 dynamic 走真接口时（src/api/dynamic.ts）：列表 / 计数 / 详情 / 处理记录 / 处理人下拉都取后台，
 * 认领、批量认领、转派、关闭、批量关闭、发起动作一一调用对应接口，成功后用返回的详情刷新；否则保持原 mock 逻辑。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import ObjRef from '@/components/ObjRef.vue';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import HoverCard from '@/components/HoverCard.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { ALERTS, RULES, METRICS, alertTimeline, type Alert, type AlertStatus } from '@/mock';
import { PEOPLE, alertDims, type AlertDim } from '@/mock/dynamic';
import { ACTIONS, ACTION_META, OBJ_KIND_LABEL } from '@/mock/actions';
import { useAppStore } from '@/stores/app';
import { isApiError } from '@/api/http';
import {
  dynamicApi, dynamicContextKey, getAlertMeta, getAlertFacets, listAlerts, getAlert, claimAlert, batchClaimAlerts, transferAlert, closeAlert, batchCloseAlerts,
  startAlertAction, listPeople, type AlertDto, type AlertDetailDto, type AlertRuleDto, type AlertTab, type AlertFacetsDto, type AlertCan,
} from '@/api/dynamic';

type LogItem = { time: string; text: string; who: string; abnormal?: boolean };
/** dims：通用维度值（演示数据里是所属门店）；objKindLabel：对象类型显示名（真接口由后台给，mock 取对象类型目录） */
type Row = Alert & { log: LogItem[]; actionRef?: string; actionStatus?: string; actionId?: string | null; dims: AlertDim[]; objKindLabel?: string; assigneeId?: string | null; canClose?: boolean; canClaim?: boolean; assignedToMe?: boolean; can?: AlertCan };
const REAL_API = dynamicApi.isReal();
/** 页面内的告警状态（模拟后台：认领、转派、关闭、发起动作都会改它并写处理记录） */
/** 关联动作取自动作中心同一份数据（mock/actions.ts，按来源告警匹配最近一条），两页的幂等键和状态一致 */
const LIST = reactive<Row[]>(REAL_API ? [] : ALERTS.map((a) => {
  const act = ACTIONS.find((x) => x.source.alertId === a.id);
  const log = alertTimeline(a);
  if (act && !(a.status === '已解决' || a.status === '已忽略')) log.push({ time: act.submittedAt, text: `发起动作「${act.action}」（${act.idemKey}），现为${act.status}`, who: act.initiator });
  log.sort((x, y) => (x.time < y.time ? -1 : 1));
  return { ...a, log, actionRef: act?.idemKey, actionStatus: act?.status, dims: alertDims(a) };
}));
/** 关联动作还没有结果时锁定转派和关闭 */
const IN_FLIGHT = ['待确认', '审批中', '执行中', '结果未知'];
/** 是否走真接口（开关 dynamic）；构建时确定 */
const live = computed(() => REAL_API);
const readFail = (e: unknown) => fail(e);
const app = useAppStore();
/** 当前处理人：mock 固定王敏；真接口时后台按 X-Demo-As 认人，前端取当前演示身份的姓名做同样的判断 */
const ME = computed(() => REAL_API ? app.user.name : '王敏');
let tick = 0;
const now = () => { tick += 1; return `2026-10-25 16:${String(30 + tick).padStart(2, '0')}:00`; };
const isClosed = (s: AlertStatus) => s === '已解决' || s === '已忽略';

const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const { readonly, reason: roReason } = useReadonly();

const TABS: { key: string; title: string; match: (s: AlertStatus) => boolean }[] = [
  { key: 'open', title: '待处理', match: (s) => s === '待处理' },
  { key: 'doing', title: '处理中', match: (s) => s === '处理中' },
  { key: 'closed', title: '已关闭', match: (s) => s === '已解决' || s === '已忽略' },
  { key: 'all', title: '全部', match: () => true },
];
const tab = computed({
  get: () => (route.query.status as string) || 'open',
  set: (v) => router.replace({ query: { ...route.query, status: v, alert: undefined, page: undefined } }),
});
const level = ref('全部');
const ruleId = ref('全部');
const kw = ref('');
/** 真接口分面筛选：对象类型、维度值（objKind:id 多选），选项和计数来自 GET /alerts/facets */
const objKind = ref('全部');
const dimSel = ref<string[]>([]);
/** 分页进 URL（?page=&size=），一屏 12 条；筛选变化回到第 1 页 */
const pq = usePagedQuery({ defaultSize: 10, filters: [level, ruleId, kw, objKind, dimSel] });
const page = pq.page;
const pageSize = pq.size;

/** 真接口状态：当前页、总数、页签计数、当前详情、处理人下拉；seen 记住看过的行（跨页勾选时批量操作用） */
const remote = reactive({ items: [] as Row[], total: 0, counts: { open: 0, doing: 0, closed: 0, all: 0 } as Record<string, number>, detail: null as (Row & { rule: AlertRuleDto | null; approver: string | null; recent7d: number; locked: boolean; actionParams: AlertDetailDto['actionParams'] }) | null, people: [] as { id: string; name: string }[] });
const seen = new Map<string, Row>();
const ruleOptions = ref<{ id: string; name: string }[]>([]);
const facets = ref<AlertFacetsDto | null>(null);
const listLoading = ref(false);
const toRow = (a: AlertDto): Row => ({
  ...a, dims: a.dims ?? [],
  actionRef: a.actionRef ?? undefined, actionStatus: a.actionStatus ?? undefined, log: [],
});
const toDetail = (d: AlertDetailDto) => ({
  ...toRow(d), rule: d.rule, approver: d.approver, recent7d: d.recent7d, locked: d.locked, can: d.can, actionParams: d.actionParams,
  log: d.log.map((l) => ({ time: l.time, text: l.text, who: l.who, abnormal: l.abnormal ?? undefined })),
});
const fail = (e: unknown) => { if ((e as Error)?.name !== 'DynamicContextChanged') message.error(isApiError(e) ? e.message || e.code : String(e)); };

const rows = computed(() => {
  if (live.value) return remote.items;
  const t = TABS.find((x) => x.key === tab.value) ?? TABS[3];
  return LIST.filter((a) =>
    t.match(a.status) &&
    (level.value === '全部' || a.level === level.value) &&
    (ruleId.value === '全部' || a.ruleId === ruleId.value) &&
    (!kw.value || a.id.includes(kw.value) || a.objLabel.includes(kw.value)),
  );
});

const total = computed(() => (live.value ? remote.total : rows.value.length));
const pageRows = computed(() => live.value ? remote.items : rows.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value));
/** 刚处理过的告警即使离开当前页签也继续显示在右侧，避免详情突然换成别的告警 */
const pinned = ref<string>();
const selectedId = computed(() => (route.query.alert as string) || pinned.value || pageRows.value[0]?.id);
const current = computed<Row | undefined>(() => live.value ? (remote.detail && remote.detail.id === selectedId.value ? remote.detail : undefined) : LIST.find((a) => a.id === selectedId.value) ?? pageRows.value[0]);
const rule = computed<AlertRuleDto | null>(() => (live.value ? remote.detail?.rule ?? null : current.value ? RULES[current.value.ruleId] : null));
const ruleVersion = (version: unknown) => version == null || version === '' ? '' : String(version).startsWith('v') ? String(version) : `v${version}`;
const triggerText = computed(() => {
  const t = rule.value?.trigger;
  if (typeof t === 'string') return t;
  if (!t) return '';
  return t.text || (t.kind === 'event' ? `事件触发${t.eventId ? ` · ${t.eventId}` : ''}` : t.kind === 'schedule' ? '定时触发' : t.kind === 'lifecycle' ? '生命周期触发' : t.kind || '');
});
const conditionText = computed(() => rule.value?.condition || rule.value?.expr || (rule.value?.conds ?? []).map((c) => c.expr || [c.field, c.op, typeof c.value === 'string' || typeof c.value === 'number' ? c.value : ''].filter(Boolean).join(' ')).filter(Boolean).join(rule.value?.logic === '或' ? ' 或 ' : ' 且 '));
const locked = computed(() => live.value ? !!remote.detail?.locked : !!current.value?.actionStatus && IN_FLIGHT.includes(current.value.actionStatus));
const metric = computed(() => (rule.value?.metric ? (live.value ? { id: rule.value.metric, name: rule.value.metric, version: '' } : METRICS.find((m) => m.id === rule.value!.metric)) : undefined));
/** 同一规则、同一对象近 7 天的触发次数（含本条） */
const recent = computed(() => {
  const a = current.value; if (!a) return 0;
  if (live.value) return remote.detail?.recent7d ?? 0;
  const end = new Date(a.triggeredAt.replace(' ', 'T')).getTime();
  return ALERTS.filter((x) => x.ruleId === a.ruleId && x.objId === a.objId &&
    end - new Date(x.triggeredAt.replace(' ', 'T')).getTime() < 7 * 864e5 &&
    new Date(x.triggeredAt.replace(' ', 'T')).getTime() <= end).length + (a.ruleId === 'R-012' ? 2 : 0);
});
const pick = (a: Alert) => (pinned.value = a.id, router.replace({ query: { ...route.query, alert: a.id } }));
const people = computed(() => (live.value ? remote.people : PEOPLE.map((name) => ({ id: name, name }))));
const ruleName = (id: string) => live.value ? ruleOptions.value.find((r) => r.id === id)?.name ?? id : RULES[id]?.name ?? id;
const filterRules = computed(() => live.value ? ruleOptions.value : Object.values(RULES));
/** 下拉选项带分面计数（真接口）；mock 不变 */
const facetCount = (key: 'level' | 'ruleId' | 'objKind', v: string) => {
  const n = facets.value?.[key].find((x) => x.value === v)?.count;
  return n == null ? '' : ` (${n})`;
};
const levelOptions = computed(() => ['全部', '高', '中', '低'].map((v) => ({ value: v, label: v === '全部' ? '全部级别' : `${v}级${live.value ? facetCount('level', v) : ''}` })));
const ruleSelectOptions = computed(() => [{ value: '全部', label: '全部规则' }, ...filterRules.value.map((r) => ({ value: r.id, label: `${r.id} ${r.name}${live.value ? facetCount('ruleId', r.id) : ''}` }))]);
const kindOptions = computed(() => [{ value: '全部', label: '全部对象类型' }, ...(facets.value?.objKind ?? []).map((k) => ({ value: k.value, label: `${k.label ?? k.value} (${k.count})` }))]);
const dimOptions = computed(() => (facets.value?.dims ?? []).map((d) => ({
  label: d.label, options: d.values.map((v) => ({ value: `${d.objKind}:${v.id}`, label: `${v.label} (${v.count})` })),
})));
/** 对象类型显示名：真接口取后台 objKindLabel，mock 取对象类型目录，不写死订单 / 商品 / 门店 */
const kindLabel = (a: Row) => a.objKindLabel ?? (live.value ? a.objKind : (OBJ_KIND_LABEL as Record<string, string>)[a.objKind] ?? a.objKind);
const dimKind = (d: AlertDim) => d.kindLabel ?? (live.value ? d.objKind : (OBJ_KIND_LABEL as Record<string, string>)[d.objKind] ?? d.objKind);
/** 发起动作的审批人：取规则绑定动作类型的审批人配置（真接口后台给 approver，mock 取 ACTION_META） */
const approverOf = (r: AlertRuleDto | null) => (live.value ? remote.detail?.approver ?? null : r?.action ? (ACTION_META as Record<string, { approver: string }>)[r.action]?.approver ?? null : null);

// ---- 真接口：取列表 / 详情 ----
const alertQuery = () => ({
  status: (TABS.some((t) => t.key === tab.value) ? tab.value : 'all') as AlertTab,
  level: level.value, ruleId: ruleId.value, kw: kw.value, objKind: objKind.value, dim: dimSel.value,
});
let listSeq = 0; let detailSeq = 0;
async function loadList() {
  const seq = ++listSeq, ctx = dynamicContextKey();
  listLoading.value = true;
  try {
    const r = await listAlerts({ ...alertQuery(), page: page.value, size: pageSize.value });
    if (seq !== listSeq || ctx !== dynamicContextKey()) return;
    remote.items = r.items.map(toRow); remote.total = r.total; remote.counts = r.counts;
    if (r.page !== page.value) page.value = r.page;
    remote.items.forEach((a) => seen.set(a.id, a));
  } catch (e) { if (seq === listSeq && ctx === dynamicContextKey()) { remote.items = []; remote.total = 0; readFail(e); } }
  finally { if (seq === listSeq) listLoading.value = false; }
}
let facetSeq = 0;
async function loadFacets() {
  const seq = ++facetSeq, ctx = dynamicContextKey();
  try {
    const f = await getAlertFacets(alertQuery());
    if (seq === facetSeq && ctx === dynamicContextKey()) facets.value = f;
  } catch (e) { if (seq === facetSeq && ctx === dynamicContextKey()) { facets.value = null; readFail(e); } }
}
async function loadDetail(id = selectedId.value) {
  const seq = ++detailSeq, ctx = dynamicContextKey();
  if (!id) { remote.detail = null; return; }
  try {
    const d = await getAlert(id);
    if (seq === detailSeq && ctx === dynamicContextKey()) remote.detail = toDetail(d);
  } catch (e) { if (seq === detailSeq && ctx === dynamicContextKey()) { remote.detail = null; readFail(e); } }
}
/** 写操作成功后：用返回的详情直接刷新右侧，再重拉列表（状态、计数、页签都可能变） */
async function afterWrite(d?: AlertDetailDto) {
  const ctx = dynamicContextKey();
  if (d) { ++detailSeq; remote.detail = toDetail(d); seen.set(d.id, toRow(d)); }
  await loadList();
  if (ctx !== dynamicContextKey()) { const changed = new Error('操作时上下文已切换'); changed.name = 'DynamicContextChanged'; throw changed; }
}
if (live.value) {
  watch(dynamicContextKey, () => { ++listSeq; ++detailSeq; remote.items = []; remote.total = 0; remote.detail = null; remote.people = []; ruleOptions.value = []; facets.value = null; objKind.value = '全部'; dimSel.value = []; seen.clear(); pinned.value = undefined; selectedKeys.value = []; }, { flush: 'sync' });
  watch([dynamicContextKey, tab, level, ruleId, kw, objKind, dimSel, page, pageSize], loadList, { immediate: true, deep: true });
  watch([dynamicContextKey, tab, level, ruleId, kw, objKind, dimSel], loadFacets, { immediate: true, deep: true });
  watch([dynamicContextKey, selectedId], () => { loadDetail(); }, { immediate: true });
  watch(dynamicContextKey, async (ctx) => {
    const results = await Promise.allSettled([getAlertMeta()]);
    if (ctx !== dynamicContextKey()) return;
    if (results[0].status === 'fulfilled') ruleOptions.value = results[0].value.rules; else readFail(results[0].reason);
  }, { immediate: true });
}

const statusBadge = (s: AlertStatus) =>
  s === '待处理' ? 'warning' : 'default';
/** 级别是提醒程度，不是失败：高 = 加粗深灰，其余灰 */
const levelClass = (l: Alert['level']) => (l === '高' ? 'c-1 fw-600' : 'c-2');

const columns = [
  { title: '级别', key: 'level', width: 64 },
  { title: '告警编号', dataIndex: 'id', key: 'id', width: 176 },
  { title: '规则', key: 'rule', width: 172 },
  { title: '对象', key: 'obj', ellipsis: true },
  { title: '触发值 / 阈值', key: 'value', width: 200 },
  { title: '触发时间', dataIndex: 'triggeredAt', key: 'triggeredAt', width: 172 },
  { title: '状态', key: 'status', width: 92 },
  { title: '处理人', key: 'assignee', width: 88 },
];

function claimRow(a: Row, who = ME.value) {
  if (a.status !== '待处理') return;
  a.status = '处理中'; a.assignee = who;
  a.log.push({ time: now(), text: '认领，状态 待处理 → 处理中', who });
}
async function claim() {
  const a = current.value; if (!a) return;
  pinned.value = a.id;
  if (REAL_API) {
    try { await afterWrite(await claimAlert(a.id)); message.success(`已认领 ${a.id}`); } catch (e) { fail(e); }
    return;
  }
  claimRow(a); message.success(`已认领 ${a.id}`);
}

// 转派
const transferTo = ref<string>();
const transferOpen = ref(false);
async function doTransfer() {
  const a = current.value; if (!a || !transferTo.value) return;
  pinned.value = a.id;
  if (REAL_API) {
    try { await afterWrite(await transferAlert(a.id, transferTo.value)); transferOpen.value = false; transferTo.value = undefined; message.success('已转派'); } catch (e) { fail(e); }
    return;
  }
  const from = a.assignee ?? '未认领';
  if (a.status === '待处理') a.status = '处理中';
  a.assignee = transferTo.value;
  a.log.push({ time: now(), text: `转派：${from} → ${transferTo.value}`, who: ME.value });
  transferOpen.value = false; transferTo.value = undefined;
  message.success('已转派');
}

// 关闭：必须选结果并写原因
const closeOpen = ref(false);
const batchCloseOpen = ref(false);
const closeAs = ref<'已解决' | '已忽略'>('已解决');
const closeReason = ref('');
function closeRows(rowsToClose: Row[]) {
  for (const a of rowsToClose) {
    const from = a.status;
    a.status = closeAs.value; a.assignee = a.assignee ?? ME.value;
    a.log.push({ time: now(), text: `关闭为${closeAs.value}：${closeReason.value}（${from} → ${closeAs.value}）`, who: ME.value });
  }
  closeOpen.value = false; batchCloseOpen.value = false; closeReason.value = '';
}
async function doClose() {
  const a = current.value; if (!a || !closeReason.value.trim()) return;
  pinned.value = a.id;
  if (REAL_API) {
    try { await afterWrite(await closeAlert(a.id, closeAs.value, closeReason.value.trim())); closeOpen.value = false; closeReason.value = ''; message.success('已关闭'); } catch (e) { fail(e); }
    return;
  }
  closeRows([a]); message.success('已关闭');
}

// 批量
const selectedKeys = ref<string[]>([]);
/** 只有本人处理中的告警才能关闭、发起动作 */
const mine = computed(() => current.value?.status === '处理中' && (live.value ? current.value.assignedToMe : current.value.assignee === ME.value));
const inList = computed(() => !!current.value && rows.value.some((a) => a.id === current.value!.id));
const selectedRows = computed(() => (live.value ? selectedKeys.value.map((k) => seen.get(k)).filter((a): a is Row => !!a) : LIST.filter((a) => selectedKeys.value.includes(a.id))));
const claimable = computed(() => selectedRows.value.filter((a) => live.value ? a.canClaim : a.status === '待处理'));
const closable = computed(() => selectedRows.value.filter((a) => live.value ? a.canClose : a.status === '处理中' && a.assignee === ME.value && !IN_FLIGHT.includes(a.actionStatus ?? '')));
async function batchClaim() {
  if (REAL_API) {
    try {
      const r = await batchClaimAlerts([...selectedKeys.value]);
      selectedKeys.value = [];
      await afterWrite(); await loadDetail();
      message.success(`已认领 ${r.claimed} 条`);
    } catch (e) { fail(e); }
    return;
  }
  const n = selectedRows.value.filter((a) => a.status === '待处理').length;
  selectedRows.value.forEach((a) => claimRow(a));
  selectedKeys.value = [];
  message.success(`已认领 ${n} 条`);
}
async function doBatchClose() {
  if (!closeReason.value.trim()) return;
  if (REAL_API) {
    try {
      const r = await batchCloseAlerts(closable.value.map((a) => a.id), closeAs.value, closeReason.value.trim());
      batchCloseOpen.value = false; closeReason.value = ''; selectedKeys.value = [];
      await afterWrite(); await loadDetail();
      message.success(`已关闭 ${r.closed} 条`);
    } catch (e) { fail(e); }
    return;
  }
  const n = closable.value.length;
  closeRows(closable.value);
  selectedKeys.value = [];
  message.success(`已关闭 ${n} 条`);
}
watch(tab, () => { selectedKeys.value = []; pinned.value = undefined; });
const actionOpen = ref(false);
const actionParamsText = ref('{}');
async function runAction() {
  const ctx = dynamicContextKey();
  const a = current.value; const r = rule.value;
  if (!a || !r?.action) return;
  let params: Record<string, unknown> = {};
  if (live.value) {
    try { const value: unknown = JSON.parse(actionParamsText.value); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(); params = value as Record<string, unknown>; }
    catch { message.error('参数须为 JSON 对象'); return; }
  }
  const ok = await confirm.ask({
    title: r.action,
    tag: '待确认',
    target: `${kindLabel(a)} ${a.objLabel}`,
    fields: [
      { label: '来源告警', value: `${a.id}（${r.id} ${r.version}）` },
      { label: '触发值', value: `${a.value}，阈值 ${a.threshold}` },
      { label: '参数', value: JSON.stringify(params) },
      { label: '审批人', value: approverOf(r) ?? '--' },
      { label: '幂等键', value: `act-${a.id.toLowerCase()}` },
    ],
    impact: '按绑定的审批配置处理，执行前重新校验权限、参数和动作版本。执行结果以关联动作记录为准；涉及外部系统且结果未知时按幂等键对账，不自动重发写入。',
  });
  if (!ok || ctx !== dynamicContextKey()) return;
  pinned.value = a.id;
  if (REAL_API) {
    try { const res = await startAlertAction(a.id, params); await afterWrite(res.alert); actionOpen.value = false; message.success(`已提交：${res.action.action} · ${res.action.dispatchPhase === 'queued' ? '待执行' : res.action.dispatchPhase === 'claimed' ? '调用中' : res.action.status}`); } catch (e) { fail(e); }
    return;
  }
  a.actionRef = `act-${a.id.toLowerCase()}`;
  a.actionStatus = '审批中';
  a.log.push({ time: now(), text: `发起动作「${r.action}」，待${approverOf(r) ?? '--'}审批；动作成功后告警自动转为已解决`, who: ME.value });
  message.success(`已提交：${r.action}`);
}
let peopleSeq = 0;
watch([dynamicContextKey, selectedId], async () => {
  transferOpen.value = false; transferTo.value = undefined; closeOpen.value = false; batchCloseOpen.value = false; actionOpen.value = false; actionParamsText.value = '{}'; closeReason.value = '';
  if (!live.value) return;
  const seq = ++peopleSeq, ctx = dynamicContextKey(), id = selectedId.value;
  remote.people = [];
  if (!id) return;
  try { const result = await listPeople(id); if (seq === peopleSeq && ctx === dynamicContextKey()) remote.people = result; }
  catch (e) { if (seq === peopleSeq && ctx === dynamicContextKey()) readFail(e); }
}, { immediate: true });
</script>

<template>
  <div class="al">
    <div class="panel al-list">
      <div class="panel-head">
        <a-radio-group v-model:value="tab" size="small" button-style="solid">
          <a-radio-button v-for="t in TABS" :key="t.key" :value="t.key">{{ t.title }}</a-radio-button>
        </a-radio-group>
        <div v-if="selectedKeys.length" class="al-filters">
          <span class="t-14 c-2">已选 {{ selectedKeys.length }} 条</span>
          <a-button size="small" :disabled="readonly || !claimable.length" @click="batchClaim">批量认领</a-button>
          <a-popover v-model:open="batchCloseOpen" trigger="click" placement="bottomRight">
            <template #content>
              <div class="al-pop">
                <a-radio-group v-model:value="closeAs"><a-radio value="已解决">已解决</a-radio><a-radio value="已忽略">已忽略</a-radio></a-radio-group>
                <a-textarea v-model:value="closeReason" :rows="3" placeholder="关闭原因（必填）" />
                <div class="al-pop-foot"><a-button size="small" type="primary" :disabled="readonly || !closable.length || !closeReason.trim()" @click="doBatchClose">关闭 {{ closable.length }} 条</a-button></div>
              </div>
            </template>
            <a-tooltip :title="closable.length ? undefined : '只能关闭本人处理中的告警，请先认领'"><a-button size="small" :disabled="!closable.length">批量关闭{{ closable.length && closable.length < selectedKeys.length ? `（${closable.length}）` : '' }}</a-button></a-tooltip>
          </a-popover>
          <a-button size="small" type="text" @click="selectedKeys = []">取消选择</a-button>
        </div>
        <div v-else class="al-filters">
          <a-select v-model:value="level" size="small" style="width: 112px" :options="levelOptions" />
          <a-select v-model:value="ruleId" size="small" style="width: 180px" :options="ruleSelectOptions" />
          <a-select v-if="live && (facets?.objKind.length ?? 0) > 1" v-model:value="objKind" size="small" style="width: 140px" :options="kindOptions" />
          <a-select v-if="live && facets?.dims.length" v-model:value="dimSel" mode="multiple" size="small" allow-clear
            :max-tag-count="1" placeholder="按维度筛选" style="width: 200px" :options="dimOptions" />
          <a-input-search v-model:value="kw" size="small" placeholder="告警编号 / 对象" style="width: 200px" />
        </div>
      </div>
      <div class="al-table paged-scroll">
        <a-table :columns="columns" :data-source="pageRows" :loading="listLoading" row-key="id" size="middle" :pagination="false"
          :row-selection="{ selectedRowKeys: selectedKeys, onChange: (k: (string | number)[]) => (selectedKeys = k as string[]), getCheckboxProps: (r: Row) => ({ disabled: readonly || isClosed(r.status) }) }"
          :row-class-name="(r: Row) => (r.id === selectedId ? 'al-row-active' : '')"
          :custom-row="(r: Row) => ({ onClick: () => pick(r) })">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'level'"><span :class="levelClass(record.level)" >{{ record.level }}</span></template>
            <template v-else-if="column.key === 'id'"><span class="num">{{ record.id }}</span></template>
            <template v-else-if="column.key === 'rule'">{{ ruleName(record.ruleId) }}</template>
            <template v-else-if="column.key === 'obj'">
              <ObjRef :kind="record.objKind" :id="record.objId" @click.stop>{{ record.objLabel }}</ObjRef>
              <span v-for="d in record.dims" :key="`${d.objKind}:${d.id}`" class="c-3"> · {{ d.label }}</span>
            </template>
            <template v-else-if="column.key === 'value'"><span class="num">{{ record.value }}</span><span class="c-3"> / {{ record.threshold }}</span></template>
            <template v-else-if="column.key === 'triggeredAt'"><span class="num">{{ record.triggeredAt }}</span></template>
            <template v-else-if="column.key === 'status'"><a-badge :status="statusBadge(record.status)" :text="record.status" /></template>
            <template v-else-if="column.key === 'assignee'">{{ record.assignee ?? '--' }}</template>
          </template>
        </a-table>
      </div>
      <AppPagination v-model:page="page" v-model:size="pageSize" variant="full" :total="total" :default-size="pq.defaultSize" unit="条告警" :empty="false" class="al-pager" />
    </div>

    <div v-if="current" class="panel al-detail">
      <div class="panel-head">
        <span class="t-16 fw-600">{{ rule?.name || current.ruleId }}</span>
        <a-badge :status="statusBadge(current.status)" :text="current.status" />
      </div>
      <div class="al-body">
        <div v-if="!inList" class="t-12 c-3 al-out">此告警已移出当前筛选（现为{{ current.status }}）</div>
        <a-descriptions :column="1" size="small" bordered :label-style="{ width: '96px' }">
          <a-descriptions-item label="告警编号"><span class="num">{{ current.id }}</span></a-descriptions-item>
          <a-descriptions-item label="对象">
            <span class="c-3">{{ kindLabel(current) }} </span><ObjRef :kind="current.objKind" :id="current.objId">{{ current.objLabel }}</ObjRef>
          </a-descriptions-item>
          <a-descriptions-item v-for="d in current.dims" :key="`${d.objKind}:${d.id}`" :label="dimKind(d)">
            <ObjRef :kind="d.objKind" :id="d.id">{{ d.label }}</ObjRef>
          </a-descriptions-item>
          <a-descriptions-item label="触发值"><span class="num">{{ current.value }}</span><span class="c-3">，阈值 {{ current.threshold }}</span></a-descriptions-item>
          <a-descriptions-item label="规则">{{ current.ruleId }}<template v-if="rule"> {{ ruleVersion(rule.version) }}<span v-if="triggerText"> · {{ triggerText }}</span></template><span v-else class="c-3"> · 当前定义不可用</span></a-descriptions-item>
          <a-descriptions-item v-if="metric" label="指标">
            <HoverCard kind="metric" :id="metric.id">{{ metric.name }}</HoverCard><span v-if="metric.version" class="c-3">（口径 {{ metric.version }}）</span>
          </a-descriptions-item>
          <a-descriptions-item v-if="conditionText" label="条件">{{ conditionText }}</a-descriptions-item>
          <a-descriptions-item label="近 7 天">同一规则、同一对象第 {{ recent }} 次触发</a-descriptions-item>
          <a-descriptions-item v-if="rule?.dedupe" label="去重">{{ rule.dedupe }}</a-descriptions-item>
          <a-descriptions-item label="数据截至"><span class="num">{{ current.cutoff }}</span></a-descriptions-item>
          <a-descriptions-item label="处理人">{{ current.assignee ?? '未认领' }}</a-descriptions-item>
          <a-descriptions-item v-if="current.actionRef" label="关联动作"><router-link v-if="current.actionId" :to="{ path: '/use/ops/actions', query: { ontology: app.ontologyId, act: current.actionId, tab: current.actionStatus === '结果未知' ? 'unknown' : 'records' } }" class="num">{{ current.actionId }}</router-link><span v-else class="num">{{ current.actionRef }}</span><span class="c-3"> · {{ current.actionStatus }}</span></a-descriptions-item>
        </a-descriptions>

        <div class="t-14 fw-600 al-sub">处理记录</div>
        <a-timeline class="al-tl">
          <a-timeline-item v-for="(t, i) in current.log" :key="i" :color="t.abnormal ? 'var(--tl-attention)' : 'gray'">
            <div class="al-tl-row"><span class="t-14 al-tl-text" :title="t.text">{{ t.text }}</span><span class="t-12 c-1 num al-tl-meta">{{ t.time }} · {{ t.who }}</span></div>
          </a-timeline-item>
        </a-timeline>
      </div>
      <div class="al-foot">
        <span v-if="readonly" class="t-14 c-3">{{ roReason }}，不能处理告警</span>
        <template v-else-if="!isClosed(current.status)">
          <span v-if="current.status === '处理中' && current.assignee !== ME" class="t-14 c-3">{{ current.assignee }} 处理中</span>
          <a-popover v-model:open="transferOpen" trigger="click" placement="topRight">
            <template #content>
              <div class="al-pop">
                <a-select v-model:value="transferTo" placeholder="转派给" :options="people.filter((p) => live ? p.id !== current!.assigneeId : p.name !== current!.assignee).map((p) => ({ value: p.id, label: live ? `${p.name}（${p.id}）` : p.name }))" />
                <div class="al-pop-foot"><a-button size="small" type="primary" :disabled="!transferTo" @click="doTransfer">转派</a-button></div>
              </div>
            </template>
            <a-tooltip :title="locked ? `关联动作${current.actionStatus}，有结果前不能转派` : undefined"><a-button :disabled="live ? !current.can?.transfer : locked">转派</a-button></a-tooltip>
          </a-popover>
          <a-button v-if="current.status === '待处理'" type="primary" :disabled="live && !current.can?.claim" @click="claim">认领</a-button>
          <template v-if="mine">
          <a-popover v-model:open="closeOpen" trigger="click" placement="topRight">
            <template #content>
              <div class="al-pop">
                <a-radio-group v-model:value="closeAs"><a-radio value="已解决">已解决</a-radio><a-radio value="已忽略">已忽略</a-radio></a-radio-group>
                <a-textarea v-model:value="closeReason" :rows="3" placeholder="关闭原因（必填）" />
                <div class="al-pop-foot"><a-button size="small" type="primary" :disabled="!closeReason.trim()" @click="doClose">关闭告警</a-button></div>
              </div>
            </template>
            <a-tooltip :title="locked ? `关联动作${current.actionStatus}，有结果前不能关闭` : undefined"><a-button :disabled="live ? !current.can?.close : locked">关闭</a-button></a-tooltip>
          </a-popover>
          <span v-if="current.actionRef" class="t-14 c-3">{{ rule?.action }} · {{ current.actionStatus }}</span>
          <a-tooltip v-else-if="rule?.action" :title="current.can?.reasons.startAction"><a-button type="primary" :disabled="live && !current.can?.startAction" @click="live ? actionOpen = true : runAction()">{{ rule.action }}</a-button></a-tooltip>
          </template>
        </template>
        <span v-else class="t-14 c-3">已关闭，处理人 {{ current.assignee }}</span>
      </div>
    </div>
    <a-modal v-model:open="actionOpen" :title="rule?.action || '发起动作'" ok-text="确认参数" @ok="runAction">
      <a-descriptions size="small" :column="1">
        <a-descriptions-item v-for="p in remote.detail?.actionParams" :key="p.api" :label="`${p.name} (${p.api})`">{{ p.type }}{{ p.required ? ' · 必填' : '' }}<span v-if="p.rule"> · {{ p.rule }}</span></a-descriptions-item>
      </a-descriptions>
      <a-form layout="vertical"><a-form-item label="参数 JSON"><a-textarea v-model:value="actionParamsText" :rows="8" spellcheck="false" /></a-form-item></a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.al { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.al-list { flex: 1; }
.al-filters { display: flex; gap: 8px; }
.al-table { flex: 1; min-height: 0; padding: 0 16px; }
.al-pager { flex: none; padding: 8px 16px 12px; }
.al-table :deep(.ant-table-row) { cursor: pointer; }
.al-table :deep(.al-row-active > td) { background: var(--brand-bg) !important; }
.al-detail { width: 480px; flex: none; }
.al-body { flex: 1; min-height: 0; padding: 12px 16px; }
.al-sub { margin: 16px 0 12px; }
.al-tl :deep(.ant-timeline-item) { padding-bottom: 12px; }
.al-out { margin-bottom: 8px; }
.al-pop { width: 280px; display: flex; flex-direction: column; gap: 8px; }
.al-pop-foot { display: flex; justify-content: flex-end; }
.al-act-state { align-self: center; }
.al-foot { flex: none; align-items: center; display: flex; justify-content: flex-end; gap: 8px; padding: 12px 16px; border-top: var(--border); }
.al-tl-row { display: flex; align-items: baseline; gap: 12px; }
.al-tl-text { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.al-tl-meta { flex: none; }
</style>
