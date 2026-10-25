<script setup lang="ts">
/**
 * 动作类型：左侧动作列表，右侧详情。页签：概览 / 参数 / 提交条件 / 写回与副作用 / 执行记录 / 版本历史。
 * URL：?action= 选中项，?tab= 页签，?ppage/psize 参数分页，?rpage/rsize 执行记录分页，?rs= 执行结果筛选，?view=draft 草稿，?ver= 历史版本只读。
 * 规则、执行记录在当前页用抽屉打开（useDrawer），不跳转。
 * 草稿：草稿来源与视图切换用公共 parts/useDraftView（与指标口径一致）；草稿里的增删改与改动标记用本页 useDraftEdits。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import ObjRef from '@/components/ObjRef.vue';
import EChart from '@/components/EChart.vue';
import StatusTimeline from '@/components/StatusTimeline.vue';
import { useDrawer } from '@/composables/useDrawer';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { useConfirmStore } from '@/stores/confirm';
import { GRAY, STATUS } from '@/theme';
import { paginate } from '@/mock/paginate';
import { fmtPct, fmtInt } from '@/mock/format';
import { RULES, ALERTS } from '@/mock/dynamic';
import { extSystemById } from '@/mock/extsys';
import {
  ACTION_TYPES, ACTION_GROUPS, ACTION_VERSION_NOTES, RULE_ACTION, UNKNOWN_FOLLOWUP, paramsOf, condsOf, writebackOf,
  runsOf, runStats, runDaily, versionsOf, withSystem, writebackSystems, ruleConfirmers,
  type ActionType, type RunStatus, type ActionParam, type SubmitCond, type OntologyEdit, type SideEffect,
} from '@/mock/actionEvent';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import { useDraftView } from './parts/useDraftView';
import { useDraftEdits, type DiffRow } from './useDraftEdits';

const route = useRoute();
const router = useRouter();
const drawer = useDrawer();
const confirm = useConfirmStore();

/* 左侧列表 */
const group = ref('全部');
const kw = ref('');
const filtered = computed(() => ACTION_TYPES.filter((a) => (group.value === '全部' || a.group === group.value)
  && (!kw.value || a.name.includes(kw.value))));
const LPAGE = 14;
const lpage = ref(1);
watch([group, kw], () => { lpage.value = 1; });
const list = computed(() => paginate(filtered.value, { page: lpage.value, size: LPAGE }).items);
const cur = computed<ActionType>(() => ACTION_TYPES.find((a) => a.id === route.query.action) ?? ACTION_TYPES[0]);
const statusColor = (s: ActionType['status']) => (s === '已发布' ? 'success' : s === '变更中' ? 'warning' : 'default');

const TABS = ['overview', 'params', 'conds', 'writeback', 'runs', 'versions'];
const tab = computed({
  get: () => (TABS.includes(route.query.tab as string) ? (route.query.tab as string) : 'overview'),
  set: (v) => router.replace({ query: { ...route.query, tab: v === 'overview' ? undefined : v, ppage: undefined, psize: undefined, rpage: undefined, rsize: undefined } }),
});

/* 草稿（公共 useDraftView：CR-0014 → 调整大区预算） */
const { draft, isDraft, editTip: viewTip, readonly, reason, setView, start } = useDraftView('action', () => cur.value.id);
const de = useDraftEdits({ draft, isDraft, editTip: viewTip, targetId: () => cur.value.id, targetName: () => `${cur.value.name}（${cur.value.api}）`, reviewer: '本体管理员 陈立军' });
const { canEdit, editTip, dirty, bannerDraft } = de;
const changeOpen = ref(false);
const changeTarget = ref('');
const openChange = (name: string) => { changeTarget.value = name; changeOpen.value = true; };
/** 切换左侧选中项：清掉抽屉与分页；有未保存修改先确认 */
const pick = (id: string) => de.guard(() => router.replace({ query: { ...route.query, action: id, view: undefined, drawer: undefined, drawer2: undefined, ppage: undefined, psize: undefined, rpage: undefined, rsize: undefined, rs: undefined } }));
const onView = (v: 'published' | 'draft') => (v === 'published' ? de.guard(() => setView('published')) : setView(v));

/* 草稿里可编辑的部分 */
const paramSec = de.section<ActionParam>('params', () => paramsOf(cur.value), (p) => p.api);
const condSec = de.section<SubmitCond>('conds', () => condsOf(cur.value), (c) => c.name);
const editSec = de.section<OntologyEdit>('edits', () => writebackOf(cur.value).edits, (e) => `${e.op}:${e.obj}`);
const effSec = de.section<SideEffect>('effects', () => writebackOf(cur.value).effects, (e) => e.target);
interface WbCfg { sysId: string | null; endpoint: string; idemKey: string }
const cfgSec = de.section<WbCfg>('config', () => [{ sysId: cur.value.sysId, endpoint: cur.value.endpoint, idemKey: cur.value.idemKey }], () => 'cfg');
const SECS = { params: paramSec, conds: condSec, edits: editSec, effects: effSec, config: cfgSec } as const;
type SecName = keyof typeof SECS;
const flat = <T extends object>(rows: DiffRow<T>[]) => rows.map((r) => ({ ...r.data, _key: r.key, _mark: r.mark }));
const editCol = (cols: any[]) => (isDraft.value ? [...cols, { title: '改动', key: '_mark', width: 64 }, { title: '操作', key: '_op', width: 96 }] : cols);
const delRow = (r: { _mark: string }) => (r._mark === '删除' ? 'at-del' : '');
/** 草稿里生效的定义（写回目标等取草稿值），用于概览、写回页 */
const eff = computed<ActionType>(() => withSystem({ ...cur.value, ...cfgSec.rows.value[0].data }));
const cfgMark = computed(() => cfgSec.rows.value[0].mark);
const cfgBase = computed(() => cfgSec.rows.value[0].base);

/* 行编辑弹窗（参数 / 提交条件 / 本体编辑 / 副作用 / 写回配置共用） */
interface FieldDef { k: string; label: string; kind?: 'input' | 'select' | 'switch' | 'textarea'; options?: { value: any; label: string }[]; required?: boolean }
const PARAM_TYPES = ['字符串', '整数', '金额', '日期', '日期时间', '枚举', '枚举列表', '文本', '布尔', `对象引用 · 门店`, '对象引用 · 商品', '对象引用 · 订单', '对象引用 · 会员', '对象引用 · 告警'];
const FORMS: Record<SecName, { title: string; fields: () => FieldDef[]; blank: () => any }> = {
  params: { title: '参数', blank: () => ({ name: '', api: '', type: '字符串', required: false, def: '—', rule: '—' }), fields: () => [
    { k: 'name', label: '参数名', required: true }, { k: 'api', label: 'API 名', required: true },
    { k: 'type', label: '类型', kind: 'select', options: PARAM_TYPES.map((t) => ({ value: t, label: t })) },
    { k: 'required', label: '必填', kind: 'switch' }, { k: 'def', label: '默认值' }, { k: 'rule', label: '校验' }] },
  conds: { title: '提交条件', blank: () => ({ kind: '参数', name: '', expr: '', fail: '' }), fields: () => [
    { k: 'kind', label: '模板', kind: 'select', options: ['当前用户', '参数', '上下文对象'].map((t) => ({ value: t, label: t })) },
    { k: 'name', label: '条件名', required: true }, { k: 'expr', label: '表达式', kind: 'textarea', required: true }, { k: 'fail', label: '不满足时提示', required: true }] },
  edits: { title: '本体编辑', blank: () => ({ op: '修改', obj: '', field: '' }), fields: () => [
    { k: 'op', label: '操作', kind: 'select', options: ['修改', '新建', '关联'].map((t) => ({ value: t, label: t })) },
    { k: 'obj', label: '对象', required: true }, { k: 'field', label: '字段', required: true }] },
  effects: { title: '副作用', blank: () => ({ kind: '通知', target: '', when: '写回成功后', note: '' }), fields: () => [
    { k: 'kind', label: '类型', kind: 'select', options: ['通知', '事件', 'Webhook'].map((t) => ({ value: t, label: t })) },
    { k: 'target', label: '目标', required: true }, { k: 'when', label: '时机', required: true }, { k: 'note', label: '说明' }] },
  config: { title: '写回配置', blank: () => ({}), fields: () => [
    { k: 'sysId', label: '外部系统', kind: 'select', options: [{ value: null, label: '本体（无外部写回）' }, ...writebackSystems().map((s) => ({ value: s.id, label: `${s.name}（${s.id}）` }))] },
    { k: 'endpoint', label: '接口', required: true }, { k: 'idemKey', label: '幂等键规则', required: true }] },
};
const editor = reactive<{ open: boolean; sec: SecName; key: string | null; form: Record<string, any> }>({ open: false, sec: 'params', key: null, form: {} });
const editorDef = computed(() => FORMS[editor.sec]);
function openAdd(sec: SecName) { if (!canEdit.value) return; Object.assign(editor, { open: true, sec, key: null, form: FORMS[sec].blank() }); }
function openEdit(sec: SecName, key: string) {
  if (!canEdit.value) return;
  const row = SECS[sec].rows.value.find((r) => r.key === key);
  if (row) Object.assign(editor, { open: true, sec, key, form: { ...(row.data as object) } });
}
function saveEditor() {
  const miss = editorDef.value.fields().find((f) => f.required && !String(editor.form[f.k] ?? '').trim());
  if (miss) { message.warning(`请填写${miss.label}`); return; }
  const sec = SECS[editor.sec] as { add: (d: any) => boolean; update: (k: string, d: any) => void };
  if (editor.sec === 'config' && !editor.form.sysId) Object.assign(editor.form, { endpoint: '—' });
  if (editor.key === null) { if (!sec.add({ ...editor.form })) { message.warning('已存在同名项'); return; } }
  else sec.update(editor.key, { ...editor.form });
  editor.open = false;
}
const delItem = (sec: SecName, key: string) => canEdit.value && SECS[sec].remove(key);
const restoreItem = (sec: SecName, key: string) => canEdit.value && SECS[sec].restore(key);

async function dryRun() {
  const a = eff.value;
  const ok = await confirm.ask({
    title: `试运行：${a.name}`, target: `${a.target}（使用示例对象）`, tag: '试运行',
    fields: paramsOf(a).filter((p) => p.required).slice(0, 5).map((p) => ({ label: p.name, value: p.def })),
    impact: `只校验提交条件并模拟调用 ${a.system}，不会写回外部系统，也不会修改本体对象。`,
    okText: '开始试运行',
  });
  if (ok) message.success(`试运行通过：${condSec.rows.value.filter((c) => c.mark !== '删除').length} 项提交条件全部满足`);
}

/* 概览 */
const stats = computed(() => runStats(cur.value.id));
const usedBy = computed(() => cur.value.usedBy.map((id) => {
  const r = RULES[id];
  const alerts = ALERTS.filter((al) => al.ruleId === id);
  const gen = RULE_ACTION[id] === cur.value.id;
  return { id, name: r.name, trigger: r.trigger, mode: gen ? `命中后生成动作 · 待 ${ruleConfirmers(id).join(' / ')} 确认后提交` : '处理告警时手动执行', alerts: alerts.length, open: alerts.filter((al) => al.status === '待处理').length };
}));
const usedCols = [
  { title: '规则', key: 'rule', width: 220 },
  { title: '触发', dataIndex: 'trigger', width: 96 },
  { title: '使用方式', dataIndex: 'mode' },
  { title: '近期告警', key: 'alerts', width: 120, align: 'right' as const },
];
const daily = computed(() => runDaily(cur.value.id));
const chartOption = computed(() => ({
  grid: { left: 32, right: 8, top: 28, bottom: 24 },
  legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: GRAY.g8, fontSize: 12 } },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: daily.value.map((d) => d.date), axisLine: { lineStyle: { color: GRAY.g6 } }, axisTick: { show: false }, axisLabel: { color: GRAY.g8, fontSize: 12, interval: 3 } },
  yAxis: { type: 'value', minInterval: 1, axisLabel: { color: GRAY.g8, fontSize: 12 }, splitLine: { lineStyle: { color: GRAY.g4 } } },
  series: [
    { name: '成功', type: 'bar', stack: 'r', barWidth: 10, data: daily.value.map((d) => d.ok), itemStyle: { color: GRAY.g7 } },
    { name: '结果未知', type: 'bar', stack: 'r', data: daily.value.map((d) => d.unknown), itemStyle: { color: STATUS.warning } },
    { name: '失败', type: 'bar', stack: 'r', data: daily.value.map((d) => d.failed), itemStyle: { color: STATUS.error } },
  ],
}));
const recentAbnormal = computed(() => runsOf(cur.value.id).filter((r) => r.status === '失败' || r.status === '结果未知').slice(0, 4));

/* 参数 */
const pq = usePagedQuery({ defaultSize: 12, prefix: 'p' });
const params = computed(() => paginate(flat(paramSec.rows.value), { page: pq.page.value, size: pq.size.value }));
const paramCols = computed(() => editCol([
  { title: '参数', dataIndex: 'name', width: 150 },
  { title: 'API 名', dataIndex: 'api', width: 150 },
  { title: '类型', dataIndex: 'type', width: 180 },
  { title: '必填', key: 'required', width: 72 },
  { title: '默认值', dataIndex: 'def', width: 200, ellipsis: { showTitle: true } },
  { title: '校验', dataIndex: 'rule', ellipsis: { showTitle: true } },
]));

/* 提交条件 */
const conds = computed(() => flat(condSec.rows.value));
const liveConds = computed(() => conds.value.filter((c) => c._mark !== '删除'));
const condCols = computed(() => editCol([
  { title: '模板', dataIndex: 'kind', width: 110 },
  { title: '条件', dataIndex: 'name', width: 140 },
  { title: '表达式', key: 'expr' },
  { title: '不满足时提示', dataIndex: 'fail', width: 300 },
]));

/* 写回与副作用 */
const wbEdits = computed(() => flat(editSec.rows.value));
const wbEffects = computed(() => flat(effSec.rows.value));
const sys = computed(() => extSystemById(eff.value.sysId));
const internal = computed(() => !sys.value);
const flow = computed(() => {
  const a = eff.value;
  const ruleGen = a.usedBy.some((id) => RULE_ACTION[id] === a.id);
  const items: { time: string; text: string; desc?: string; status?: 'done' | 'warn' | 'error' | 'unknown' }[] = [];
  const n = () => String(items.length + 1);
  if (ruleGen) items.push({ time: n(), text: '规则生成 ⇒ 待确认', desc: '规则命中只生成动作，确认人确认后才提交；确认前可撤回', status: 'warn' });
  items.push({ time: n(), text: '校验提交条件', desc: `${liveConds.value.length} 项，任一不满足即拒绝提交` });
  if (a.approval !== '免审') items.push({ time: n(), text: '审批', desc: a.approval });
  if (sys.value) {
    items.push({ time: n(), text: `调用 ${sys.value.name}（同步）`, desc: `超时 ${a.timeout}；幂等键 ${sys.value.idemKey}` });
    items.push({ time: n(), text: '外部明确返回失败 ⇒ 自动重试', desc: sys.value.retry, status: 'error' });
    items.push({ time: n(), text: '超时或无响应 ⇒ 结果未知 · 待对账', desc: `不重试；${UNKNOWN_FOLLOWUP}`, status: 'unknown' });
  }
  items.push({ time: n(), text: '提交本体编辑', desc: '写回成功后才提交，保证本体与外部系统一致' });
  items.push({ time: n(), text: '执行副作用（异步）', desc: '通知、事件；失败不影响动作结果' });
  return items;
});
const editCols = computed(() => editCol([
  { title: '操作', dataIndex: 'op', width: 72 },
  { title: '对象', dataIndex: 'obj', width: 180 },
  { title: '字段', dataIndex: 'field' },
]));
const effectCols = computed(() => editCol([
  { title: '类型', dataIndex: 'kind', width: 88 },
  { title: '目标', dataIndex: 'target', width: 260 },
  { title: '时机', dataIndex: 'when', width: 150 },
  { title: '说明', dataIndex: 'note' },
]));

/* 执行记录 */
const RUN_FILTERS = ['全部', '待确认', '审批中', '执行中', '成功', '失败', '结果未知', '已对账', '已撤回'];
const runFilter = computed({
  get: () => (RUN_FILTERS.includes(route.query.rs as string) ? (route.query.rs as string) : '全部'),
  set: (v) => router.replace({ query: { ...route.query, rs: v === '全部' ? undefined : v, rpage: undefined } }),
});
const rq = usePagedQuery({ defaultSize: 14, prefix: 'r' });
const runRows = computed(() => runsOf(cur.value.id).filter((r) => runFilter.value === '全部' || r.status === runFilter.value));
const runs = computed(() => paginate(runRows.value, { page: rq.page.value, size: rq.size.value }));
const runCols = [
  { title: '时间', dataIndex: 'at', width: 172 },
  { title: '执行记录', key: 'id', width: 170 },
  { title: '目标对象', key: 'obj', width: 200, ellipsis: true },
  { title: '参数摘要', dataIndex: 'summary', ellipsis: { showTitle: true } },
  { title: '发起人', key: 'initiator', width: 120 },
  { title: '确认人', dataIndex: 'confirmer', width: 80 },
  { title: '来源', dataIndex: 'source', width: 84 },
  { title: '结果', key: 'status', width: 150 },
  { title: '耗时', key: 'ms', width: 80, align: 'right' as const },
];
const runBadge = (s: RunStatus) => (s === '失败' ? 'error' : s === '待确认' || s === '审批中' || s === '执行中' ? 'warning' : 'default');

/* 版本历史 */
const versions = computed(() => versionsOf(cur.value.version, cur.value.owner, ACTION_VERSION_NOTES));
const verCols = [
  { title: '版本', dataIndex: 'ver', width: 80 },
  { title: '本体发布', dataIndex: 'release', width: 120 },
  { title: '状态', key: 'status', width: 96 },
  { title: '变更说明', dataIndex: 'change' },
  { title: '发布人', dataIndex: 'by', width: 96 },
  { title: '发布时间', dataIndex: 'at', width: 160 },
  { title: '操作', key: 'op', width: 110 },
];
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly" @click="openChange('新建动作类型')">新建动作类型</a-button></a-tooltip>
  </Teleport>

  <div class="at">
    <div class="panel at-list">
      <div class="at-filter">
        <a-input-search v-model:value="kw" placeholder="搜索名称" class="at-kw" />
        <a-select v-model:value="group" :options="ACTION_GROUPS.map((g) => ({ value: g, label: g === '全部' ? '全部分组' : g }))" class="at-sel" />
      </div>
      <ul class="at-items">
        <li v-for="a in list" :key="a.id" :class="{ active: a.id === cur.id }" @click="pick(a.id)">
          <div class="at-row">
            <span class="t-14 at-name">{{ a.name }}</span>
            <span class="t-12 num" :class="a.status === '变更中' ? 'c-warning' : 'c-3'">{{ a.status === '已发布' ? '' : a.status + ' · ' }}{{ a.version }}</span>
          </div>
          <div class="at-row">
            <span class="t-12 c-3 num">{{ a.api }} → {{ a.target }}</span>
            <span class="t-12 c-3">{{ a.group }}</span>
          </div>
        </li>
      </ul>
      <div class="at-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="filtered.length" :size="LPAGE" unit="个动作" class="at-pager" empty-text="没有匹配的动作类型" />
      </div>
    </div>

    <div class="panel at-detail">
      <DraftBanner v-if="isDraft && bannerDraft" :draft="bannerDraft"
        @submit="de.submit()" @discard="de.discard(() => setView('published'))" />
      <div class="at-head">
        <span class="t-16 fw-600 c-1">{{ cur.name }}</span>
        <span class="t-14 c-3 num">{{ cur.api }}</span>
        <span class="t-14 c-2">目标对象 {{ cur.target }}</span>
        <span class="t-14 c-3">{{ cur.version }}</span>
        <a-tag :color="statusColor(cur.status)">{{ cur.status }}</a-tag>
        <a-segmented v-if="draft && !readonly" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => onView(v)" />
        <span class="at-sp" />
        <a-button :disabled="cur.status === '草稿'" @click="dryRun">试运行</a-button>
        <a-tooltip v-if="readonly" :title="reason"><a-button type="primary" disabled>发起变更</a-button></a-tooltip>
        <a-button v-else-if="!draft" type="primary" @click="openChange(`${cur.name}（${cur.api}）`)">发起变更</a-button>
        <a-button v-else-if="!isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
      </div>
      <a-tabs v-model:activeKey="tab" class="at-tabs">
        <a-tab-pane key="overview" tab="概览" />
        <a-tab-pane key="params" tab="参数" />
        <a-tab-pane key="conds" tab="提交条件" />
        <a-tab-pane key="writeback" tab="写回与副作用" />
        <a-tab-pane key="runs" tab="执行记录" />
        <a-tab-pane key="versions" tab="版本历史" />
      </a-tabs>

      <!-- 概览 -->
      <div v-if="tab === 'overview'" class="at-body at-ov">
        <div class="at-ov-left">
          <div class="at-sec t-14 fw-600">基本信息</div>
          <a-descriptions :column="2" bordered size="small" class="at-desc">
            <a-descriptions-item label="目标对象">{{ cur.target }}（{{ cur.targetApi }}）</a-descriptions-item>
            <a-descriptions-item label="负责人">{{ cur.owner }}</a-descriptions-item>
            <a-descriptions-item label="说明" :span="2">{{ cur.desc }}</a-descriptions-item>
            <a-descriptions-item label="谁可以执行">{{ cur.runners }}</a-descriptions-item>
            <a-descriptions-item label="审批">{{ cur.approval }}</a-descriptions-item>
            <a-descriptions-item label="写回系统">{{ eff.system }}<span v-if="eff.sysId" class="t-12 c-3 num"> · {{ eff.sysId }}</span></a-descriptions-item>
            <a-descriptions-item label="写回接口"><span class="num">{{ eff.endpoint }}</span></a-descriptions-item>
            <a-descriptions-item label="幂等键规则" :span="2"><span class="num">{{ eff.idemKey }}</span></a-descriptions-item>
            <a-descriptions-item label="超时">{{ eff.timeout }}</a-descriptions-item>
            <a-descriptions-item label="重试">{{ eff.retry }}</a-descriptions-item>
          </a-descriptions>
          <div class="at-sec t-14 fw-600 at-sec-row">
            <span>使用该动作的规则与告警</span><span class="t-12 c-3 fw-400">点规则编号在当前页查看规则</span>
          </div>
          <a-table :columns="usedCols" :data-source="usedBy" :pagination="false" size="small" row-key="id"
            :locale="{ emptyText: '暂无规则使用，只能在对象详情里手动执行' }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'rule'"><ObjRef kind="rule" :id="record.id">{{ record.id }}</ObjRef><span class="c-1 at-gap">{{ record.name }}</span></template>
              <template v-else-if="column.key === 'alerts'">
                <span class="num">{{ record.alerts }}</span><span v-if="record.open" class="t-12 c-warning num">（待处理 {{ record.open }}）</span>
              </template>
            </template>
          </a-table>
          <div class="at-sec t-14 fw-600 at-sec-row">
            <span>提交条件 {{ liveConds.length }} 项</span><span class="t-12 c-3 fw-400">表达式见“提交条件”页签</span>
          </div>
          <div v-for="c in liveConds.slice(0, 5)" :key="c._key" class="at-cond">
            <span class="t-12 c-3">{{ c.kind }}</span><span class="t-14 c-1">{{ c.name }}</span><span class="t-12 c-3 at-cond-f">{{ c.fail }}</span>
          </div>
        </div>
        <div class="at-ov-right">
          <div class="at-sec t-14 fw-600 at-sec-row"><span>近 30 天执行</span><span class="t-12 c-3 fw-400">截至 2026-10-25 15:42</span></div>
          <div class="at-stats">
            <div><div class="t-12 c-3">执行次数</div><div class="t-20 fw-600 c-1 num">{{ fmtInt(stats.total) }}</div></div>
            <div><div class="t-12 c-3">成功率</div><div class="t-20 fw-600 c-1 num">{{ stats.total ? fmtPct(stats.rate, 1) : '--' }}</div></div>
            <div><div class="t-12 c-3">失败</div><div class="t-20 fw-600 num" :class="stats.failed ? 'c-error' : 'c-1'">{{ stats.failed }}</div></div>
            <div><div class="t-12 c-3">结果未知 · 待对账</div><div class="t-20 fw-600 num" :class="stats.unknown ? 'c-warning' : 'c-1'">{{ stats.unknown }}</div></div>
          </div>
          <template v-if="stats.total">
            <div class="at-chart"><EChart :option="chartOption" /></div>
            <div class="at-sec t-14 fw-600">最近异常</div>
            <div v-for="r in recentAbnormal" :key="r.id" class="at-abn">
              <a class="t-14 num" @click="drawer.open('action', r.id)">{{ r.id }}</a>
              <span class="t-12 c-3 num">{{ r.at }}</span>
              <span class="t-12 at-abn-st" :class="r.status === '失败' ? 'c-error' : 'c-warning'">
                <QuestionCircleOutlined v-if="r.status === '结果未知'" />{{ r.status === '结果未知' ? '结果未知 · 待对账' : r.code }}
              </span>
            </div>
          </template>
          <a-empty v-else class="at-empty" description="草稿未发布，暂无执行记录" />
        </div>
      </div>

      <!-- 参数 -->
      <div v-else-if="tab === 'params'" class="at-body">
        <div class="at-tool">
          <span class="t-12 c-3">提交时按顺序校验；对象引用类参数在执行面板里用对象选择器填写</span>
          <span class="at-sp" />
          <template v-if="isDraft">
            <span class="t-12" :class="dirty ? 'c-warning' : 'c-3'">{{ dirty ? '有未保存的修改' : de.locked.value ? '已提交审核，草稿已锁定' : '草稿已保存' }}</span>
            <a-button size="small" :disabled="!dirty" @click="de.revert()">撤销修改</a-button>
            <a-button size="small" :disabled="!dirty" @click="de.save()">保存草稿</a-button>
          </template>
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit" @click="openAdd('params')">新增参数</a-button></a-tooltip>
        </div>
        <div class="paged-scroll" :class="{ 'is-overflow': pq.overflow.value }">
          <a-table :columns="paramCols" :data-source="params.items" :pagination="false" size="middle" row-key="_key" :row-class-name="delRow">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'required'">{{ record.required ? '是' : '否' }}</template>
              <template v-else-if="column.key === '_mark'"><a-tag v-if="record._mark">{{ record._mark }}</a-tag></template>
              <template v-else-if="column.key === '_op'">
                <a-space v-if="record._mark === '删除'"><a :class="{ 'at-dis': !canEdit }" @click="restoreItem('params', record._key)">恢复</a></a-space>
                <a-space v-else><a :class="{ 'at-dis': !canEdit }" @click="openEdit('params', record._key)">编辑</a><a :class="{ 'at-dis': !canEdit }" @click="delItem('params', record._key)">删除</a></a-space>
              </template>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="params.total" :default-size="pq.defaultSize" :empty="false" />
      </div>

      <!-- 提交条件 -->
      <div v-else-if="tab === 'conds'" class="at-body">
        <div class="at-tool">
          <span class="t-12 c-3">全部满足才允许提交；条件模板分三类：当前用户、参数、上下文对象</span>
          <span class="at-sp" />
          <template v-if="isDraft">
            <span class="t-12" :class="dirty ? 'c-warning' : 'c-3'">{{ dirty ? '有未保存的修改' : de.locked.value ? '已提交审核，草稿已锁定' : '草稿已保存' }}</span>
            <a-button size="small" :disabled="!dirty" @click="de.revert()">撤销修改</a-button>
            <a-button size="small" :disabled="!dirty" @click="de.save()">保存草稿</a-button>
          </template>
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit" @click="openAdd('conds')">新增条件</a-button></a-tooltip>
        </div>
        <a-table :columns="condCols" :data-source="conds" :pagination="false" size="middle" row-key="_key" :row-class-name="delRow">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'expr'"><span class="at-code t-12">{{ record.expr }}</span></template>
            <template v-else-if="column.key === '_mark'"><a-tag v-if="record._mark">{{ record._mark }}</a-tag></template>
              <template v-else-if="column.key === '_op'">
                <a-space v-if="record._mark === '删除'"><a :class="{ 'at-dis': !canEdit }" @click="restoreItem('conds', record._key)">恢复</a></a-space>
                <a-space v-else><a :class="{ 'at-dis': !canEdit }" @click="openEdit('conds', record._key)">编辑</a><a :class="{ 'at-dis': !canEdit }" @click="delItem('conds', record._key)">删除</a></a-space>
              </template>
          </template>
        </a-table>
      </div>

      <!-- 写回与副作用 -->
      <div v-else-if="tab === 'writeback'" class="at-body at-ov">
        <div class="at-ov-left">
          <div class="at-sec t-14 fw-600 at-sec-row">
            <span>写回（同步，决定动作结果）<a-tag v-if="cfgMark" class="at-mark">{{ cfgMark }}</a-tag></span>
            <span class="at-tool">
              <template v-if="isDraft">
                <span class="t-12" :class="dirty ? 'c-warning' : 'c-3'">{{ dirty ? '有未保存的修改' : de.locked.value ? '已提交审核，草稿已锁定' : '草稿已保存' }}</span>
                <a-button size="small" :disabled="!dirty" @click="de.revert()">撤销修改</a-button>
                <a-button size="small" :disabled="!dirty" @click="de.save()">保存草稿</a-button>
              </template>
              <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit" @click="openEdit('config', 'cfg')">编辑写回</a-button></a-tooltip>
            </span>
          </div>
          <a-descriptions :column="2" bordered size="small">
            <a-descriptions-item label="外部系统">
              {{ eff.system }}<span v-if="eff.sysId" class="t-12 c-3 num"> · {{ eff.sysId }}</span>
              <span v-if="cfgMark && cfgBase && cfgBase.sysId !== eff.sysId" class="t-12 c-3 at-was">原 {{ withSystem({ ...cur, ...cfgBase }).system }}</span>
            </a-descriptions-item>
            <a-descriptions-item label="接口"><span class="num">{{ eff.endpoint }}</span></a-descriptions-item>
            <a-descriptions-item label="幂等键" :span="2"><span class="num">{{ eff.idemKey }}</span><span v-if="sys" class="t-12 c-3"> · 请求头 {{ sys.idemKey }}</span></a-descriptions-item>
            <a-descriptions-item label="超时">{{ eff.timeout }}</a-descriptions-item>
            <a-descriptions-item label="重试">{{ eff.retry }}</a-descriptions-item>
            <a-descriptions-item label="超时处理" :span="2">
              <span v-if="internal" class="c-2">本体内部编辑，不存在结果未知</span>
              <span v-else class="c-warning"><QuestionCircleOutlined /> 结果未知 · 待对账：不重试，{{ UNKNOWN_FOLLOWUP }}</span>
            </a-descriptions-item>
          </a-descriptions>
          <div class="at-sec t-14 fw-600 at-sec-row">
            <span>本体编辑（写回成功后提交）</span>
            <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit" @click="openAdd('edits')">新增编辑</a-button></a-tooltip>
          </div>
          <a-table :columns="editCols" :data-source="wbEdits" :pagination="false" size="small" row-key="_key" :row-class-name="delRow">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === '_mark'"><a-tag v-if="record._mark">{{ record._mark }}</a-tag></template>
              <template v-else-if="column.key === '_op'">
                <a-space v-if="record._mark === '删除'"><a :class="{ 'at-dis': !canEdit }" @click="restoreItem('edits', record._key)">恢复</a></a-space>
                <a-space v-else><a :class="{ 'at-dis': !canEdit }" @click="openEdit('edits', record._key)">编辑</a><a :class="{ 'at-dis': !canEdit }" @click="delItem('edits', record._key)">删除</a></a-space>
              </template>
            </template>
          </a-table>
          <div class="at-sec t-14 fw-600 at-sec-row">
            <span>副作用（异步，失败不影响动作结果）</span>
            <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit" @click="openAdd('effects')">新增副作用</a-button></a-tooltip>
          </div>
          <a-table :columns="effectCols" :data-source="wbEffects" :pagination="false" size="small" row-key="_key" :row-class-name="delRow">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === '_mark'"><a-tag v-if="record._mark">{{ record._mark }}</a-tag></template>
              <template v-else-if="column.key === '_op'">
                <a-space v-if="record._mark === '删除'"><a :class="{ 'at-dis': !canEdit }" @click="restoreItem('effects', record._key)">恢复</a></a-space>
                <a-space v-else><a :class="{ 'at-dis': !canEdit }" @click="openEdit('effects', record._key)">编辑</a><a :class="{ 'at-dis': !canEdit }" @click="delItem('effects', record._key)">删除</a></a-space>
              </template>
            </template>
          </a-table>
        </div>
        <div class="at-ov-right">
          <div class="at-sec t-14 fw-600">执行顺序</div>
          <StatusTimeline :items="flow" />
        </div>
      </div>

      <!-- 执行记录 -->
      <div v-else-if="tab === 'runs'" class="at-body">
        <div class="at-tool">
          <a-segmented v-model:value="runFilter" :options="RUN_FILTERS" size="small" />
          <span class="at-sp" />
          <span class="t-12 c-3">规则生成的动作先待确认，确认人确认后才提交；点编号查看过程</span>
        </div>
        <div class="paged-scroll">
          <a-table :columns="runCols" :data-source="runs.items" :pagination="false" size="middle" row-key="id"
            :locale="{ emptyText: cur.status === '草稿' ? '草稿未发布，暂无执行记录' : '没有符合条件的执行记录' }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'id'"><a class="num" @click="drawer.open('action', record.id)">{{ record.id }}</a></template>
              <template v-else-if="column.key === 'obj'">
                <ObjRef v-if="record.objKind" :kind="record.objKind" :id="record.objId">{{ record.objLabel }}</ObjRef>
                <span v-else class="num">{{ record.objLabel }}</span>
              </template>
              <template v-else-if="column.key === 'initiator'">
                <ObjRef v-if="record.ruleId" kind="rule" :id="record.ruleId">规则 {{ record.ruleId }}</ObjRef>
                <span v-else>{{ record.initiator }}</span>
              </template>
              <template v-else-if="column.key === 'status'">
                <span v-if="record.status === '结果未知'" class="c-warning"><QuestionCircleOutlined /> 结果未知 · 待对账</span>
                <a-badge v-else :status="runBadge(record.status)" :text="record.status" />
              </template>
              <template v-else-if="column.key === 'ms'"><span class="num">{{ (record.ms / 1000).toFixed(2) }} 秒</span></template>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="rq.page.value" v-model:size="rq.size.value" variant="full" :total="runs.total" :default-size="rq.defaultSize" :empty="false" />
      </div>

      <!-- 版本历史 -->
      <div v-else class="at-body">
        <a-table :columns="verCols" :data-source="versions" :pagination="false" size="middle" row-key="ver">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'status'"><a-badge :status="'default'" :text="record.status" /></template>
            <template v-else-if="column.key === 'op'">
              <a-space><a>对比</a><a-tooltip v-if="record.status !== '生效中'" :title="reason"><a :class="{ 'at-dis': readonly }">回滚</a></a-tooltip></a-space>
            </template>
          </template>
        </a-table>
        <div class="t-12 c-3">动作类型版本只随本体整版发布生效；进行中的执行按提交时的版本完成，不受新版本影响。</div>
      </div>
    </div>
  </div>
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTarget" @ok="start" />
  <a-modal v-model:open="editor.open" :title="`${editor.key === null ? '新增' : '编辑'}${editorDef.title}`" ok-text="确定" cancel-text="取消" :width="520" @ok="saveEditor">
    <a-form layout="vertical" class="at-form">
      <a-form-item v-for="f in editorDef.fields()" :key="f.k" :label="f.label" :required="f.required">
        <a-select v-if="f.kind === 'select'" v-model:value="editor.form[f.k]" :options="f.options" />
        <a-switch v-else-if="f.kind === 'switch'" v-model:checked="editor.form[f.k]" />
        <a-textarea v-else-if="f.kind === 'textarea'" v-model:value="editor.form[f.k]" :rows="2" />
        <a-input v-else v-model:value="editor.form[f.k]" :disabled="f.k === 'endpoint' && editor.sec === 'config' && !editor.form.sysId" />
      </a-form-item>
    </a-form>
    <div class="t-12 c-3">改动先保存在本地，点“保存草稿”后计入 {{ draft?.id }} 的改动数。</div>
  </a-modal>
</template>

<style scoped>
.at { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.at-list { width: 300px; flex: none; }
.at-filter { padding: 12px 12px 8px; display: flex; gap: 8px; flex: none; }
.at-kw { flex: 1; min-width: 0; }
.at-sel { width: 108px; flex: none; }
.at-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; }
.at-items li { padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.at-items li:hover { background: var(--g3); }
.at-items li.active { background: var(--brand-bg); }
.at-items li.active .at-name { color: var(--brand); }
.at-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.at-pager { flex: 1; min-height: 0; }
.at-foot { padding: 8px 12px 8px 16px; border-top: var(--border); flex: none; display: flex; align-items: center; }
.at-detail { flex: 1; min-width: 0; }
.at-head { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; flex: none; }
.at-head :deep(.ant-tag) { margin-inline-end: 0; }
.at-sp { flex: 1; }
.at-tabs { padding: 0 16px; flex: none; }
.at-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.at-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
.at-ov { flex-direction: row; gap: 16px; }
.at-ov-left { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.at-ov-right { width: 400px; flex: none; display: flex; flex-direction: column; gap: 8px; padding-left: 16px; border-left: var(--border); }
.at-sec { margin-top: 4px; }
.at-sec-row { display: flex; justify-content: space-between; align-items: center; }
.at-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.at-stats > div { padding: 8px 12px; border: var(--border); border-radius: var(--radius); }
.at-chart { height: 180px; }
.at-abn { display: grid; grid-template-columns: 170px 1fr; column-gap: 8px; padding: 6px 0; border-bottom: var(--border); }
.at-abn-st { grid-column: 1 / 3; display: inline-flex; gap: 4px; align-items: center; }
.at-empty { margin-top: 32px; }
.at-tool { display: flex; align-items: center; gap: 8px; flex: none; }
.at-code { font-family: Menlo, Consolas, "Noto Sans Mono CJK SC", monospace; color: var(--g9); }
.at-gap { margin-left: 4px; }
.at-cond { display: grid; grid-template-columns: 96px 160px 1fr; gap: 8px; align-items: center; padding: 6px 0; border-bottom: var(--border); }
.at-cond-f { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.at-dis { color: var(--g7); cursor: not-allowed; }
.at-mark { margin-left: 8px; }
.at-was { margin-left: 8px; }
.at-form :deep(.ant-form-item) { margin-bottom: 12px; }
:deep(.at-del td) { text-decoration: line-through; color: var(--g7); }
</style>
