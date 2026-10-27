<script setup lang="ts">
/**
 * 动作类型：左侧动作列表，右侧详情。页签：概览 / 参数 / 提交条件 / 写回与副作用 / 执行记录 / 版本历史。
 * URL：?action= 选中项，?tab= 页签，?ppage/psize 参数分页，?rpage/rsize 执行记录分页，?rs= 执行结果筛选，?view=draft 草稿，?ver= 历史版本只读。
 * 规则、执行记录在当前页用抽屉打开（useDrawer），不跳转。
 * 草稿：草稿来源与视图切换用公共 parts/useDraftView（与指标口径一致）；草稿里的增删改与改动标记用本页 useDraftEdits。
 * 真接口（开关 action 打开）：左侧列表（分组 / 关键字 / 分页）走 GET /action-types；详情、参数、提交条件、写回、版本历史
 * 走 api/action.ts 的快照；执行记录与统计走 dynamic 的 /action-runs（dynamic 开关，取不到用 mock）；近 14 天执行图走
 * GET /action-types/{id}/stats/daily（取到之前 / 后台没起来用 mock runDaily）。引用规则走 /used-by（关系随版本，告警为实时统计）。
 */
import { computed, reactive, ref, watch, onScopeDispose } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import InfoTip from '@/components/InfoTip.vue';
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
  ACTION_TYPES, ACTION_GROUPS, RULE_ACTION, UNKNOWN_FOLLOWUP,
  runDaily, withSystem, ruleConfirmer,
  type ActionType, type RunStatus, type ActionParam, type SubmitCond, type OntologyEdit, type SideEffect,
} from '@/mock/actionEvent';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import NewActionEventForm from './parts/NewActionEventForm.vue';
import ActionTrialForm from './parts/ActionTrialForm.vue';
import ActionExecutionEditor from './parts/ActionExecutionEditor.vue';
import ActionParamFunctionEditor from './parts/ActionParamFunctionEditor.vue';
import { useDraftView, draftOf, forgetTarget } from './parts/useDraftView';
import { useDraftEdits, type DiffRow } from './useDraftEdits';
import { currentApiContext, isApiError } from '@/api/http';
import {
  actionApi, actionContextKey, snapshotReady, loadSnapshot, actionDetailReady, loadActionDetail, listActionParams, listActionTypes, actionTypes, paramsOf, condsOf, writebackOf, actionVersionsOf, writebackSystems, loadWritebackSystems, runsOf, runStats, getActionRunStats, listActionRuns, type ActionRunStats, type ActionTypeRow,
  getActionUsedBy, type ActionUsedBy, getRunDaily, type RunDaily, crWriter, crStart, crErrText, crWriteOn, crRestore,
} from '@/api/action';

const route = useRoute();
const router = useRouter();
const drawer = useDrawer();
const confirm = useConfirmStore();

/* 左侧列表 */
const group = ref('全部');
const kw = ref('');
const filtered = computed(() => ACTION_TYPES.filter((a) => (group.value === '全部' || a.group === group.value)
  && (!kw.value || a.name.includes(kw.value))));
const LPAGE = actionApi.isReal() ? 10 : 22;
const lpage = ref(1);
watch([group, kw], () => { lpage.value = 1; });

/* 真接口：列表分页 / 筛选走后台，目录只读轻头，选中类型才读详情；编辑须等完整基线，真模式空数据不借演示类型 */
const real = actionApi.isReal();
let mounted = true;
onScopeDispose(() => { mounted = false; });
const loadErr = ref('');
const realList = reactive<{ key: string; items: ActionTypeRow[]; total: number; groups: string[] }>({ key: '', items: [], total: 0, groups: [] });
const apiMsg = (e: unknown) => (isApiError(e) ? e.message : String(e));
// 写回 tab 外部系统下拉：integration 开关开着时取 ?writeback=1（和 action 开关独立）
watch(actionContextKey, () => { const key = actionContextKey(); loadWritebackSystems().catch((e) => { if (mounted && key === actionContextKey()) message.error(`外部系统加载失败：${apiMsg(e)}`); }); }, { immediate: true });
if (real) {
  watch(actionContextKey, () => { const key = actionContextKey(); loadErr.value = ''; loadSnapshot().catch((e) => { if (mounted && key === actionContextKey()) loadErr.value = apiMsg(e); }); }, { immediate: true });
  let seq = 0;
  watch([group, kw, lpage, actionContextKey], async () => {
    const n = ++seq; const key = actionContextKey();
    if (realList.key !== key) Object.assign(realList, { key, items: [], total: 0, groups: [] });
    realList.items = [];
    try {
      const r = await listActionTypes({ group: group.value, kw: kw.value, page: lpage.value, size: LPAGE });
      if (mounted && n === seq && key === actionContextKey()) { Object.assign(realList, { items: r.items, total: r.total, groups: r.groups }); if (r.page && r.page !== lpage.value) lpage.value = r.page; }
    } catch (e) { if (mounted && n === seq && key === actionContextKey()) message.error(`动作类型列表加载失败：${apiMsg(e)}`); }
  }, { immediate: true, flush: 'sync' });
}
/** 真实模式加载和错误均保持真实空态；演示数据仅用于显式 mock 模式。 */
const live = computed(() => real);
const ready = computed(() => !real || snapshotReady() && (!cur.value.id || actionDetailReady(cur.value.id, isDraft.value)));
const groups = computed(() => (live.value ? realList.key === actionContextKey() ? realList.groups : ['全部'] : ACTION_GROUPS));
const list = computed<ActionTypeRow[]>(() => (live.value ? realList.key === actionContextKey() ? realList.items : [] : paginate(filtered.value, { page: lpage.value, size: LPAGE }).items));
const listTotal = computed(() => (live.value ? realList.key === actionContextKey() ? realList.total : 0 : filtered.value.length));
const emptyAction: ActionType = { id: '', name: '', api: '', group: '', status: '已发布', version: '', target: '', targetApi: '', owner: '', desc: '', runners: '', approval: '', sysId: null, endpoint: '', idemKey: '', usedBy: [], system: '', timeout: '', retry: '', idempotent: false };
const cur = computed<ActionType>(() => {
  const all = actionTypes();
  return (route.query.action ? all.find((a) => a.id === route.query.action) : all[0]) ?? (real ? emptyAction : ACTION_TYPES[0]);
});
const statusColor = (s: ActionType['status']) => (s === '变更中' ? 'warning' : 'default');

const TABS = ['overview', 'params', 'conds', 'writeback', 'runs', 'versions'];
const tab = computed({
  get: () => (TABS.includes(route.query.tab as string) ? (route.query.tab as string) : 'overview'),
  set: (v) => router.replace({ query: { ...route.query, tab: v === 'overview' ? undefined : v, ppage: undefined, psize: undefined, rpage: undefined, rsize: undefined } }),
});

/* 草稿（公共 useDraftView：CR-0014 → 调整大区预算） */
const { draft, isDraft, editTip: viewTip, readonly, reason, setView, start, cancel } = useDraftView('action', () => cur.value.id);
if (real) watch([actionContextKey, () => cur.value.id, isDraft], async () => {
  const key = actionContextKey(), id = cur.value.id;
  loadErr.value = '';
  if (!id) return;
  try { await loadActionDetail(id, isDraft.value); }
  catch (e) { if (mounted && key === actionContextKey() && id === cur.value.id) loadErr.value = apiMsg(e); }
}, { immediate: true, flush: 'sync' });
const crTarget = () => ({ id: cur.value.id, name: `${cur.value.name}（${cur.value.api}）` });
const de = useDraftEdits({ draft, isDraft, editTip: viewTip, targetId: () => cur.value.id, targetName: () => `${cur.value.name}（${cur.value.api}）`, reviewer: '本体管理员 陈立军', writer: crWriter('action', crTarget), ready: () => ready.value });
/** 放弃草稿（确认卡在 useDraftEdits.discard）：写库模式下删库成功后用 cancel 撤掉本对象在登记表里的登记并切回已发布版；开关关着同以前（mock 登记不动） */
const onDiscard = () => de.discard(() => { const d = draft.value; if (crWriteOn() && d) cancel(d.id); else setView('published'); });
/* 刷新后恢复（写库模式）：按库里的草稿登记重新登记本页对象，库里已没有的 mock 登记去掉；本对象的草稿行由 useDraftEdits 读回 */
if (crWriteOn()) {
  const key = actionContextKey();
  crRestore('action').then(({ pairs, inDb }) => {
    if (!mounted || key !== actionContextKey()) return;
    for (const t of ACTION_TYPES) { const d = draftOf('action', t.id); if (d && !inDb(d.id, t.id)) forgetTarget(d.id, 'action', t.id); }
    for (const [cr, id] of pairs) start({ join: cr, target: id });
  }).catch((e) => message.error(`读取变更单草稿失败：${crErrText(e)}`));
}
/** 发起变更 / 加入：VITE_PALANTIR_CR_WRITE 开着时先写库（失败弹错，不进草稿），关着同以前 */
async function onStart(opts: { join?: string; title?: string; desc?: string }) {
  if (creating.value) { createOptions.value = opts; createOpen.value = true; creating.value = false; return; }
  try { start(await crStart({ kind: 'action', ...crTarget() }, opts)); } catch (e) { message.error(`发起变更失败：${crErrText(e)}`); }
}
const creating = ref(false);
const createOpen = ref(false);
const createOptions = ref<{ join?: string; title?: string; desc?: string }>({});
watch(() => route.query.createType, value => { if (real && value) createOpen.value = true; }, { immediate: true });
const { canEdit: draftCanEdit, editTip, dirty, bannerDraft } = de;
const canEdit = computed(() => draftCanEdit.value && ready.value);
const changeOpen = ref(false);
const changeTarget = ref('');
const openChange = (name: string, isNew = false) => { creating.value = isNew; changeTarget.value = name; changeOpen.value = true; };
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
const executionEditor = ref<{ validate: () => boolean } | null>(null);
const paramFunctionEditor = ref<{ validate: () => boolean } | null>(null);
const parameterComputed = computed(() => { try { return !!JSON.parse(editor.form.def)?.function; } catch { return false; } });
function openAdd(sec: SecName) { if (!canEdit.value) return; Object.assign(editor, { open: true, sec, key: null, form: FORMS[sec].blank() }); }
function openEdit(sec: SecName, key: string) {
  if (!canEdit.value) return;
  const row = SECS[sec].rows.value.find((r) => r.key === key);
  if (row) Object.assign(editor, { open: true, sec, key, form: { ...(row.data as object) } });
}
function saveEditor() {
  if (real && editor.sec === 'params' && !paramFunctionEditor.value?.validate()) {
    message.warning('请选择有效的计算函数、固定版本和输入参数'); return;
  }
  if (real && (editor.sec === 'edits' || editor.sec === 'effects') && !executionEditor.value?.validate()) {
    message.warning('请完成操作目标、字段取值和版本映射；无法执行的原说明需要重新配置'); return;
  }
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

const dryKey = () => JSON.stringify([actionContextKey(), cur.value.id, isDraft.value, draft.value?.id]);
const trialOpen = ref(false), hasTrialResult = ref(false);
watch(dryKey, () => { trialOpen.value = false; hasTrialResult.value = false; editor.open = false; });
async function dryRun() {
  if (real) {
    if (!ready.value || !cur.value.id || currentApiContext().previewAs || (isDraft.value && dirty.value)) return;
    trialOpen.value = true;
    return;
  }
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
const emptyStats = () => ({ total: 0, ok: 0, failed: 0, unknown: 0, pending: 0, confirm: 0, rate: 0, avgMs: 0 });
const remoteStats = ref<ActionRunStats>(emptyStats());
if (real) {
  let seq = 0;
  watch([() => cur.value.id, actionContextKey, ready], async () => {
    const n = ++seq, key = actionContextKey(), id = cur.value.id;
    remoteStats.value = emptyStats();
    if (!ready.value || !id) return;
    try { const value = await getActionRunStats(id); if (mounted && n === seq && key === actionContextKey() && id === cur.value.id) remoteStats.value = value; }
    catch (e) { if (mounted && n === seq && key === actionContextKey()) message.error(`执行统计加载失败：${apiMsg(e)}`); }
  }, { immediate: true, flush: 'sync' });
}
const stats = computed(() => real ? remoteStats.value : runStats(cur.value.id));
const statsAsOf = computed(() => real ? remoteStats.value.asOf : '2026-10-25 15:42');
const mockUsedBy = computed(() => cur.value.usedBy.map((id) => {
  const r = RULES[id];
  const alerts = ALERTS.filter((al) => al.ruleId === id);
  const gen = RULE_ACTION[id] === cur.value.id;
  return { id, name: r.name, trigger: r.trigger, mode: gen ? `命中后生成动作 · 待 ${ruleConfirmer(id)} 确认后提交` : '处理告警时手动执行', alerts: alerts.length, open: alerts.filter((al) => al.status === '待处理').length };
}));
const usedState = reactive<{ key: string; loading: boolean; error: string; items: ActionUsedBy[] }>({ key: '', loading: false, error: '', items: [] });
const usedContext = () => JSON.stringify([cur.value.id, currentApiContext()]);
let usedSeq = 0;
async function loadUsedBy() {
  const seq = ++usedSeq;
  const context = usedContext();
  Object.assign(usedState, { key: context, loading: false, error: '', items: [] });
  if (!live.value || !ready.value || !cur.value.id) return;
  usedState.loading = true;
  try {
    const items = await getActionUsedBy(cur.value.id);
    if (mounted && seq === usedSeq && context === usedContext()) usedState.items = items;
  } catch (e) {
    if (mounted && seq === usedSeq && context === usedContext()) usedState.error = apiMsg(e);
  } finally { if (mounted && seq === usedSeq && context === usedContext()) usedState.loading = false; }
}
watch([() => cur.value.id, ready, live, usedContext], loadUsedBy, { immediate: true });
const usedBy = computed(() => live.value ? (usedState.key === usedContext() ? usedState.items : []).map((r) => ({ id: r.ruleId, name: r.ruleName, trigger: r.trigger, mode: r.mode, alerts: r.alerts, open: r.openAlerts })) : mockUsedBy.value);
const usedCols = [
  { title: '规则', key: 'rule', width: 220 },
  { title: '触发', dataIndex: 'trigger', width: 96 },
  { title: '使用方式', dataIndex: 'mode', ellipsis: { showTitle: true } },
  { title: '告警（实时）', key: 'alerts', width: 120, align: 'right' as const },
];
/* 近 14 天执行量：真接口按选中类型取；typeId 对不上（切换中 / 草稿新类型 404）时读 mock */
const rd = reactive<{ key: string; items: RunDaily[] }>({ key: '', items: [] });
const dailyKey = () => JSON.stringify([actionContextKey(), cur.value.id]);
if (real) {
  let n = 0;
  watch([dailyKey, ready], async () => {
    const k = ++n; const key = dailyKey();
    Object.assign(rd, { key, items: [] });
    if (!ready.value || !cur.value.id) return;
    try { const items = await getRunDaily(cur.value.id); if (mounted && k === n && key === dailyKey()) rd.items = items; }
    catch (e) { if (mounted && k === n && key === dailyKey()) message.error(`执行趋势加载失败：${apiMsg(e)}`); }
  }, { immediate: true, flush: 'sync' });
}
const daily = computed(() => live.value ? rd.key === dailyKey() ? rd.items : [] : runDaily(cur.value.id));
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
const pq = usePagedQuery({ defaultSize: real ? 10 : 12, prefix: 'p' });
const paramPage = reactive({ items: [] as (ActionParam & { _key: string; _mark: string })[], total: 0 });
const paramsLoading = ref(false);
if (real) {
  let seq = 0, scope = '';
  watch([actionContextKey, () => cur.value.id, ready, isDraft, tab, pq.page, pq.size], async () => {
    const n = ++seq, key = actionContextKey(), id = cur.value.id;
    paramsLoading.value = false;
    const nextScope = JSON.stringify([key, id]);
    if (scope !== nextScope) { scope = nextScope; paramPage.items = []; paramPage.total = 0; }
    if (!ready.value || !id || isDraft.value || tab.value !== 'params') return;
    paramsLoading.value = true;
    try {
      const r = await listActionParams(id, pq.page.value, pq.size.value);
      if (!mounted || n !== seq || key !== actionContextKey()) return;
      paramPage.items = r.items.map(p => ({ ...p, _key: p.api, _mark: '' })); paramPage.total = r.total;
      if (r.page && r.page !== pq.page.value) pq.page.value = r.page;
    } catch (e) { if (mounted && n === seq && key === actionContextKey()) { paramPage.items = []; message.error(`参数加载失败：${apiMsg(e)}`); } }
    finally { if (mounted && n === seq && key === actionContextKey()) paramsLoading.value = false; }
  }, { immediate: true, flush: 'sync' });
}
const params = computed(() => real && !isDraft.value ? paramPage : paginate(flat(paramSec.rows.value), { page: pq.page.value, size: pq.size.value }));
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
  { title: '表达式', key: 'expr', ellipsis: true },
  { title: '不满足时提示', dataIndex: 'fail', width: 300, ellipsis: { showTitle: true } },
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
  { title: '字段', dataIndex: 'field', ellipsis: { showTitle: true } },
]));
const effectCols = computed(() => editCol([
  { title: '类型', dataIndex: 'kind', width: 88 },
  { title: '目标', dataIndex: 'target', width: 260, ellipsis: { showTitle: true } },
  { title: '时机', dataIndex: 'when', width: 200, ellipsis: { showTitle: true } },
  { title: '说明', dataIndex: 'note', ellipsis: { showTitle: true } },
]));

/* 执行记录 */
const RUN_FILTERS = ['全部', '待确认', '审批中', '执行中', '成功', '失败', '结果未知', '已对账', '已撤回'];
const runFilter = computed({
  get: () => (RUN_FILTERS.includes(route.query.rs as string) ? (route.query.rs as string) : '全部'),
  set: (v) => router.replace({ query: { ...route.query, rs: v === '全部' ? undefined : v, rpage: undefined } }),
});
const rq = usePagedQuery({ defaultSize: real ? 10 : 14, prefix: 'r' });
const runRows = computed(() => runsOf(cur.value.id).filter((r) => runFilter.value === '全部' || r.status === runFilter.value));
const remoteRuns = reactive({ items: [] as ReturnType<typeof runsOf>, total: 0 });
if (real) {
  let seq = 0, scope = '';
  watch([actionContextKey, () => cur.value.id, ready, tab, runFilter, rq.page, rq.size], async () => {
    const n = ++seq, key = actionContextKey(), id = cur.value.id;
    const nextScope = JSON.stringify([key, id, runFilter.value]);
    if (scope !== nextScope) { scope = nextScope; remoteRuns.items = []; remoteRuns.total = 0; }
    if (!ready.value || !id || tab.value !== 'runs') return;
    try {
      const r = await listActionRuns(id, { page: rq.page.value, size: rq.size.value, status: runFilter.value });
      if (!mounted || n !== seq || key !== actionContextKey()) return;
      Object.assign(remoteRuns, r); if (r.page && r.page !== rq.page.value) rq.page.value = r.page;
    } catch (e) { if (mounted && n === seq && key === actionContextKey()) { remoteRuns.items = []; message.error(`执行记录加载失败：${apiMsg(e)}`); } }
  }, { immediate: true, flush: 'sync' });
}
const runs = computed(() => real ? remoteRuns : paginate(runRows.value, { page: rq.page.value, size: rq.size.value }));
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
const runBadge = (s: RunStatus) => (s === '失败' ? 'error' : s === '待确认' ? 'warning' : 'default');

/* 版本历史 */
const versions = computed(() => actionVersionsOf(cur.value));
const verCols = [
  { title: '版本', dataIndex: 'ver', width: 80 },
  { title: '本体发布', dataIndex: 'release', width: 120 },
  { title: '状态', key: 'status', width: 96 },
  { title: '变更说明', dataIndex: 'change', ellipsis: { showTitle: true } },
  { title: '发布人', dataIndex: 'by', width: 96 },
  { title: '发布时间', dataIndex: 'at', width: 160 },
  { title: '操作', key: 'op', width: 110 },
];
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="!real ? '新建动作类型表单尚未接入' : readonly ? reason : !crWriteOn() ? '需开启真实变更单写入' : ''"><a-button :disabled="readonly || !crWriteOn()" @click="openChange('新建动作类型', true)">新建动作类型</a-button></a-tooltip>
  </Teleport>

  <a-empty v-if="live && ready && !cur.id" description="暂无动作类型或所选类型不存在" />
  <div v-else-if="!ready" class="panel at-wait">
    <span v-if="loadErr" class="c-3">动作类型加载失败：{{ loadErr }}</span><a-spin v-else />
  </div>
  <div v-else class="at">
    <div class="panel at-list">
      <div class="at-filter">
        <a-input-search v-model:value="kw" placeholder="搜索名称" class="at-kw" />
        <a-select v-model:value="group" :options="groups.map((g) => ({ value: g, label: g === '全部' ? '全部分组' : g }))" class="at-sel" />
      </div>
      <ul class="at-items">
        <a-tooltip v-for="a in list" :key="a.id" placement="right" :mouse-enter-delay="0.4" :title="`${a.api} → ${a.target} · ${a.group}`">
          <li :class="{ active: a.id === cur.id }" @click="pick(a.id)">
            <div class="at-row">
              <span class="t-14 at-name">{{ a.name }}</span>
              <span class="t-12 num" :class="a.status === '变更中' ? 'c-warning' : 'c-3'">{{ a.status === '已发布' ? '' : a.status + ' · ' }}{{ a.version }}</span>
            </div>
          </li>
        </a-tooltip>
      </ul>
      <div class="at-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="listTotal" :size="LPAGE" unit="个动作" class="at-pager" empty-text="没有匹配的动作类型" />
      </div>
    </div>

    <div class="panel at-detail">
      <DraftBanner v-if="isDraft && bannerDraft" :draft="bannerDraft"
        @submit="de.submit()" @discard="onDiscard()" />
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
        <a-tooltip :title="real ? isDraft && dirty ? '请先保存草稿，试运行读取数据库已保存内容' : '按当前定义填写参数和对象，只校验提交条件' : undefined"><span><a-button :disabled="cur.status === '草稿' || (real && (!!currentApiContext().previewAs || !ready || (isDraft && dirty)))" @click="dryRun">试运行</a-button></span></a-tooltip>
        <a-button v-if="real && hasTrialResult" type="link" @click="trialOpen = true">查看校验结果</a-button>
        <a-tooltip v-if="readonly" :title="reason"><a-button disabled>发起变更</a-button></a-tooltip>
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
        <div class="at-ov-content">
        <div class="at-ov-left">
          <div class="at-sec t-14 fw-600">基本信息</div>
          <a-descriptions :column="2" bordered size="small" class="at-desc">
            <a-descriptions-item label="目标对象">{{ cur.target }}（{{ cur.targetApi }}）</a-descriptions-item>
            <a-descriptions-item label="负责人">{{ cur.owner }}</a-descriptions-item>
            <a-descriptions-item label="说明" :span="2"><span class="at-ell" :title="cur.desc">{{ cur.desc }}</span></a-descriptions-item>
            <a-descriptions-item label="谁可以执行"><span class="at-ell" :title="cur.runners">{{ cur.runners }}</span></a-descriptions-item>
            <a-descriptions-item label="审批"><span class="at-ell" :title="cur.approval">{{ cur.approval }}</span></a-descriptions-item>
            <a-descriptions-item label="写回系统">{{ eff.system }}<span v-if="eff.sysId" class="num"> · {{ eff.sysId }}</span></a-descriptions-item>
            <a-descriptions-item label="写回接口"><span class="num">{{ eff.endpoint }}</span></a-descriptions-item>
            <a-descriptions-item label="幂等键规则" :span="2"><span class="num">{{ eff.idemKey }}</span></a-descriptions-item>
            <a-descriptions-item label="超时"><span class="at-ell" :title="eff.timeout">{{ eff.timeout }}</span></a-descriptions-item>
            <a-descriptions-item label="重试"><span class="at-ell" :title="eff.retry">{{ eff.retry }}</span></a-descriptions-item>
          </a-descriptions>
          <div class="at-sec t-14 fw-600 at-sec-row">
            <span>使用该动作的规则与告警<InfoTip text="点规则编号在当前页查看规则" /></span>
          </div>
          <div v-if="live && usedState.error" role="alert">引用规则加载失败：{{ usedState.error }} <a-button type="link" @click="loadUsedBy">重试</a-button></div>
          <a-table v-else :loading="live && (!ready || usedState.loading)" :columns="usedCols" :data-source="usedBy" :pagination="false" size="small" row-key="id"
            :locale="{ emptyText: live && (!ready || usedState.loading) ? '正在加载引用规则…' : '暂无规则使用，只能在对象详情里手动执行' }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'rule'"><ObjRef kind="rule" :id="record.id">{{ record.id }}</ObjRef><span class="c-1 at-gap">{{ record.name }}</span></template>
              <template v-else-if="column.key === 'alerts'">
                <span class="num">{{ record.alerts }}</span><span v-if="record.open" class="t-12 c-warning num">（待处理 {{ record.open }}）</span>
              </template>
            </template>
          </a-table>
          <div class="at-sec t-14 fw-600 at-sec-row">
            <span>提交条件 {{ liveConds.length }} 项<InfoTip text="表达式见“提交条件”页签" /></span>
          </div>
          <div v-for="c in liveConds.slice(0, 5)" :key="c._key" class="at-cond">
            <span class="t-12 c-3">{{ c.kind }}</span><span class="t-14 c-1">{{ c.name }}</span><span class="t-12 c-3 at-cond-f" :title="c.fail">{{ c.fail }}</span>
          </div>
        </div>
        <div class="at-ov-right">
          <div class="at-sec t-14 fw-600"><span>近 30 天执行</span><span v-if="statsAsOf" class="fw-400 c-2"> · 截至 {{ statsAsOf }}</span></div>
          <div class="at-stats">
            <div><span class="t-12 c-3">执行次数</span><span class="t-16 fw-600 c-1 num">{{ fmtInt(stats.total) }}</span></div>
            <div><span class="t-12 c-3">成功率</span><span class="t-16 fw-600 c-1 num">{{ stats.total ? fmtPct(stats.rate, 1) : '--' }}</span></div>
            <div><span class="t-12 c-3">失败</span><span class="t-16 fw-600 num" :class="stats.failed ? 'c-error' : 'c-1'">{{ stats.failed }}</span></div>
            <div><span class="t-12 c-3">结果未知 · 待对账</span><span class="t-16 fw-600 num" :class="stats.unknown ? 'c-warning' : 'c-1'">{{ stats.unknown }}</span></div>
          </div>
          <template v-if="stats.total">
            <div class="at-chart"><EChart :option="chartOption" /></div>
            <div class="at-sec t-14 fw-600">最近异常</div>
            <div v-for="r in recentAbnormal" :key="r.id" class="at-abn" :title="`${r.id} · ${r.at} · ${r.status === '结果未知' ? '结果未知 · 待对账' : r.code}`">
              <a class="t-14 num" @click="drawer.open('action', r.id)">{{ r.id }}</a>
              <span class="t-12 c-2 num">{{ r.at }}</span>
              <span class="t-12 at-abn-st" :class="r.status === '失败' ? 'c-error' : 'c-warning'" :title="r.status === '结果未知' ? '结果未知 · 待对账' : r.code">
                <QuestionCircleOutlined v-if="r.status === '结果未知'" />{{ r.status === '结果未知' ? '结果未知 · 待对账' : r.code }}
              </span>
            </div>
          </template>
          <a-empty v-else class="at-empty" description="草稿未发布，暂无执行记录" />
        </div>
        </div>
      </div>

      <!-- 参数 -->
      <div v-else-if="tab === 'params'" class="at-body">
        <div class="at-tool">
          <span class="t-14 c-1">{{ real && (!ready || paramsLoading) ? '参数加载中…' : `参数 ${params.total} 个` }}</span><InfoTip text="提交时按顺序校验；对象引用类参数在执行面板里用对象选择器填写" />
          <span class="at-sp" />
          <template v-if="isDraft">
            <span class="t-12" :class="dirty ? 'c-warning' : 'c-3'">{{ dirty ? '有未保存的修改' : de.locked.value ? '已提交审核，草稿已锁定' : '草稿已保存' }}</span>
            <a-button size="small" :disabled="!dirty" @click="de.revert()">撤销修改</a-button>
            <a-button size="small" :disabled="!dirty || !ready" @click="de.save()">保存草稿</a-button>
          </template>
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit" @click="openAdd('params')">新增参数</a-button></a-tooltip>
        </div>
        <div class="paged-scroll" :class="{ 'is-overflow': pq.overflow.value }">
          <a-table :loading="real && (!ready || paramsLoading)" :columns="paramCols" :data-source="params.items" :pagination="false" size="middle" row-key="_key" :row-class-name="delRow" :locale="{ emptyText: real && (!ready || paramsLoading) ? '正在读取参数…' : '暂无参数' }">
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
          <span class="t-14 c-1">提交条件 {{ liveConds.length }} 项</span><InfoTip text="全部满足才允许提交；条件模板分三类：当前用户、参数、上下文对象" />
          <span class="at-sp" />
          <template v-if="isDraft">
            <span class="t-12" :class="dirty ? 'c-warning' : 'c-3'">{{ dirty ? '有未保存的修改' : de.locked.value ? '已提交审核，草稿已锁定' : '草稿已保存' }}</span>
            <a-button size="small" :disabled="!dirty" @click="de.revert()">撤销修改</a-button>
            <a-button size="small" :disabled="!dirty || !ready" @click="de.save()">保存草稿</a-button>
          </template>
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit" @click="openAdd('conds')">新增条件</a-button></a-tooltip>
        </div>
        <div class="paged-scroll">
        <a-table :columns="condCols" :data-source="conds" :pagination="false" size="middle" row-key="_key" :row-class-name="delRow">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'expr'"><span class="at-code t-12" :title="record.expr">{{ record.expr }}</span></template>
            <template v-else-if="column.key === '_mark'"><a-tag v-if="record._mark">{{ record._mark }}</a-tag></template>
              <template v-else-if="column.key === '_op'">
                <a-space v-if="record._mark === '删除'"><a :class="{ 'at-dis': !canEdit }" @click="restoreItem('conds', record._key)">恢复</a></a-space>
                <a-space v-else><a :class="{ 'at-dis': !canEdit }" @click="openEdit('conds', record._key)">编辑</a><a :class="{ 'at-dis': !canEdit }" @click="delItem('conds', record._key)">删除</a></a-space>
              </template>
          </template>
        </a-table>
        </div>
      </div>

      <!-- 写回与副作用 -->
      <div v-else-if="tab === 'writeback'" class="at-body at-ov">
        <div class="at-ov-content">
        <div class="at-ov-left">
          <div class="at-sec t-14 fw-600 at-sec-row">
            <span>写回（同步，决定动作结果）<a-tag v-if="cfgMark" class="at-mark">{{ cfgMark }}</a-tag></span>
            <span class="at-tool">
              <template v-if="isDraft">
                <span class="t-12" :class="dirty ? 'c-warning' : 'c-3'">{{ dirty ? '有未保存的修改' : de.locked.value ? '已提交审核，草稿已锁定' : '草稿已保存' }}</span>
                <a-button size="small" :disabled="!dirty" @click="de.revert()">撤销修改</a-button>
                <a-button size="small" :disabled="!dirty || !ready" @click="de.save()">保存草稿</a-button>
              </template>
              <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit" @click="openEdit('config', 'cfg')">编辑写回</a-button></a-tooltip>
            </span>
          </div>
          <a-descriptions :column="2" bordered size="small" class="at-desc">
            <a-descriptions-item label="外部系统">
              {{ eff.system }}<span v-if="eff.sysId" class="num"> · {{ eff.sysId }}</span><span v-if="cfgMark && cfgBase && cfgBase.sysId !== eff.sysId"> · 原 {{ withSystem({ ...cur, ...cfgBase }).system }}</span>
            </a-descriptions-item>
            <a-descriptions-item label="接口"><span class="num">{{ eff.endpoint }}</span></a-descriptions-item>
            <a-descriptions-item label="幂等键" :span="2"><span class="num">{{ eff.idemKey }}</span><span v-if="sys"> · 请求头 {{ sys.idemKey }}</span></a-descriptions-item>
            <a-descriptions-item label="超时"><span class="at-ell" :title="eff.timeout">{{ eff.timeout }}</span></a-descriptions-item>
            <a-descriptions-item label="重试"><span class="at-ell" :title="eff.retry">{{ eff.retry }}</span></a-descriptions-item>
            <a-descriptions-item label="超时处理" :span="2">
              <span v-if="internal" class="c-2">本体内部编辑，不存在结果未知</span>
              <span v-else class="c-warning at-ell" :title="`结果未知 · 待对账：不重试，${UNKNOWN_FOLLOWUP}`"><QuestionCircleOutlined /> 结果未知 · 待对账：不重试，{{ UNKNOWN_FOLLOWUP }}</span>
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
      </div>

      <!-- 执行记录 -->
      <div v-else-if="tab === 'runs'" class="at-body">
        <div class="at-tool">
          <a-segmented v-model:value="runFilter" :options="RUN_FILTERS" size="small" />
          <InfoTip text="规则生成的动作先待确认，确认人确认后才提交；点编号查看过程" />
        </div>
        <div class="paged-scroll">
          <a-table :columns="runCols" :data-source="runs.items" :pagination="false" size="middle" row-key="id"
            :locale="{ emptyText: cur.status === '草稿' ? '草稿未发布，暂无执行记录' : '没有符合条件的执行记录' }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'id'"><a class="num" @click="drawer.open('action', record.id)">{{ record.id }}</a></template>
              <template v-else-if="column.key === 'obj'">
                <span :title="record.objLabel"><ObjRef v-if="record.objKind" :kind="record.objKind" :id="record.objId">{{ record.objLabel }}</ObjRef>
                <span v-else class="num">{{ record.objLabel }}</span></span>
              </template>
              <template v-else-if="column.key === 'initiator'">
                <ObjRef v-if="record.ruleId" kind="rule" :id="record.ruleId">规则 {{ record.ruleId }}</ObjRef>
                <span v-else>{{ record.initiator }}</span>
              </template>
              <template v-else-if="column.key === 'status'">
                <span v-if="record.status === '结果未知'" class="c-warning at-nw"><QuestionCircleOutlined /> 结果未知 · 待对账</span>
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
        <div class="paged-scroll">
        <a-table :columns="verCols" :data-source="versions" :pagination="false" size="middle" row-key="ver">
          <template #headerCell="{ column }"><template v-if="column.key === 'status'">状态<InfoTip text="动作类型版本只随本体整版发布生效；进行中的执行按提交时的版本完成，不受新版本影响" /></template></template>
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'status'"><a-badge :status="'default'" :text="record.status" /></template>
            <template v-else-if="column.key === 'op'">
              <a-space><a-tooltip title="版本对比暂未开放"><a class="at-dis">对比</a></a-tooltip><a-tooltip v-if="record.status !== '生效中'" :title="readonly ? reason : '回滚暂未开放'"><a class="at-dis">回滚</a></a-tooltip></a-space>
            </template>
          </template>
        </a-table>
        </div>
      </div>
    </div>
  </div>
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTarget" @ok="onStart" />
  <NewActionEventForm v-if="real" v-model:open="createOpen" kind="action" :options="createOptions" :row-forms="FORMS" />
  <ActionTrialForm v-if="real && cur.id" :key="dryKey()" v-model:open="trialOpen" :action="cur" :cr="isDraft ? draft?.id : undefined"
    :disabled="!ready || !!currentApiContext().previewAs || (isDraft && dirty) || cur.status === '草稿'" @result="hasTrialResult = true" />
  <a-modal v-model:open="editor.open" :title="`${editor.key === null ? '新增' : '编辑'}${editorDef.title}`" ok-text="确定" cancel-text="取消" :width="real && (editor.sec === 'edits' || editor.sec === 'effects') ? 820 : 520" @ok="saveEditor">
    <ActionExecutionEditor v-if="real && editor.open && (editor.sec === 'edits' || editor.sec === 'effects')" ref="executionEditor" v-model="editor.form" :section="editor.sec" :action="cur" :params="flat(paramSec.rows.value)" />
    <a-form v-else layout="vertical" class="at-form">
      <ActionParamFunctionEditor v-if="real && editor.open && editor.sec === 'params'" ref="paramFunctionEditor" v-model="editor.form.def" :params="flat(paramSec.rows.value)" :parameter-api="editor.form.api" />
      <a-form-item v-for="f in editorDef.fields().filter(f => !(real && editor.sec === 'params' && f.k === 'def' && parameterComputed))" :key="f.k" :label="f.label" :required="f.required">
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
.at-wait { display: flex; align-items: center; justify-content: center; min-height: 240px; }
.at-filter { padding: 12px 12px 8px; display: flex; gap: 8px; flex: none; }
.at-kw { flex: 1; min-width: 0; }
.at-sel { width: 108px; flex: none; }
.at-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; overflow: auto; }
.at-items li { padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.at-items li:hover { background: var(--g3); }
.at-items li.active { background: var(--brand-bg); }
.at-items li.active .at-name { color: var(--brand); }
.at-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
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
.at-ov { overflow: auto; }
.at-ov-content { display: flex; gap: 16px; flex: none; min-height: 100%; }
.at-ov-left { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.at-ov-right { width: 400px; flex: none; display: flex; flex-direction: column; gap: 8px; padding-left: 16px; border-left: var(--border); }
.at-sec { margin-top: 4px; }
.at-sec-row { display: flex; justify-content: space-between; align-items: center; }
.at-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.at-stats > div { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; padding: 6px 12px; border: var(--border); border-radius: var(--radius); white-space: nowrap; }
.at-desc.at-desc :deep(.ant-descriptions-view > table) { table-layout: fixed; width: 100%; }
.at-desc :deep(.ant-descriptions-item-label) { width: 96px; white-space: nowrap; }
.at-desc :deep(.ant-descriptions-item-content) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.at-ell { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.at-nw { white-space: nowrap; }
.at-chart { height: 180px; }
.at-abn { display: grid; grid-template-columns: auto auto 1fr; column-gap: 12px; align-items: center; padding: 6px 0; border-bottom: var(--border); white-space: nowrap; }
.at-abn-st { min-width: 0; overflow: hidden; text-overflow: ellipsis; text-align: right; }
.at-empty { margin-top: 32px; }
.at-tool { display: flex; align-items: center; gap: 8px; flex: none; }
.at-code { font-family: Menlo, Consolas, "Noto Sans Mono CJK SC", monospace; color: var(--g9); }
.at-gap { margin-left: 4px; }
.at-cond { display: grid; grid-template-columns: 96px 160px 1fr; gap: 8px; align-items: center; padding: 6px 0; border-bottom: var(--border); }
.at-cond-f { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.at-dis { color: var(--g7); cursor: not-allowed; }
.at-mark { margin-left: 8px; }
.at-form :deep(.ant-form-item) { margin-bottom: 12px; }
:deep(.at-del td) { text-decoration: line-through; color: var(--g7); }
</style>
