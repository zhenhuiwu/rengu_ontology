<script setup lang="ts">
import InfoTip from '@/components/InfoTip.vue';
/**
 * 动作中心（动态本体）：左侧动作列表（full 分页），右侧 480px 当前动作详情：参数、来源、幂等键、外部单号、执行记录。
 * 页签写进 ?tab=（pending 待确认 / records 执行记录 / unknown 结果未知与对账），当前选中写进 ?act=，页码 ?page=&size=。
 * 写外部系统的动作（确认提交、重新提交）一律走公共确认卡。结果未知：系统每 15 分钟按幂等键自动查询外部结果，
 * 仍无法确认时人工发起对账；任何情况下都不自动重试写入。
 * 开关 dynamic 走真接口时（src/api/dynamic.ts）：列表 / 页签计数 / 详情（执行记录）/ 动作与外部系统下拉都取后台，
 * 确认提交、撤回、重新提交、对账一一调用对应接口，成功后用返回的详情刷新；否则保持原 mock 逻辑。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import ObjRef from '@/components/ObjRef.vue';
import StatusTimeline from '@/components/StatusTimeline.vue';
import AppPagination from '@/components/AppPagination.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { paginate } from '@/mock/paginate';
import {
  ACTIONS, ACTION_META, ACTION_NAMES, ACTION_SYSTEMS, OBJ_KIND_LABEL, hasObjRef, waitingText,
  type ActionRecord as MockActionRecord, type ActionStatus, type ActionLog,
} from '@/mock/actions';
import { useAppStore } from '@/stores/app';
import { isApiError } from '@/api/http';
import {
  dynamicApi, dynamicContextKey, listActionRuns, getActionRun, getActionMeta, confirmActionRun, approveActionRun, withdrawActionRun, resubmitActionRun, reconcileActionRun,
  type ActionMetaDto, type ActionRunDetailDto, type ActionRunDto, type ActionTab, type ActionCan,
} from '@/api/dynamic';

type ActionRecord = MockActionRecord & { typeId?: string; can?: ActionCan };

/** 页面内的动作状态（模拟后台：确认、撤回、重新提交、对账都会改它并写执行记录） */
const LIST = ACTIONS;
/** 是否走真接口（开关 dynamic）；构建时确定 */
const REAL_API = dynamicApi.isReal();
const live = computed(() => REAL_API);
const readFail = (e: unknown) => fail(e);
const app = useAppStore();
/** 当前用户：mock 固定王敏；真接口时后台按 X-Demo-As 认人，前端取当前演示身份的姓名做同样的判断 */
const ME = computed(() => REAL_API ? app.user.name : '王敏');
let tick = 0;
const now = () => { tick += 1; return `2026-10-25 16:${String(40 + tick).padStart(2, '0')}:00`; };
const TERMINAL: ActionStatus[] = ['成功', '已撤回', '已对账'];

const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const { readonly, reason: roReason } = useReadonly();

const TABS: { key: string; title: string; statuses: ActionStatus[] }[] = [
  { key: 'pending', title: '待确认', statuses: ['待确认'] },
  { key: 'records', title: '执行记录', statuses: ['审批中', '执行中', '成功', '失败', '结果未知', '已撤回', '已对账'] },
  { key: 'unknown', title: '结果未知与对账', statuses: ['结果未知', '已对账'] },
];
const tab = computed({
  get: () => (TABS.some((t) => t.key === route.query.tab) ? (route.query.tab as string) : 'pending'),
  set: (v) => router.replace({ query: { ...route.query, tab: v === 'pending' ? undefined : v, act: undefined, page: undefined } }),
});
const curTab = computed(() => TABS.find((t) => t.key === tab.value)!);
const actionName = ref('全部');
const system = ref('全部');
const status = ref('全部');
const kw = ref('');
watch(tab, () => { status.value = '全部'; pinned.value = undefined; });
const pq = usePagedQuery({ defaultSize: 10, filters: [actionName, system, status, kw] });
const listLoading = ref(false);

/** 真接口状态：当前页、总数、页签计数、当前详情、动作元数据（动作 / 外部系统下拉、调用接口名） */
const remote = reactive({
  items: [] as ActionRecord[], total: 0, counts: { pending: 0, records: 0, unknown: 0 } as Record<string, number>,
  detail: null as ActionRecord | null, meta: null as ActionMetaDto | null,
});
const toRec = (a: ActionRunDto | ActionRunDetailDto): ActionRecord => ({ ...a, log: 'log' in a ? a.log : [] } as ActionRecord);
const fail = (e: unknown) => { if ((e as Error)?.name !== 'DynamicContextChanged') message.error(isApiError(e) ? e.message || e.code : String(e)); };
const actionNames = computed<string[]>(() => (live.value ? remote.meta?.actionNames ?? [] : ACTION_NAMES));
const actionSystems = computed<{ id: string; name: string }[]>(() => (live.value ? remote.meta?.actionSystems ?? [] : ACTION_SYSTEMS));
const callOf = (name: string, typeId?: string) => (live.value ? remote.meta?.actionMeta[typeId || name]?.call ?? '--' : ACTION_META[name as keyof typeof ACTION_META]?.call ?? '--');

const rows = computed(() => {
  if (live.value) return remote.items;
  const t = curTab.value;
  const k = kw.value.trim();
  const list = LIST.filter((a) =>
    t.statuses.includes(a.status) &&
    (status.value === '全部' || a.status === status.value) &&
    (actionName.value === '全部' || a.action === actionName.value) &&
    (system.value === '全部' || a.sysId === system.value) &&
    (!k || a.id.includes(k) || a.objLabel.includes(k) || a.idemKey.includes(k.toLowerCase()) || (a.extRef ?? '').includes(k) || (a.source.alertId ?? '').includes(k) || (a.source.ruleId ?? '') === k || (a.source.session ?? '') === k));
  // 对账页签：待对账的排在前面
  return t.key === 'unknown' ? [...list].sort((a, b) => Number(b.status === '结果未知') - Number(a.status === '结果未知')) : list;
});
const paged = computed(() => (live.value ? { items: remote.items, total: remote.total } : paginate(rows.value, { page: pq.page.value, size: pq.size.value })));

/** 刚处理过的动作即使离开当前页签也继续显示在右侧，避免详情突然换成别的动作 */
const pinned = ref<string>();
const selectedId = computed(() => (route.query.act as string) || pinned.value || paged.value.items[0]?.id);
const current = computed<ActionRecord | undefined>(() => live.value ? (remote.detail && remote.detail.id === selectedId.value ? remote.detail : undefined) : LIST.find((a) => a.id === selectedId.value) ?? paged.value.items[0]);
const inList = computed(() => !!current.value && rows.value.some((a) => a.id === current.value!.id));
const pick = (a: Pick<ActionRecord, 'id'>) => { pinned.value = a.id; router.replace({ query: { ...route.query, act: a.id } }); };

// ---- 真接口：取列表 / 详情 / 元数据 ----
let listSeq = 0; let detailSeq = 0;
async function loadList() {
  const seq = ++listSeq, ctx = dynamicContextKey();
  listLoading.value = true;
  try {
    const r = await listActionRuns({ tab: tab.value as ActionTab, status: status.value, action: actionName.value, sysId: system.value, kw: kw.value, page: pq.page.value, size: pq.size.value });
    if (seq !== listSeq || ctx !== dynamicContextKey()) return;
    remote.items = r.items.map(toRec); remote.total = r.total; remote.counts = r.counts;
  } catch (e) { if (seq === listSeq && ctx === dynamicContextKey()) { remote.items = []; remote.total = 0; readFail(e); } }
  finally { if (seq === listSeq) listLoading.value = false; }
}
async function loadDetail(id = selectedId.value) {
  const seq = ++detailSeq, ctx = dynamicContextKey();
  if (!id) { remote.detail = null; return; }
  try {
    const d = await getActionRun(id);
    if (seq === detailSeq && ctx === dynamicContextKey()) remote.detail = toRec(d);
  } catch (e) { if (seq === detailSeq && ctx === dynamicContextKey()) { remote.detail = null; readFail(e); } }
}
/** 写操作成功后：用返回的详情直接刷新右侧，再重拉列表（状态、计数、页签都可能变） */
async function afterWrite(d?: ActionRunDetailDto) {
  if (d) { ++detailSeq; remote.detail = toRec(d); }
  await loadList();
}
if (live.value) {
  watch(dynamicContextKey, () => { ++listSeq; ++detailSeq; remote.items = []; remote.total = 0; remote.detail = null; remote.meta = null; pinned.value = undefined; }, { flush: 'sync' });
  watch([dynamicContextKey, tab, actionName, system, status, kw, pq.page, pq.size], loadList, { immediate: true });
  watch([dynamicContextKey, selectedId], () => { loadDetail(); }, { immediate: true });
  watch(dynamicContextKey, async (ctx) => {
    try { const m = await getActionMeta(); if (ctx === dynamicContextKey()) remote.meta = m; }
    catch (e) { if (ctx === dynamicContextKey()) readFail(e); }
  }, { immediate: true });
}

/** 状态点：待确认橙（需要关注）、失败红，其余灰；结果未知单独显示橙色问号 + “结果未知 · 待对账” */
const badge = (s: ActionStatus) => (s === '待确认' ? 'warning' : s === '失败' ? 'error' : 'default');
/** 对象类型显示名：真接口取 /actions/meta 的对象类型目录，mock 取 OBJ_KIND_LABEL；未登记的类型显示编号 */
const kindLabel = (k: ActionRecord['objKind']) => (live.value ? remote.meta?.objKindLabel?.[k] : (OBJ_KIND_LABEL as Record<string, string>)[k]) ?? k;
const sourceText = (a: ActionRecord) =>
  a.source.type === '规则' ? `规则 ${a.source.ruleId} ${a.source.ruleVersion}${a.source.alertId ? ` · 告警 ${a.source.alertId}` : ''}`
  : a.source.type === '告警' ? `告警 ${a.source.alertId}`
  : a.source.type === '问数' ? `问数 · ${a.source.session} ${a.source.sessionTitle}`
  : a.source.type === '推演' ? `情景推演 · ${a.source.scenarioId} ${a.source.scenarioName}`
  : a.retryOf ? `重新提交 · ${a.retryOf}` : '手动';
/** 规则暂存的动作由规则指定的确认人确认 / 撤回；其他人只能查看 */
const confirmerOf = (a: ActionRecord) => a.source.confirmer ?? a.initiator;
const notMine = (a: ActionRecord) => !!a.source.confirmer && a.source.confirmer !== ME.value;
const notMineText = (a: ActionRecord) => `该动作由规则 ${a.source.ruleId} 暂存，指定确认人为 ${a.source.confirmer}，只有确认人可以确认提交或撤回`;
const timeline = computed(() => (current.value?.log ?? []).map((l: ActionLog) => ({ time: l.desc ? `${l.time} · ${l.desc}` : l.time, text: l.text, status: l.status })));

const columns = [
  { title: '动作编号', key: 'id', width: 168 },
  { title: '动作', dataIndex: 'action', key: 'action', width: 120 },
  { title: '对象', key: 'obj', ellipsis: true },
  { title: '来源', key: 'source', width: 180, ellipsis: true },
  { title: '外部系统', dataIndex: 'system', key: 'system', width: 84 },
  { title: '状态', key: 'status', width: 150 },
  { title: '发起人', dataIndex: 'initiator', key: 'initiator', width: 124, ellipsis: { showTitle: false } },
  { title: '提交时间', key: 'submittedAt', width: 168 },
];

function push(a: ActionRecord, l: ActionLog) { a.log.push(l); }

async function approve() {
  const a = current.value;
  if (!live.value || !a?.can?.approve) return;
  const ok = await confirm.ask({ title: `审批 ${a.action}`, tag: '审批中', okText: '审批并执行',
    target: `${kindLabel(a.objKind)} ${a.objLabel}`, fields: [{ label: '参数', value: a.params },
      { label: '幂等键', value: a.idemKey }], impact: '审批后按已发布定义执行，外部动作进入持久执行队列。' });
  if (!ok) return;
  try { await afterWrite(await approveActionRun(a.id)); message.success('审批已处理'); }
  catch (e) { fail(e); }
}

// 待确认 → 确认提交（写外部系统前的确认卡）
async function submit() {
  const ctx = dynamicContextKey();
  const a = current.value; if (!a || a.status !== '待确认') return;
  const ok = await confirm.ask({
    title: a.action, tag: '待确认', okText: '确认提交',
    target: `${kindLabel(a.objKind)} ${a.objLabel}`,
    fields: [
      { label: '参数', value: a.params },
      { label: '来源', value: a.source.type === '告警' ? `${a.source.alertId}（${a.source.ruleId} ${a.source.ruleVersion}）` : sourceText(a) },
      { label: '确认人', value: confirmerOf(a) },
      { label: '审批人', value: a.approver },
      { label: '外部系统', value: `${a.system} · ${callOf(a.action, a.typeId)}` },
      { label: '幂等键', value: a.idemKey },
    ],
    blocked: notMine(a) ? notMineText(a) : undefined,
    impact: `确认后进入审批，审批通过后写回 ${a.system}；外部系统超时会标记为“结果未知”，系统每 15 分钟按幂等键自动查询，仍无法确认时人工对账，不自动重试写入。`,
  });
  if (!ok || ctx !== dynamicContextKey()) return;
  pinned.value = a.id;
  if (REAL_API) {
    try { const d = await confirmActionRun(a.id); await afterWrite(d); message.success(d.status === '审批中' ? `已提交：${d.action}，等待 ${d.approver} 审批` : `已提交：${d.action}，${d.status}`); } catch (e) { fail(e); }
    return;
  }
  a.status = '审批中';
  push(a, { time: now(), text: '确认提交', desc: `确认人 ${ME.value}` });
  push(a, { time: now(), text: '等待审批', desc: `审批人 ${a.approver}`, status: 'warn' });
  message.success(`已提交：${a.action}，等待 ${a.approver} 审批`);
}

// 待确认 → 撤回（不写外部系统，只需填原因）
const withdrawOpen = ref(false);
const withdrawReason = ref('');
async function doWithdraw() {
  const a = current.value; if (!a || !withdrawReason.value.trim() || notMine(a)) return;
  pinned.value = a.id;
  if (REAL_API) {
    try { await afterWrite(await withdrawActionRun(a.id, withdrawReason.value.trim())); withdrawOpen.value = false; withdrawReason.value = ''; message.success(`已撤回 ${a.id}`); } catch (e) { fail(e); }
    return;
  }
  a.status = '已撤回';
  push(a, { time: now(), text: `撤回：${withdrawReason.value.trim()}`, desc: ME.value });
  withdrawOpen.value = false; withdrawReason.value = '';
  message.success(`已撤回 ${a.id}`);
}

// 失败 → 重新提交：生成新动作、新幂等键，原记录保留
async function resubmit() {
  const ctx = dynamicContextKey();
  const a = current.value; if (!a || a.status !== '失败' || a.retriedAs) return;
  const n = (a.idemKey.match(/-r(\d+)$/)?.[1] ?? '0');
  const newKey = `${a.idemKey.replace(/-r\d+$/, '')}-r${Number(n) + 1}`;
  // 真接口时新编号由后台生成，确认卡里不预先显示
  const newId = REAL_API ? '' : `ACT-20261025-${String(Math.max(...LIST.map((x) => Number(x.id.slice(-4)))) + 1).padStart(4, '0')}`;
  const ok = await confirm.ask({
    title: `重新提交：${a.action}`, okText: '重新提交', irreversible: true,
    target: `${kindLabel(a.objKind)} ${a.objLabel}`,
    fields: [
      { label: '上次失败', value: a.failReason ?? '--' },
      { label: '参数', value: a.params },
      { label: '外部系统', value: `${a.system} · ${callOf(a.action, a.typeId)}` },
      { label: '新幂等键', value: newKey },
    ],
    impact: `沿用原审批（${a.approver}），生成新动作${newId ? ` ${newId}` : ''} 并用新幂等键调用 ${a.system}；原动作 ${a.id} 保留为失败，便于追溯。`,
  });
  if (!ok || ctx !== dynamicContextKey()) return;
  if (REAL_API) {
    try {
      const r = await resubmitActionRun(a.id);
      await afterWrite(r.action);
      pick({ id: r.id }); // 选中新生成的动作（原动作保留为失败）
      message.success(`已重新提交为 ${r.id}`);
    } catch (e) { fail(e); }
    return;
  }
  const t = now();
  const rec: ActionRecord = reactive({
    ...a, id: newId, idemKey: newKey, status: '执行中', submittedAt: t, extRef: null, failReason: undefined,
    initiator: ME.value, retryOf: a.id, retriedAs: undefined, source: a.source.type === '告警' || a.source.type === '规则' ? { ...a.source, confirmer: undefined } : { type: '手动' },
    log: [
      { time: t, text: `重新提交自 ${a.id}（上次失败：${a.failReason ?? '--'}）`, desc: ME.value },
      { time: t, text: `生成动作「${a.action}」· 动作类型 ${a.typeVersion}`, desc: `发起人 ${ME.value} · 幂等键 ${newKey}` },
      { time: t, text: `调用 ${a.system}：${ACTION_META[a.action].call}`, desc: `沿用原审批 ${a.approver}` },
      { time: t, text: `等待 ${a.system} 返回`, desc: waitingText(a.sysId), status: 'warn' },
    ],
  });
  LIST.unshift(rec);
  a.retriedAs = newId;
  push(a, { time: t, text: `已重新提交为 ${newId}`, desc: `${ME.value} · 新幂等键 ${newKey}` });
  pick({ id: newId });
  message.success(`已重新提交为 ${newId}`);
}

// 结果未知 → 发起对账：自动查询仍无法确认时，人工核对外部系统后选结论并写说明；不自动重试写入
const reconOpen = ref(false);
const reconAs = ref<'外部已成功' | '外部未执行' | '仍无法确认'>();
const reconNote = ref('');
async function doReconcile() {
  const a = current.value; if (!a || a.status !== '结果未知' || !reconAs.value || !reconNote.value.trim()) return;
  pinned.value = a.id;
  const note = reconNote.value.trim();
  if (REAL_API) {
    try {
      const as = reconAs.value;
      await afterWrite(await reconcileActionRun(a.id, as, note));
      if (as === '仍无法确认') message.info('已记录对账，仍为结果未知'); else message.success(`已对账：${as}`);
      reconOpen.value = false; reconAs.value = undefined; reconNote.value = '';
    } catch (e) { fail(e); }
    return;
  }
  if (reconAs.value === '仍无法确认') {
    push(a, { time: now(), text: `对账：仍无法确认（${note}）`, desc: `${ME.value} · 保持结果未知，系统继续每 15 分钟按幂等键查询`, status: 'unknown' });
    message.info('已记录对账，仍为结果未知');
  } else {
    a.status = '已对账'; a.reconcile = reconAs.value;
    push(a, {
      time: now(),
      text: reconAs.value === '外部已成功' ? `对账：外部已成功（${note}）` : `对账：外部未执行，动作未生效（${note}）`,
      desc: `${ME.value} · ${a.system} 外部单号 ${a.extRef ?? '--'}`,
    });
    message.success(`已对账：${reconAs.value}`);
  }
  reconOpen.value = false; reconAs.value = undefined; reconNote.value = '';
}
watch(selectedId, () => { reconOpen.value = false; withdrawOpen.value = false; });

const terminalText = computed(() => {
  const a = current.value; if (!a) return '';
  if (a.status === '成功') return `已完成 · ${a.system} 外部单号 ${a.extRef}`;
  if (a.status === '已撤回') return '已撤回，不再执行';
  if (a.status === '已对账') return a.reconcile === '外部未执行' ? '已对账 · 外部未执行，如仍需执行请重新发起' : `已对账 · 外部已成功（${a.extRef}）`;
  return '';
});
</script>

<template>
  <div class="ac">
    <div class="panel ac-list">
      <div class="panel-head">
        <a-radio-group v-model:value="tab" size="small" button-style="solid">
          <a-radio-button v-for="t in TABS" :key="t.key" :value="t.key">{{ t.title }}</a-radio-button>
        </a-radio-group>
        <div class="ac-filters">
          <a-select v-model:value="actionName" size="small" class="ac-w140"
            :options="[{ value: '全部', label: '全部动作' }, ...actionNames.map((v) => ({ value: v, label: v }))]" />
          <a-select v-model:value="system" size="small" class="ac-w120"
            :options="[{ value: '全部', label: '全部外部系统' }, ...actionSystems.map((v) => ({ value: v.id, label: v.name }))]" />
          <a-select v-if="curTab.statuses.length > 1" v-model:value="status" size="small" class="ac-w120"
            :options="[{ value: '全部', label: '全部状态' }, ...curTab.statuses.map((v) => ({ value: v, label: v }))]" />
          <a-input-search v-model:value="kw" size="small" placeholder="编号 / 对象 / 幂等键 / 外部单号" class="ac-w240" />
        </div>
      </div>
      <div class="ac-table paged-scroll">
        <a-table :columns="columns" :data-source="paged.items" :loading="listLoading" row-key="id" size="middle" :pagination="false"
          :row-class-name="(r: ActionRecord) => (r.id === current?.id ? 'ac-row-active' : '')"
          :custom-row="(r: ActionRecord) => ({ onClick: () => pick(r) })">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'id'"><span class="num">{{ record.id }}</span></template>
            <template v-else-if="column.key === 'obj'">
              <ObjRef v-if="hasObjRef(record.objKind)" :kind="record.objKind" :id="record.objId" @click.stop>{{ record.objLabel }}</ObjRef><span v-else>{{ record.objLabel }}</span>
            </template>
            <template v-else-if="column.key === 'source'"><span :class="record.source.type === '手动' ? 'c-2' : 'num'">{{ sourceText(record) }}</span></template>
            <template v-else-if="column.key === 'initiator'"><a-tooltip :title="record.initiator" placement="topLeft"><span class="ac-nowrap">{{ record.initiator }}</span></a-tooltip></template>
            <template v-else-if="column.key === 'status'"><span v-if="record.status === '结果未知'" class="tl-attention ac-unk"><QuestionCircleOutlined /> 结果未知 · 待对账</span><a-badge v-else :status="badge(record.status)" :text="record.status" /></template>
            <template v-else-if="column.key === 'submittedAt'"><span class="num">{{ record.submittedAt }}</span></template>
          </template>
        </a-table>
      </div>
      <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" class="ac-pager"
        variant="full" :total="paged.total" :default-size="pq.defaultSize" :empty="false" />
    </div>

    <div v-if="current" class="panel ac-detail">
      <div class="panel-head">
        <span class="t-16 fw-600">{{ current.action }}</span>
        <span v-if="current.status === '结果未知'" class="tl-attention ac-unk"><QuestionCircleOutlined /> 结果未知 · 待对账</span>
        <a-badge v-else :status="badge(current.status)" :text="current.status" />
      </div>
      <div class="ac-body scroll-area">
        <div v-if="!inList" class="t-12 c-3 ac-out">此动作已移出当前筛选（现为{{ current.status }}）</div>
        <a-descriptions :column="2" size="small" bordered :label-style="{ width: '96px' }">
          <a-descriptions-item label="编号" :span="2"><span class="num">{{ current.id }}</span><span class="c-3 num"> · {{ current.submittedAt }}</span></a-descriptions-item>
          <a-descriptions-item label="动作" :span="2">{{ current.action }}<span class="c-3"> · 动作类型 {{ current.typeVersion }}</span></a-descriptions-item>
          <a-descriptions-item label="参数" :span="2">{{ current.params }}</a-descriptions-item>
          <a-descriptions-item label="对象" :span="2">
            <span class="c-3">{{ kindLabel(current.objKind) }} </span><ObjRef v-if="hasObjRef(current.objKind)" :kind="current.objKind" :id="current.objId">{{ current.objLabel }}</ObjRef><span v-else class="c-1">{{ current.objLabel }}</span>
          </a-descriptions-item>
          <a-descriptions-item label="来源" :span="2">
            <template v-if="current.source.type === '告警'"><span class="num">告警 {{ current.source.alertId }}</span><span class="c-3"> · 规则 {{ current.source.ruleId }} {{ current.source.ruleVersion }}</span></template>
            <template v-else-if="current.source.type === '规则'"><span class="num">规则 {{ current.source.ruleId }} {{ current.source.ruleVersion }}</span><span v-if="current.source.alertId" class="c-3 num"> · 告警 {{ current.source.alertId }}</span><span class="c-3"> · 命中后自动暂存</span></template>
            <template v-else>{{ sourceText(current) }}</template>
          </a-descriptions-item>
          <a-descriptions-item label="发起人"><span class="ac-nowrap">{{ current.initiator }}</span></a-descriptions-item>
          <a-descriptions-item label="确认人">{{ confirmerOf(current) }}</a-descriptions-item>
          <a-descriptions-item label="审批人" :span="2">{{ current.approver }}</a-descriptions-item>
          <a-descriptions-item label="幂等键" :span="2"><span class="num">{{ current.idemKey }}</span></a-descriptions-item>
          <a-descriptions-item label="外部系统"><span class="ac-nowrap">{{ current.system }}</span></a-descriptions-item>
          <a-descriptions-item label="外部单号"><span class="num ac-nowrap">{{ current.extRef ?? '--' }}</span></a-descriptions-item>
        </a-descriptions>

        <div class="t-14 fw-600 ac-sub">执行记录</div>
        <StatusTimeline class="ac-tl" :items="timeline" />
      </div>

      <div class="ac-foot">
        <template v-if="readonly">
          <span class="t-14 c-3 ac-foot-note">{{ roReason }}，不能处理动作</span>
          <a-tooltip :title="roReason"><a-button disabled>{{ current.status === '结果未知' ? '发起对账' : current.status === '失败' ? '重新提交' : '确认提交' }}</a-button></a-tooltip>
        </template>

        <template v-else-if="current.status === '待确认' && notMine(current)">
          <span class="t-14 c-3 ac-foot-note">确认人为 {{ current.source.confirmer }}，你只能查看</span>
          <a-tooltip :title="notMineText(current)"><a-button disabled>撤回</a-button></a-tooltip>
          <a-tooltip :title="notMineText(current)"><a-button type="primary" disabled>确认提交</a-button></a-tooltip>
        </template>

        <template v-else-if="current.status === '待确认'">
          <a-popover v-model:open="withdrawOpen" trigger="click" placement="topRight">
            <template #content>
              <div class="ac-pop">
                <div class="t-14 c-2">撤回后该动作不再执行，也不会写外部系统。</div>
                <a-textarea v-model:value="withdrawReason" :rows="3" placeholder="撤回原因（必填）" />
                <div class="ac-pop-foot"><a-button size="small" type="primary" :disabled="!withdrawReason.trim()" @click="doWithdraw">撤回</a-button></div>
              </div>
            </template>
            <a-button>撤回</a-button>
          </a-popover>
          <a-button type="primary" @click="submit">确认提交</a-button>
        </template>

        <template v-else-if="current.status === '审批中'">
          <span class="t-14 c-3">等待 {{ current.approver }} 审批</span>
          <a-button v-if="live && current.can?.approve" type="primary" @click="approve">审批并执行</a-button>
        </template>
        <span v-else-if="current.status === '执行中'" class="t-14 c-3">等待 {{ current.system }} 返回，{{ waitingText(current.sysId) }}</span>

        <template v-else-if="current.status === '失败'">
          <span v-if="current.retriedAs" class="t-14 c-3">已重新提交为 <a class="num" @click="pick({ id: current!.retriedAs! })">{{ current.retriedAs }}</a></span>
          <template v-else>
            <a-popover trigger="click" placement="topRight">
              <template #content>
                <div class="ac-pop">
                  <div class="t-14 fw-600">{{ current.system }} 返回失败 <InfoTip text="修正原因后重新提交，会生成新动作和新幂等键。" /></div>
                  <div class="t-14 c-error">{{ current.failReason }}</div>
                  <div class="t-12"><span class="c-2">外部单号</span> <span class="c-1 num">{{ current.extRef ?? '--' }}</span><span class="c-2 ac-pop-gap">幂等键</span> <span class="c-1 num">{{ current.idemKey }}</span></div>
                </div>
              </template>
              <a-button>查看原因</a-button>
            </a-popover>
            <a-button type="primary" @click="resubmit">重新提交</a-button>
          </template>
        </template>

        <template v-else-if="current.status === '结果未知'">
          <span class="t-14 c-3 ac-foot-note">每 15 分钟按幂等键自动查询，仍无法确认</span>
          <a-button type="primary" @click="reconOpen = true">发起对账</a-button>
        </template>

        <span v-else class="t-14 c-3">{{ terminalText }}</span>
      </div>
    </div>

    <!-- 发起对账：与公共确认卡（ConfirmActionModal）同样式；公共卡只挂一个且不能放单选 + 必填说明，所以在页内单独实现 -->
    <a-modal v-if="current" :open="reconOpen" :width="520" :mask-closable="false" ok-text="提交对账" cancel-text="取消"
      :ok-button-props="{ disabled: !reconAs || !reconNote.trim() }" @ok="doReconcile" @cancel="reconOpen = false">
      <template #title><span class="t-16 fw-600">发起对账：{{ current.action }}</span><a-tag class="ca-tag">结果未知</a-tag></template>
      <div class="ca-target t-14 c-2">作用对象：<span class="c-1">{{ kindLabel(current.objKind) }} {{ current.objLabel }}</span></div>
      <a-descriptions :column="1" size="small" bordered :label-style="{ width: '96px' }">
        <a-descriptions-item label="动作编号"><span class="num">{{ current.id }}</span></a-descriptions-item>
        <a-descriptions-item label="外部系统"><span class="num">{{ current.system }} · {{ callOf(current.action, current.typeId) }}</span></a-descriptions-item>
        <a-descriptions-item label="外部单号"><span class="num ac-nowrap">{{ current.extRef ?? '--' }}</span></a-descriptions-item>
        <a-descriptions-item label="幂等键"><span class="num">{{ current.idemKey }}</span></a-descriptions-item>
      </a-descriptions>
      <div class="ca-impact t-14 c-2">系统已每 15 分钟按幂等键自动查询 {{ current.system }}，仍无法确认。请人工核对后选择结论；对账不会重试写入外部系统。</div>
      <div class="ca-row">
        <span class="t-14 c-2 ca-label">对账结论<span class="c-error">*</span></span>
        <a-radio-group v-model:value="reconAs" class="ac-radios">
          <a-radio value="外部已成功">外部已成功<span class="c-3">（标记为已对账）</span></a-radio>
          <a-radio value="外部未执行">外部未执行<span class="c-3">（标记为已对账，动作未生效）</span></a-radio>
          <a-radio value="仍无法确认">仍无法确认<span class="c-3">（保持结果未知，继续自动查询）</span></a-radio>
        </a-radio-group>
      </div>
      <div class="ca-row">
        <span class="t-14 c-2 ca-label">对账说明<span class="c-error">*</span></span>
        <a-textarea v-model:value="reconNote" :rows="3" placeholder="如核对人、外部查询结果" />
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.ac { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.ac-list { flex: 1; }
.ac-filters { display: flex; gap: 8px; }
.ac-w120 { width: 132px; }
.ac-w140 { width: 140px; }
.ac-w240 { width: 240px; }
.ac-table { flex: 1; min-height: 0; padding: 0 16px; }
.ac-table :deep(.ant-table-row) { cursor: pointer; }
.ac-table :deep(.ac-row-active > td) { background: var(--brand-bg) !important; }
.ac-pager { padding: 4px 16px 8px; }
.ac-detail { width: 480px; flex: none; }
.ac-body { flex: 1; padding: 12px 16px 0; }
.ac-out { margin-bottom: 8px; }
.ac-sub { margin: 16px 0 4px; }
.ac-tl :deep(.ant-timeline-item) { padding-bottom: 12px; }
.ac-pop { width: 300px; display: flex; flex-direction: column; gap: 8px; }
.ac-radios { display: flex; flex-direction: column; gap: 4px; }
.ac-unk { white-space: nowrap; }
.ac-nowrap { white-space: nowrap; }
.ca-tag { margin-inline-start: 8px; vertical-align: 2px; }
.ca-target { margin: 8px 0 12px; }
.ca-impact { margin: 12px 0; padding: 8px 12px; background: var(--g2); border: var(--border); border-radius: var(--radius); }
.ca-row { display: flex; align-items: flex-start; gap: 8px; margin: 12px 0; }
.ca-label { width: 96px; flex: none; }
.ac-pop-foot { display: flex; justify-content: flex-end; }
.ac-foot { flex: none; min-height: 57px; display: flex; align-items: center; justify-content: flex-end; gap: 8px; padding: 12px 16px; border-top: var(--border); }
.ac-foot-note { margin-right: auto; }
.ac-pop-gap { margin-left: 16px; }
</style>
