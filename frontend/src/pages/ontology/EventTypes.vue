<script setup lang="ts">
/**
 * 事件类型：左侧事件列表，右侧详情。页签：概览 / 载荷字段 / 订阅方 / 近期事件 / 版本历史。
 * URL：?event= 选中项，?tab= 页签，?fpage/fsize 载荷字段分页，?range= 时间范围 + ?epage= 近期事件分页，?view=draft 草稿，?ver= 历史版本只读。
 * 真接口（开关 action 打开）：左侧列表（来源 / 关键字 / 分页）走 GET /event-types；详情（含 specific）、载荷字段、订阅方、
 * 版本历史走 api/action.ts 的快照；近期事件（GET /event-types/{id}/events，按时间范围 / 结果 / 页码取）、
 * 概览的最近事件 / 运行健康 / 近 14 天事件数（…/events 第 1 页、…/stats）按选中类型取，真模式加载或失败为空态。
 * 规则名、草稿写入（versioning）仍读 mock。
 */
import PayloadSchemaEditor from './PayloadSchemaEditor.vue';
import PayloadValueEditor from './PayloadValueEditor.vue';
import { computed, reactive, ref, watch, onScopeDispose } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import InfoTip from '@/components/InfoTip.vue';
import ObjRef from '@/components/ObjRef.vue';
import EChart from '@/components/EChart.vue';
import EventDeliveries from '@/components/EventDeliveries.vue';
import DeliveryExceptions from '@/components/DeliveryExceptions.vue';
import { useDrawer } from '@/composables/useDrawer';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { useUnsavedGuard } from '@/composables/useUnsavedGuard';
import { useConfirmStore } from '@/stores/confirm';
import { GRAY } from '@/theme';
import { paginate } from '@/mock/paginate';
import { fmtInt } from '@/mock/format';
import { RULES } from '@/mock/dynamic';
import {
  EVENT_TYPES, EVENT_SOURCES, eventsOf, eventDaily,
  type EventType, type EventOutcome, type PayloadField, type Subscriber,
} from '@/mock/actionEvent';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import NewActionEventForm from './parts/NewActionEventForm.vue';
import { useAssistantDraft } from '@/composables/useAssistantDraft';
import { useDraftView, draftOf, forgetTarget } from './parts/useDraftView';
import { draftFields, useStaticCr } from './parts/useStaticCr';
import { useDraftEdits, type DiffRow } from './useDraftEdits';
import { isApiError } from '@/api/http';
import {
  actionApi, actionContextKey, snapshotReady, loadSnapshot, invalidateActionSnapshot, eventDetailReady, loadEventDetail, listEventPayload, listEventTypes, eventTypes, payloadOf, subscribersOf, eventVersionsOf, type EventTypeRow,
  listEvents, getEventStats, simulateEvent, type EventSimulation, type EventStats, crWriter, crStart, crErrText, crWriteOn, crRestore, crChanges,
} from '@/api/action';
import type { EventRecord } from '@/api/action';

const route = useRoute();
const router = useRouter();
const drawer = useDrawer();
const confirm = useConfirmStore();

/* 左侧列表 */
const source = ref('全部');
const kw = ref('');
const filtered = computed(() => EVENT_TYPES.filter((e) => (source.value === '全部' || e.source === source.value)
  && (!kw.value || e.name.includes(kw.value))));
const LPAGE = actionApi.isReal() ? 10 : 22;
const lpage = ref(1);
watch([source, kw], () => { lpage.value = 1; });

/* 真接口：列表分页 / 筛选走后台，目录只读轻头，选中类型才读详情；编辑须等完整基线，真模式空数据不借演示类型 */
const real = actionApi.isReal();
let mounted = true;
onScopeDispose(() => { mounted = false; });
const loadErr = ref('');
const realList = reactive<{ key: string; items: EventTypeRow[]; total: number; sources: string[] }>({ key: '', items: [], total: 0, sources: [] });
const apiMsg = (e: unknown) => (isApiError(e) ? e.message : String(e));
if (real) {
  watch(actionContextKey, () => { const key = actionContextKey(); loadErr.value = ''; loadSnapshot().catch((e) => { if (mounted && key === actionContextKey()) loadErr.value = apiMsg(e); }); }, { immediate: true });
  let seq = 0;
  watch([source, kw, lpage, actionContextKey], async () => {
    const n = ++seq; const key = actionContextKey();
    if (realList.key !== key) Object.assign(realList, { key, items: [], total: 0, sources: [] });
    realList.items = [];
    try {
      const r = await listEventTypes({ source: source.value, kw: kw.value, page: lpage.value, size: LPAGE });
      if (mounted && n === seq && key === actionContextKey()) { Object.assign(realList, { items: r.items, total: r.total, sources: r.sources }); if (r.page && r.page !== lpage.value) lpage.value = r.page; }
    } catch (e) { if (mounted && n === seq && key === actionContextKey()) message.error(`事件类型列表加载失败：${apiMsg(e)}`); }
  }, { immediate: true, flush: 'sync' });
}
/** 真实模式加载和错误均保持真实空态；演示数据仅用于显式 mock 模式。 */
const live = computed(() => real);
const ready = computed(() => !real || snapshotReady() && (!cur.value.id || eventDetailReady(cur.value.id, isDraft.value)));
const sources = computed(() => (live.value ? realList.key === actionContextKey() ? realList.sources : ['全部'] : EVENT_SOURCES));
const list = computed<EventTypeRow[]>(() => (live.value ? realList.key === actionContextKey() ? realList.items : [] : paginate(filtered.value, { page: lpage.value, size: LPAGE }).items));
const listTotal = computed(() => (live.value ? realList.key === actionContextKey() ? realList.total : 0 : filtered.value.length));
const emptyEvent: EventType = { id: '', name: '', api: '', source: '数据同步', sourceRef: '', object: '', objectApi: '', status: '已发布', version: '', owner: '', desc: '', dedupKey: '', dedupWindow: '', retention: '', delivery: '', ordering: '', weekly: 0, specific: [] };
const cur = computed<EventType>(() => {
  const all = eventTypes();
  return (route.query.event ? all.find((e) => e.id === route.query.event) : all[0]) ?? (real ? emptyEvent : EVENT_TYPES[0]);
});
const RESET = { view: undefined, fpage: undefined, fsize: undefined, epage: undefined };
const statusColor = (s: EventType['status']) => (s === '变更中' ? 'warning' : 'default');

const TABS = ['overview', 'payload', 'subs', 'recent', 'exceptions', 'versions'];
const tab = computed({
  get: () => (TABS.includes(route.query.tab as string) ? (route.query.tab as string) : 'overview'),
  set: (v) => router.replace({ query: { ...route.query, ...RESET, view: route.query.view, tab: v === 'overview' ? undefined : v } }),
});

/* 保存即生效：真实模式下不建草稿，已发布定义直接可编辑，保存后立即生成版本 */
const view = useDraftView('event', () => cur.value.id);
const { readonly, reason } = view;
const isDraft = computed(() => (real ? !readonly.value : view.isDraft.value));
const draft = computed(() => (real ? undefined : view.draft.value));
const viewTip = view.editTip;
const { setView, start, cancel } = view;
if (real) watch([actionContextKey, () => cur.value.id, isDraft], async () => {
  const key = actionContextKey(), id = cur.value.id;
  loadErr.value = '';
  if (!id) return;
  try { await loadEventDetail(id, isDraft.value); }
  catch (e) { if (mounted && key === actionContextKey() && id === cur.value.id) loadErr.value = apiMsg(e); }
}, { immediate: true, flush: 'sync' });
const crTarget = () => ({ id: cur.value.id, name: `${cur.value.name}（${cur.value.api}）` });
const dedupCr = useStaticCr('event', crTarget, { draft, start, setView, cancel });
const dedupValues = computed(() => ({ dedupKey: cur.value.dedupKey, dedupWindow: cur.value.dedupWindow,
  ...(isDraft.value ? draftFields(dedupCr.items.value, 'def') : {}) }));
const dedupOpen = ref(false), dedupSaving = ref(false);
const dedupForm = reactive({ dedupKey: '', dedupWindow: '' });
const dedupSnapshot = ref('');
const dedupDirty = computed(() => dedupOpen.value && JSON.stringify(dedupForm) !== dedupSnapshot.value);
const dedupGuard = useUnsavedGuard(dedupDirty);
const canEditDedup = computed(() => real && isDraft.value && !readonly.value && ready.value
  && !dedupCr.loading.value && !dedupCr.error.value && dedupCr.head.value?.status === '草稿');
function editDedup() {
  if (!canEditDedup.value) return;
  Object.assign(dedupForm, { dedupKey: String(dedupValues.value.dedupKey ?? ''), dedupWindow: String(dedupValues.value.dedupWindow ?? '') });
  dedupSnapshot.value = JSON.stringify(dedupForm); dedupOpen.value = true;
}
async function saveDedup() {
  if (!canEditDedup.value || dedupSaving.value || !dedupDirty.value) return;
  const key = actionContextKey(), id = cur.value.id;
  dedupSaving.value = true;
  try {
    if (await dedupCr.save('def', (['dedupKey', 'dedupWindow'] as const)
      .filter(field => dedupForm[field] !== String(dedupValues.value[field] ?? ''))
      .map(field => ({ key: field, op: 'update', before: cur.value[field], after: dedupForm[field] })))) {
      if (key === actionContextKey() && id === cur.value.id) {
        if (dedupCr.head.value) crChanges[dedupCr.head.value.id] = dedupCr.head.value.changes;
        dedupOpen.value = false; message.success('去重设置已保存到草稿');
      }
    }
  } finally { dedupSaving.value = false; }
}
watch([actionContextKey, () => cur.value.id], () => { dedupOpen.value = false; });
const de = useDraftEdits({ draft, isDraft, editTip: viewTip, targetId: () => cur.value.id, targetName: () => `${cur.value.name}（${cur.value.api}）`, writer: crWriter('event', crTarget), ready: () => ready.value, direct: real, readonly, onSaved: async () => { const id = cur.value.id; invalidateActionSnapshot(); await loadEventDetail(id, true); } });
/** 放弃草稿（确认卡在 useDraftEdits.discard）：写库模式下删库成功后用 cancel 撤掉本对象在登记表里的登记并切回已发布版；开关关着同以前（mock 登记不动） */
const onDiscard = () => de.discard(() => { const d = draft.value; if (crWriteOn() && d) cancel(d.id); else setView('published'); });
/* 刷新后恢复（写库模式）：按库里的草稿登记重新登记本页对象，库里已没有的 mock 登记去掉；本对象的草稿行由 useDraftEdits 读回 */
if (crWriteOn() && !real) {
  const key = actionContextKey();
  crRestore('event').then(({ pairs, inDb }) => {
    if (!mounted || key !== actionContextKey()) return;
    for (const t of EVENT_TYPES) { const d = draftOf('event', t.id); if (d && !inDb(d.id, t.id)) forgetTarget(d.id, 'event', t.id); }
    for (const [cr, id] of pairs) start({ join: cr, target: id });
  }).catch((e) => message.error(`读取变更单草稿失败：${crErrText(e)}`));
}
/** 发起变更 / 加入：VITE_PALANTIR_CR_WRITE 开着时先写库（失败弹错，不进草稿），关着同以前 */
async function onStart(opts: { join?: string; title?: string; desc?: string }) {
  if (creating.value) { createOptions.value = opts; createOpen.value = true; creating.value = false; return; }
  try { start(await crStart({ kind: 'event', ...crTarget() }, opts)); } catch (e) { message.error(`发起变更失败：${crErrText(e)}`); }
}
const creating = ref(false);
async function onCreated(id: string) {
  const key = actionContextKey();
  try {
    invalidateActionSnapshot();
    await loadSnapshot();
    const result = await listEventTypes({ source: source.value, kw: kw.value, page: lpage.value, size: LPAGE });
    if (key !== actionContextKey()) return;
    Object.assign(realList, { items: result.items, total: result.total, sources: result.sources });
    await router.replace({ query: { ...route.query, event: id, view: undefined } });
  } catch (e) { message.error(`刷新事件类型失败：${apiMsg(e)}`); }
}
const createOpen = ref(false);
const assistantDraft = ref<Record<string, unknown>>();
watch(createOpen, open => { if (!open) assistantDraft.value = undefined; });
useAssistantDraft(['event'], () => readonly.value || createOpen.value || dirty.value, draft => { assistantDraft.value = draft; createOpen.value = true; });
const createOptions = ref<{ join?: string; title?: string; desc?: string }>({});
watch(() => route.query.createType, value => { if (real && value) createOpen.value = true; }, { immediate: true });
const { canEdit: draftCanEdit, editTip, dirty, bannerDraft } = de;
const canEdit = computed(() => draftCanEdit.value && ready.value);
const changeOpen = ref(false);
const changeTarget = ref('');
const openChange = (name: string, isNew = false) => { if (isNew && real) { createOptions.value = {}; createOpen.value = true; return; } creating.value = isNew; changeTarget.value = name; changeOpen.value = true; };
/** 切换左侧选中项：清掉抽屉与分页；有未保存修改先确认 */
const pick = (id: string) => de.guard(() => router.replace({ query: { ...route.query, ...RESET, drawer: undefined, drawer2: undefined, event: id } }));
const onView = (v: 'published' | 'draft') => (v === 'published' ? de.guard(() => setView('published')) : setView(v));

const fieldSec = de.section<PayloadField>('payload', () => cur.value.specific, (f) => f.api);
const subSec = de.section<Subscriber>('subs', () => subscribersOf(cur.value), (x) => x.name);
const SECS = { payload: fieldSec, subs: subSec } as const;
type SecName = keyof typeof SECS;
const flat = <T extends object>(rows: DiffRow<T>[]) => rows.map((r) => ({ ...r.data, _key: r.key, _mark: r.mark }));
const editCol = (cols: any[]) => (isDraft.value ? [...cols, { title: '改动', key: '_mark', width: 64 }, { title: '操作', key: '_op', width: 96 }] : cols);
const delRow = (r: { _mark: string; _env?: boolean }) => (r._mark === '删除' ? 'et-del' : '');

interface FieldDef { k: string; label: string; kind?: 'input' | 'select' | 'switch'; options?: { value: any; label: string }[]; required?: boolean }
const FIELD_TYPES = ['字符串', '整数', '金额', '日期', '日期时间', '枚举', '文本', '布尔', '对象', '数组'];
const opt = (xs: string[]) => xs.map((x) => ({ value: x, label: x }));
const FORMS: Record<SecName, { title: string; fields: () => FieldDef[]; blank: () => any }> = {
  payload: { title: '载荷字段', blank: () => ({ name: '', api: '', type: '字符串', required: false, desc: '', sample: '' }), fields: () => [
    { k: 'name', label: '字段名', required: true }, { k: 'api', label: 'API 名', required: true },
    { k: 'type', label: '类型', kind: 'select', options: opt(FIELD_TYPES) }, { k: 'required', label: '必填', kind: 'switch' },
    { k: 'desc', label: '说明' }, { k: 'sample', label: '示例' }] },
  subs: { title: '订阅方', blank: () => ({ kind: '自动化', name: '', filter: '全部', then: '', status: '启用', weekly: 0 }), fields: () => [
    { k: 'kind', label: '类型', kind: 'select', options: opt(['规则', '自动化', '通知', '外部订阅']) },
    ...(editor.form.kind === '规则' ? [{ k: 'ruleId', label: '规则编号', required: true }] : []),
    { k: 'name', label: '订阅方', required: true }, { k: 'filter', label: '过滤条件', required: true }, { k: 'then', label: '收到后', required: true },
    { k: 'status', label: '状态', kind: 'select', options: opt(['启用', '停用']) }] },
};
const createForms = FORMS;
const editor = reactive<{ open: boolean; sec: SecName; key: string | null; form: Record<string, any> }>({ open: false, sec: 'payload', key: null, form: {} });
const editorDef = computed(() => FORMS[editor.sec]);
function openAdd(sec: SecName) { if (!canEdit.value) return; Object.assign(editor, { open: true, sec, key: null, form: FORMS[sec].blank() }); }
function openEdit(sec: SecName, key: string) {
  if (!canEdit.value) return;
  const row = SECS[sec].rows.value.find((r) => r.key === key);
  if (row) Object.assign(editor, { open: true, sec, key, form: JSON.parse(JSON.stringify(row.data)) });
}
function validSchema(node: any, depth = 0): boolean {
  if (depth > 4) return false;
  if (node.type === '对象') {
    const fields = node.schema?.fields;
    return Array.isArray(fields) && fields.length > 0 && fields.length <= 100 && new Set(fields.map((f: any) => f.api)).size === fields.length && fields.every((f: any) => f.name?.trim() && /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(f.api) && validSchema(f, depth + 1));
  }
  if (node.type === '数组') return !!node.schema?.item && validSchema(node.schema.item, depth + 1);
  return true;
}
function saveEditor() {
  const miss = editorDef.value.fields().find((f) => f.required && !String(editor.form[f.k] ?? '').trim());
  if (miss) { message.warning(`请填写${miss.label}`); return; }
  if (editor.sec === 'subs' && editor.form.kind !== '规则') editor.form.ruleId = null;
  if (editor.sec === 'payload' && editor.key === null && payloadOf(cur.value).some((f) => f.api === editor.form.api)) { message.warning('API 名与已有字段（含信封字段）重复'); return; }
  if (editor.sec === 'payload' && !validSchema(editor.form)) { message.warning('请完整填写嵌套字段，且同级 API 名不能重复'); return; }
  const sec = SECS[editor.sec] as { add: (d: any) => boolean; update: (k: string, d: any) => void };
  if (editor.key === null) { if (!sec.add({ ...editor.form })) { message.warning('已存在同名项'); return; } }
  else sec.update(editor.key, { ...editor.form });
  editor.open = false;
}
const delItem = (sec: SecName, key: string) => canEdit.value && SECS[sec].remove(key);
const restoreItem = (sec: SecName, key: string) => canEdit.value && SECS[sec].restore(key);

const simulation = ref<EventSimulation | null>(null);
const simulating = ref(false);
const simulationOpen = ref(false);
const simulationPayload = ref<Record<string, any>>({});
watch([() => cur.value.id, actionContextKey, isDraft], () => { simulation.value = null; });
async function simulate() {
  if (real && (readonly.value || dirty.value || simulating.value)) return;
  simulationPayload.value = {};
  simulationOpen.value = true;
}
async function submitSimulation() {
  const e = cur.value;
  const missing = e.specific.find(f => f.required && simulationPayload.value[f.api] == null);
  if (missing) { message.warning(`请填写必填字段：${missing.name}`); return; }
  if (!real) { simulationOpen.value = false; message.success(`测试事件已投递 ${liveSubs.value.filter((s) => s.status !== '停用').length} 个订阅方的测试通道`); return; }
  const key = actionContextKey(); const id = e.id;
  simulating.value = true;
  try {
    const result = await simulateEvent(id, 'published', undefined, simulationPayload.value);
    if (key === actionContextKey() && id === cur.value.id) { simulationOpen.value = false; simulation.value = result; }
    message.info(result.message);
  } catch (e) { message.error(`模拟校验失败：${crErrText(e)}`); }
  finally { simulating.value = false; }
}

/* 概览 */
const subs = computed(() => flat(subSec.rows.value));
const liveSubs = computed(() => subs.value.filter((x) => x._mark !== '删除'));
/* 真接口：选中类型的运行健康 + 趋势、最近 5 条事件；类型或上下文对不上时保持空态 */
const rt = reactive<{ key: string; typeId: string; stats: EventStats | null; top: EventRecord[]; err: string }>({ key: '', typeId: '', stats: null, top: [], err: '' });
/* 非“后台没起来”的报错：在最近事件 / 统计区显示单行错误 + 重试，不再只给空列表 */
let rtSeq = 0;
async function loadRt(id: string) {
  const k = ++rtSeq; const key = actionContextKey();
  Object.assign(rt, { key, typeId: id, stats: null, top: [], err: '' });
  if (!mounted || !real || !ready.value || !id) return;
  try {
    const [stats, top] = await Promise.all([getEventStats(id), listEvents(id, { range: '7d', page: 1, size: 5 })]);
    if (mounted && k === rtSeq && key === actionContextKey() && id === cur.value.id) Object.assign(rt, { typeId: id, stats, top: top.items, err: '' });
  } catch (e) {
    if (!mounted || k !== rtSeq || key !== actionContextKey() || id !== cur.value.id) return;
    const err = apiMsg(e);
    Object.assign(rt, { typeId: id, stats: null, top: [], err });
  }
}
if (real) watch([() => cur.value.id, actionContextKey, ready], () => loadRt(cur.value.id), { immediate: true });
const rtOn = computed(() => live.value && rt.key === actionContextKey() && rt.typeId === cur.value.id);
const daily = computed(() => (live.value ? (rtOn.value ? rt.stats?.daily ?? [] : []) : eventDaily(cur.value.id, cur.value.weekly)));
const weekly = computed(() => real ? rtOn.value ? rt.stats?.weekly ?? 0 : 0 : cur.value.weekly);
const asOf = computed(() => (live.value ? (rtOn.value ? rt.stats?.asOf ?? '—' : '—') : '2026-10-25 15:42'));
const chartOption = computed(() => ({
  grid: { left: 48, right: 8, top: 12, bottom: 24 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: daily.value.map((d) => d.date), axisLine: { lineStyle: { color: GRAY.g6 } }, axisTick: { show: false }, axisLabel: { color: GRAY.g8, fontSize: 12, interval: 3 } },
  yAxis: { type: 'value', minInterval: 1, axisLabel: { color: GRAY.g8, fontSize: 12 }, splitLine: { lineStyle: { color: GRAY.g4 } } },
  series: [{ name: '事件数', type: 'bar', barWidth: 10, data: daily.value.map((d) => d.value), itemStyle: { color: GRAY.g7 } }],
}));
const recentAll = computed(() => eventsOf(cur.value.id));
const recentTop = computed(() => (live.value ? (rtOn.value ? rt.top : []) : recentAll.value.slice(0, 5)));
const health = computed(() => {
  if (live.value) { const s = rtOn.value ? rt.stats : null; return { total: s?.total ?? 0, dedup: s?.dedup ?? 0, failed: s?.failed ?? 0, partial: s?.partial ?? 0, pending: s?.pending ?? 0, p95: s?.p95 ?? null }; }
  const rows = recentAll.value;
  const c = (o: EventOutcome) => rows.filter((r) => r.outcome === o).length;
  return { total: rows.length, dedup: c('已去重'), failed: c('投递失败'), partial: c('部分投递'), pending: 0,
    p95: rows.length ? [...rows].sort((a, b) => a.latencyMs - b.latencyMs)[Math.floor(rows.length * 0.95)]?.latencyMs ?? 0 : 0 };
});

/* 载荷字段 */
const fq = usePagedQuery({ defaultSize: real ? 10 : 12, prefix: 'f' });
const envelope = computed(() => payloadOf(cur.value).filter((f) => !cur.value.specific.some((x) => x.api === f.api)).map((f) => ({ ...f, _key: `env:${f.api}`, _mark: '', _env: true })));
const fieldRows = computed(() => [...flat(fieldSec.rows.value), ...envelope.value]);
const payloadPage = reactive({ items: [] as (PayloadField & { _key: string; _mark: string; _env: boolean })[], total: 0 });
if (real) {
  let seq = 0, scope = '';
  watch([actionContextKey, () => cur.value.id, ready, isDraft, tab, fq.page, fq.size], async () => {
    const n = ++seq, key = actionContextKey(), id = cur.value.id;
    const nextScope = JSON.stringify([key, id]);
    if (scope !== nextScope) { scope = nextScope; payloadPage.items = []; payloadPage.total = 0; }
    if (!ready.value || !id || isDraft.value || tab.value !== 'payload') return;
    try {
      const r = await listEventPayload(id, fq.page.value, fq.size.value);
      if (!mounted || n !== seq || key !== actionContextKey()) return;
      payloadPage.items = r.items.map(p => ({ ...p, _key: p.envelope ? `env:${p.api}` : p.api, _mark: '', _env: p.envelope })); payloadPage.total = r.total;
      if (r.page && r.page !== fq.page.value) fq.page.value = r.page;
    } catch (e) { if (mounted && n === seq && key === actionContextKey()) { payloadPage.items = []; message.error(`载荷字段加载失败：${apiMsg(e)}`); } }
  }, { immediate: true, flush: 'sync' });
}
const fields = computed(() => real && !isDraft.value ? payloadPage : paginate(fieldRows.value, { page: fq.page.value, size: fq.size.value }));
const fieldCols = computed(() => editCol([
  { title: '字段', dataIndex: 'name', width: 140 },
  { title: 'API 名', dataIndex: 'api', width: 150 },
  { title: '类型', dataIndex: 'type', width: 100 },
  { title: '必填', key: 'required', width: 72 },
  { title: '说明', dataIndex: 'desc', ellipsis: { showTitle: true } },
  { title: '示例', dataIndex: 'sample', width: 260, ellipsis: { showTitle: true } },
]));

/* 订阅方 */
const subCols = computed(() => editCol([
  { title: '类型', dataIndex: 'kind', width: 96 },
  { title: '订阅方', key: 'name', width: 280 },
  { title: '过滤条件', dataIndex: 'filter', width: 220, ellipsis: { showTitle: true } },
  { title: '收到后', dataIndex: 'then', ellipsis: { showTitle: true } },
  { title: '近 7 天投递', key: 'weekly', width: 120, align: 'right' as const },
  { title: '状态', key: 'status', width: 88 },
]));

/* 近期事件（事件流：先选时间范围，游标分页） */
const RANGES = [{ label: '近 24 小时', value: '24h' }, { label: '近 7 天', value: '7d' }];
const range = computed({
  get: () => (route.query.range === '24h' ? '24h' : '7d'),
  set: (v) => router.replace({ query: { ...route.query, range: v === '7d' ? undefined : v, epage: undefined } }),
});
const OUTCOMES = ['全部', '已投递', '已生成告警', '已去重', '部分投递', '投递失败', ...(real ? ['待投递'] : [])];
const outcome = computed({
  get: () => (OUTCOMES.includes(route.query.oc as string) ? (route.query.oc as string) : '全部'),
  set: (v) => router.replace({ query: { ...route.query, oc: v === '全部' ? undefined : v, epage: undefined } }),
});
const eq = usePagedQuery({ defaultSize: real ? 10 : 14, prefix: 'e' });
const CUTOFF_24H = '2026-10-24 15:42:18';
const recentRows = computed(() => recentAll.value.filter((r) => (range.value === '7d' || r.at >= CUTOFF_24H) && (outcome.value === '全部' || r.outcome === outcome.value)));
/* 真接口：近期事件页签按 range / 结果 / 页码走后台分页 */
const realRecent = reactive<{ key: string; items: EventRecord[]; total: number; err: string }>({ key: '', items: [], total: 0, err: '' });
const recentKey = computed(() => JSON.stringify([actionContextKey(), cur.value.id, range.value, outcome.value, eq.page.value, eq.size.value]));
let recentSeq = 0, recentScope = '';
const recentScopeKey = () => JSON.stringify([actionContextKey(), cur.value.id, range.value, outcome.value, eq.size.value]);
async function loadRecent(force = false) {
  const key = recentKey.value;
  const k = ++recentSeq;
  if (!mounted || !real || !ready.value || !cur.value.id || tab.value !== 'recent' || (!force && realRecent.key === key)) return;
  const scope = recentScopeKey();
  if (scope !== recentScope) { recentScope = scope; Object.assign(realRecent, { items: [], total: 0 }); }
  Object.assign(realRecent, { key, err: '' });
  try {
    const r = await listEvents(cur.value.id, { range: range.value as '24h' | '7d', outcome: outcome.value, page: eq.page.value, size: eq.size.value });
    if (mounted && k === recentSeq && key === recentKey.value) { Object.assign(realRecent, { key, items: r.items, total: r.total, err: '' }); if (r.page && r.page !== eq.page.value) eq.page.value = r.page; }
  } catch (e) {
    if (!mounted || k !== recentSeq || key !== recentKey.value) return;
    const err = apiMsg(e);
    Object.assign(realRecent, { key, items: [], total: 0, err });
  }
}
if (real) watch([recentKey, () => tab.value, ready], () => loadRecent(), { immediate: true, flush: 'sync' });
const rtErr = computed(() => (rtOn.value ? rt.err : ''));
const recentErr = computed(() => (live.value && realRecent.key === recentKey.value ? realRecent.err : ''));
const recent = computed(() => live.value ? realRecent.key === recentKey.value ? { items: realRecent.items, total: realRecent.total } : { items: [], total: 0 } : paginate(recentRows.value, { page: eq.page.value, size: eq.size.value }));
const recentCols = [
  { title: '时间', dataIndex: 'at', width: 172 },
  { title: '事件编号', key: 'id', width: 190 },
  { title: '对象', key: 'obj', width: 220, ellipsis: true },
  { title: '载荷摘要', dataIndex: 'summary', ellipsis: { showTitle: true } },
  { title: '订阅方', key: 'subs', width: 88, align: 'right' as const },
  { title: '延迟', key: 'lat', width: 96, align: 'right' as const },
  { title: '结果', key: 'outcome', width: 120 },
];
const outcomeBadge = (o: EventRecord['outcome']) => (o === '投递失败' ? 'error' : o === '待投递' ? 'warning' : 'default');

/* 版本历史 */
const versions = computed(() => eventVersionsOf(cur.value));
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
    <a-tooltip :title="!real ? '需要真实接口' : readonly ? reason : ''"><a-button :disabled="readonly || !real" @click="openChange('新建事件类型', true)">新建事件类型</a-button></a-tooltip>
  </Teleport>

  <a-empty v-if="live && ready && !cur.id" description="暂无事件类型或所选类型不存在" />
  <div v-else-if="!ready" class="panel et-wait">
    <span v-if="loadErr" class="c-3">事件类型加载失败：{{ loadErr }}</span><a-spin v-else />
  </div>
  <div v-else class="et">
    <div class="panel et-list">
      <div class="et-filter">
        <a-input-search v-model:value="kw" placeholder="搜索名称" class="et-kw" />
        <a-select v-model:value="source" :options="sources.map((g) => ({ value: g, label: g === '全部' ? '全部来源' : g }))" class="et-sel" />
      </div>
      <ul class="et-items">
        <a-tooltip v-for="e in list" :key="e.id" placement="right" :mouse-enter-delay="0.4" :title="`${e.api} · ${e.source} · ${e.object}`">
          <li :class="{ active: e.id === cur.id }" @click="pick(e.id)">
            <div class="et-row">
              <span class="t-14 et-name">{{ e.name }}</span>
              <span class="t-12 num" :class="e.status === '变更中' ? 'c-warning' : 'c-3'">{{ e.status === '已发布' ? '' : e.status + ' · ' }}{{ e.version }}</span>
            </div>
          </li>
        </a-tooltip>
      </ul>
      <div class="et-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="listTotal" :size="LPAGE" unit="个事件" class="et-pager" empty-text="没有匹配的事件类型" />
      </div>
    </div>

    <div class="panel et-detail">
      <DraftBanner v-if="isDraft && bannerDraft" :draft="bannerDraft"
        @submit="de.submit()" @discard="onDiscard()" />
      <div class="et-head">
        <span class="t-16 fw-600 c-1">{{ cur.name }}</span>
        <span class="t-14 c-3 num">{{ cur.api }}</span>
        <span class="t-14 c-2">{{ cur.source }} · {{ cur.object }}</span>
        <span class="t-14 c-3">{{ cur.version }}</span>
        <a-tag :color="statusColor(cur.status)">{{ cur.status }}</a-tag>
        <a-segmented v-if="draft && !readonly" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => onView(v)" />
        <span class="et-sp" />
        <a-tooltip :title="real && readonly ? reason : real && dirty ? '请先保存草稿后校验' : undefined"><span><a-button :loading="simulating" :disabled="cur.status === '草稿' || (real && (readonly || dirty))" @click="simulate">模拟触发</a-button></span></a-tooltip>
        <a-tooltip v-if="!real && readonly" :title="reason"><a-button disabled>发起变更</a-button></a-tooltip>
        <a-button v-else-if="!real && !draft" type="primary" @click="openChange(`${cur.name}（${cur.api}）`)">发起变更</a-button>
        <a-button v-else-if="!real && draft && !isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
      </div>
      <a-tabs v-model:activeKey="tab" class="et-tabs">
        <a-tab-pane key="overview" tab="概览" />
        <a-tab-pane key="payload" tab="载荷字段" />
        <a-tab-pane key="subs" tab="订阅方" />
        <a-tab-pane key="recent" tab="近期事件" />
        <a-tab-pane v-if="real" key="exceptions" tab="投递异常" />
        <a-tab-pane key="versions" tab="版本历史" />
      </a-tabs>

      <!-- 概览 -->
      <div v-if="tab === 'overview'" class="et-body et-ov">
        <div class="et-ov-content">
        <div class="et-ov-left">
          <div class="et-sec t-14 fw-600 et-sec-row"><span>基本信息</span><a-button v-if="real && isDraft" size="small" :disabled="!canEditDedup" @click="editDedup">编辑去重设置</a-button></div>
          <a-descriptions :column="2" bordered size="small" class="et-desc">
            <a-descriptions-item label="来源">{{ cur.source }}</a-descriptions-item>
            <a-descriptions-item label="来源说明">
              <template v-if="cur.ruleId"><ObjRef kind="rule" :id="cur.ruleId">{{ cur.ruleId }}</ObjRef><span class="et-gap">{{ RULES[cur.ruleId].name }}</span></template>
              <template v-else>{{ cur.sourceRef }}</template>
            </a-descriptions-item>
            <a-descriptions-item label="触发对象">{{ cur.object }}（{{ cur.objectApi }}）</a-descriptions-item>
            <a-descriptions-item label="负责人">{{ cur.owner }}</a-descriptions-item>
            <a-descriptions-item label="说明" :span="2"><span class="et-ell" :title="cur.desc">{{ cur.desc }}</span></a-descriptions-item>
            <a-descriptions-item label="载荷摘要" :span="2">{{ flat(fieldSec.rows.value).filter((f) => f._mark !== '删除').map((f) => f.name).join('、') }}<template v-if="!real || eventDetailReady(cur.id, true)">，另含 {{ envelope.length }} 个通用信封字段</template></a-descriptions-item>
            <a-descriptions-item label="去重键"><span class="num">{{ dedupValues.dedupKey }}</span></a-descriptions-item>
            <a-descriptions-item label="去重窗口">{{ dedupValues.dedupWindow }}</a-descriptions-item>
            <a-descriptions-item label="投递保证">{{ cur.delivery }}</a-descriptions-item>
            <a-descriptions-item label="顺序">{{ cur.ordering }}</a-descriptions-item>
            <a-descriptions-item label="保留期">{{ cur.retention }}</a-descriptions-item>
            <a-descriptions-item label="近 7 天">{{ fmtInt(weekly) }} 条</a-descriptions-item>
          </a-descriptions>
          <div class="et-sec t-14 fw-600 et-sec-row">
            <span>订阅方 {{ liveSubs.length }} 个<InfoTip text="完整列表见“订阅方”页签" /></span>
          </div>
          <div v-for="s in liveSubs.slice(0, 4)" :key="s._key" class="et-sub">
            <span class="t-12 c-3 et-sub-k">{{ s.kind }}</span>
            <span class="t-14 c-1 et-sub-n">
              <template v-if="s.ruleId"><ObjRef kind="rule" :id="s.ruleId">{{ s.ruleId }}</ObjRef></template><span :class="{ 'et-gap': s.ruleId }">{{ s.name }}</span>
            </span>
            <span class="t-12" :class="s.status === '异常' ? 'c-warning' : 'c-3'">{{ s.status }}</span>
          </div>
          <div class="et-sec t-14 fw-600 et-sec-row">
            <span>最近事件<InfoTip text="完整事件流见“近期事件”页签；点事件编号查看投递过程" /></span>
          </div>
          <div v-if="rtErr" class="et-err t-12"><span class="c-error et-err-m" :title="rtErr">最近事件加载失败：{{ rtErr }}</span><a-button size="small" @click="loadRt(cur.id)">重试</a-button></div>
          <div v-for="r in recentTop" :key="r.id" class="et-ev">
            <a class="t-14 num et-event-id" :title="r.id" @click="drawer.open('event', r.id)">{{ r.id }}</a>
            <span class="t-12 c-2 num">{{ r.at }}</span>
            <span class="t-12 c-2 et-ev-s" :title="r.summary">{{ r.summary }}</span>
            <span class="t-12" :class="r.outcome === '投递失败' ? 'c-error' : 'c-3'">{{ r.outcome }}</span>
          </div>
          <div v-if="!recentTop.length && !rtErr" class="t-12 c-3">{{ cur.status === '草稿' ? '草稿未发布，暂无事件' : '所选时间范围内暂无事件' }}</div>
        </div>
        <div class="et-ov-right">
          <div class="et-sec t-14 fw-600"><span>近 14 天事件数</span><span class="fw-400 c-2"> · 截至 {{ asOf }}</span></div>
          <template v-if="cur.status !== '草稿'">
            <div v-if="rtErr" class="et-err t-12"><span class="c-error et-err-m" :title="rtErr">运行统计加载失败：{{ rtErr }}</span><a-button size="small" @click="loadRt(cur.id)">重试</a-button></div>
            <div class="et-chart"><EChart :option="chartOption" /></div>
            <div class="et-stats">
              <div><span class="t-12 c-3">近 7 天</span><span class="t-16 fw-600 c-1 num">{{ fmtInt(weekly) }}</span></div>
              <div><span class="t-12 c-3">处理延迟 P95</span><span class="t-16 fw-600 c-1 num">{{ health.p95 == null ? '未知' : `${health.p95} 毫秒` }}</span></div>
              <div v-if="live"><span class="t-12 c-3">待投递（尚未投递）</span><span class="t-16 fw-600 c-1 num">{{ health.pending }}</span></div>
              <div><span class="t-12 c-3">去重丢弃（近期样本）</span><span class="t-16 fw-600 c-1 num">{{ health.dedup }}</span></div>
              <div><span class="t-12 c-3">投递失败 / 部分投递</span><span class="t-16 fw-600 num"><span :class="health.failed ? 'c-error' : 'c-1'">{{ health.failed }}</span><span class="c-3"> / </span><span class="c-1">{{ health.partial }}</span></span></div>
            </div>
          </template>
          <a-empty v-else class="et-empty" description="草稿未发布，暂无事件" />
        </div>
        </div>
      </div>

      <!-- 载荷字段 -->
      <div v-else-if="tab === 'payload'" class="et-body">
        <div class="et-tool">
          <span class="t-14 c-1">载荷字段 {{ fields.total }} 个</span><InfoTip text="事件自身字段在前，通用信封字段在后（所有事件类型共有，不可修改）" />
          <span class="et-sp" />
          <template v-if="isDraft">
            <span class="t-12" :class="dirty ? 'c-warning' : 'c-3'">{{ dirty ? '有未保存的修改' : real ? '已保存并生效' : de.locked.value ? '历史变更单已锁定' : '草稿已保存' }}</span>
            <a-button size="small" :disabled="!dirty" @click="de.revert()">撤销修改</a-button>
            <a-button size="small" :disabled="!dirty || !ready" @click="de.save()">{{ real ? '保存' : '保存草稿' }}</a-button>
          </template>
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit" @click="openAdd('payload')">新增字段</a-button></a-tooltip>
        </div>
        <div class="paged-scroll">
          <a-table :columns="fieldCols" :data-source="fields.items" :pagination="false" size="middle" row-key="_key" :row-class-name="delRow">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'required'">{{ record.required ? '是' : '否' }}</template>
              <template v-else-if="column.dataIndex === 'name'">{{ record.name }}<span v-if="record._env"> · 信封</span></template>
              <template v-else-if="column.key === '_mark'"><a-tag v-if="record._mark">{{ record._mark }}</a-tag></template>
              <template v-else-if="column.key === '_op'">
                <span v-if="record._env" class="t-12 c-3">信封字段</span>
                <a-space v-else-if="record._mark === '删除'"><a :class="{ 'et-dis': !canEdit }" @click="restoreItem('payload', record._key)">恢复</a></a-space>
                <a-space v-else><a :class="{ 'et-dis': !canEdit }" @click="openEdit('payload', record._key)">编辑</a><a :class="{ 'et-dis': !canEdit }" @click="delItem('payload', record._key)">删除</a></a-space>
              </template>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="fq.page.value" v-model:size="fq.size.value" variant="full" :total="fields.total" :default-size="fq.defaultSize" :empty="false" />
      </div>

      <!-- 订阅方 -->
      <div v-else-if="tab === 'subs'" class="et-body">
        <div class="et-tool">
          <span class="t-14 c-1">订阅方 {{ liveSubs.length }} 个</span><InfoTip text="规则、自动化、通知和外部订阅都按同一份载荷接收；点规则编号在当前页查看规则" />
          <span class="et-sp" />
          <template v-if="isDraft">
            <span class="t-12" :class="dirty ? 'c-warning' : 'c-3'">{{ dirty ? '有未保存的修改' : real ? '已保存并生效' : de.locked.value ? '历史变更单已锁定' : '草稿已保存' }}</span>
            <a-button size="small" :disabled="!dirty" @click="de.revert()">撤销修改</a-button>
            <a-button size="small" :disabled="!dirty || !ready" @click="de.save()">{{ real ? '保存' : '保存草稿' }}</a-button>
          </template>
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit" @click="openAdd('subs')">新增订阅</a-button></a-tooltip>
        </div>
        <div class="paged-scroll">
        <a-table :columns="subCols" :data-source="subs" :pagination="false" size="middle" row-key="_key" :row-class-name="delRow">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <template v-if="record.ruleId"><ObjRef kind="rule" :id="record.ruleId">{{ record.ruleId }}</ObjRef></template><span :class="{ 'et-gap': record.ruleId }">{{ record.name }}</span>
            </template>
            <template v-else-if="column.key === 'weekly'"><span class="num">{{ fmtInt(record.weekly) }}</span></template>
            <template v-else-if="column.key === 'status'">
              <a-badge :status="record.status === '异常' ? 'warning' : 'default'" :text="record.status" />
            </template>
            <template v-else-if="column.key === '_mark'"><a-tag v-if="record._mark">{{ record._mark }}</a-tag></template>
              <template v-else-if="column.key === '_op'">
                <a-space v-if="record._mark === '删除'"><a :class="{ 'et-dis': !canEdit }" @click="restoreItem('subs', record._key)">恢复</a></a-space>
                <a-space v-else><a :class="{ 'et-dis': !canEdit }" @click="openEdit('subs', record._key)">编辑</a><a :class="{ 'et-dis': !canEdit }" @click="delItem('subs', record._key)">删除</a></a-space>
              </template>
          </template>
        </a-table>
        </div>
      </div>

      <!-- 近期事件 -->
      <div v-else-if="tab === 'recent'" class="et-body">
        <div class="et-tool">
          <a-segmented v-model:value="range" :options="RANGES" size="small" />
          <a-segmented v-model:value="outcome" :options="OUTCOMES" size="small" />
          <InfoTip text="事件流按时间倒序，点事件编号查看投递过程" />
        </div>
        <div v-if="recentErr" class="et-err t-12"><span class="c-error et-err-m" :title="recentErr">近期事件加载失败：{{ recentErr }}</span><a-button size="small" @click="loadRecent(true)">重试</a-button></div>
        <div class="paged-scroll">
          <a-table :columns="recentCols" :data-source="recent.items" :pagination="false" :scroll="{ x: 1050 }" table-layout="fixed" size="middle" row-key="id"
            :locale="{ emptyText: cur.status === '草稿' ? '草稿未发布，暂无事件' : '该时间范围内没有事件' }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'id'"><a class="num et-event-id" :title="record.id" @click="drawer.open('event', record.id)">{{ record.id }}</a></template>
              <template v-else-if="column.key === 'obj'">
                <span :title="record.objLabel"><ObjRef v-if="record.objKind" :kind="record.objKind" :id="record.objId">{{ record.objLabel }}</ObjRef>
                <span v-else class="num">{{ record.objLabel }}</span></span>
              </template>
              <template v-else-if="column.key === 'subs'"><span class="num">{{ record.subscribers }}</span></template>
              <template v-else-if="column.key === 'lat'"><span class="num">{{ record.latencyMs == null ? '未知' : `${record.latencyMs} 毫秒` }}</span></template>
              <template v-else-if="column.key === 'outcome'"><a-badge :status="outcomeBadge(record.outcome)" :text="record.outcome" /></template>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="eq.page.value" variant="simple" class="et-pg-nt" :total="recent.total" :size="eq.size.value" unit="个事件" :empty="false">
          <template #extra><span class="t-12 c-2">保留期 {{ cur.retention }}</span><InfoTip text="事件流不显示总数" /></template>
        </AppPagination>
      </div>

      <div v-else-if="tab === 'exceptions' && real" class="et-body">
        <DeliveryExceptions :key="cur.id" :type-id="cur.id" />
      </div>

      <!-- 版本历史 -->
      <div v-else class="et-body">
        <div class="paged-scroll">
        <a-table :columns="verCols" :data-source="versions" :pagination="false" size="middle" row-key="ver">
          <template #headerCell="{ column }"><template v-if="column.dataIndex === 'change'">变更说明<InfoTip text="载荷只能新增可选字段；删除或改类型属于不兼容变更，需要所有订阅方确认后随整版发布" /></template></template>
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'status'"><a-badge status="default" :text="record.status" /></template>
            <template v-else-if="column.key === 'op'">
              <a-space><a-tooltip title="版本对比暂未开放"><a class="et-dis">对比</a></a-tooltip><a-tooltip v-if="record.status !== '生效中'" :title="readonly ? reason : '回滚暂未开放'"><a class="et-dis">回滚</a></a-tooltip></a-space>
            </template>
          </template>
        </a-table>
        </div>
      </div>
    </div>
  </div>
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTarget" @ok="onStart" />
  <NewActionEventForm v-if="real" :assistant-draft="assistantDraft" v-model:open="createOpen" kind="event" :options="createOptions" :row-forms="createForms" @saved="onCreated" />
  <a-modal :open="dedupOpen" title="编辑去重设置" ok-text="保存到草稿" :confirm-loading="dedupSaving"
    :closable="!dedupSaving" :mask-closable="false" :ok-button-props="{ disabled: !canEditDedup || !dedupDirty || !dedupForm.dedupWindow.trim() }"
    @ok="saveDedup" @cancel="dedupGuard(() => { dedupOpen = false; })">
    <a-form layout="vertical">
      <a-form-item label="去重键" html-for="event-dedup-key"><a-input id="event-dedup-key" v-model:value="dedupForm.dedupKey" :disabled="dedupSaving" placeholder="留空时按事件 ID 去重" /></a-form-item>
      <a-form-item label="去重窗口" html-for="event-dedup-window" required><a-input id="event-dedup-window" v-model:value="dedupForm.dedupWindow" :disabled="dedupSaving" placeholder="例如 24 小时" /></a-form-item>
    </a-form>
  </a-modal>
  <a-modal v-model:open="editor.open" :title="`${editor.key === null ? '新增' : '编辑'}${editorDef.title}`" ok-text="确定" cancel-text="取消" :width="520" @ok="saveEditor">
    <a-form layout="vertical" class="et-form">
      <a-form-item v-for="f in editorDef.fields()" :key="f.k" :label="f.label" :required="f.required">
        <a-select v-if="f.kind === 'select'" v-model:value="editor.form[f.k]" :options="f.options" />
        <a-switch v-else-if="f.kind === 'switch'" v-model:checked="editor.form[f.k]" />
        <a-input v-else v-model:value="editor.form[f.k]" />
      </a-form-item>
      <PayloadSchemaEditor v-if="editor.open && editor.sec === 'payload'" :node="editor.form" />
      <div v-if="editor.sec === 'payload' && ['对象', '数组'].includes(editor.form.type)" class="t-12 c-3">示例用于模拟触发，请填写与定义一致的 JSON 值。</div>
    </a-form>
    <div class="t-12 c-3">{{ editor.sec === 'payload' ? '新增字段建议设为可选；删除或改类型属于不兼容变更，需要所有订阅方确认。' : '' }}点“保存”立即生效并生成新版本。</div>
  </a-modal>
  <a-modal v-model:open="simulationOpen" title="模拟触发" ok-text="触发测试" :confirm-loading="simulating" @ok="submitSimulation">
    <p class="t-12 c-3">填写测试载荷；必填项需填写，嵌套必填项由服务端校验。</p>
    <div v-for="field in cur.specific" :key="field.api" style="margin-bottom: 12px">
      <div>{{ field.name }} <span v-if="field.required" class="c-error">（必填）</span></div>
      <PayloadValueEditor :node="field" v-model="simulationPayload[field.api]" />
    </div>
  </a-modal>
  <a-modal :open="!!simulation" title="模拟触发结果" :footer="null" @cancel="simulation = null">
    <template v-if="simulation">
      <a-alert type="info" :message="simulation.message" show-icon />
      <a-list :data-source="simulation.checks" size="small">
        <template #renderItem="{ item }"><a-list-item><span class="t-14">{{ item.name }}：{{ item.message }}</span></a-list-item></template>
      </a-list>
      <template v-if="simulation.testEventId">
        <div class="t-12 c-3">测试事件：{{ simulation.testEventId }}</div>
        <EventDeliveries :event-id="simulation.testEventId" />
      </template>
    </template>
  </a-modal>
</template>

<style scoped>
.et-event-id { display: block; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.et { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.et-list { width: 300px; flex: none; }
.et-wait { display: flex; align-items: center; justify-content: center; min-height: 240px; }
.et-filter { padding: 12px 12px 8px; display: flex; gap: 8px; flex: none; }
.et-kw { flex: 1; min-width: 0; }
.et-sel { width: 108px; flex: none; }
.et-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; overflow: auto; }
.et-items li { padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.et-items li:hover { background: var(--g3); }
.et-items li.active { background: var(--brand-bg); }
.et-items li.active .et-name { color: var(--brand); }
.et-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.et-sub-n { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.et-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.et-pager { flex: 1; min-height: 0; }
.et-foot { padding: 8px 12px 8px 16px; border-top: var(--border); flex: none; display: flex; align-items: center; }
.et-detail { flex: 1; min-width: 0; }
.et-head { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; flex: none; }
.et-head :deep(.ant-tag) { margin-inline-end: 0; }
.et-sp { flex: 1; }
.et-tabs { padding: 0 16px; flex: none; }
.et-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.et-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
.et-ov { overflow: auto; }
.et-ov-content { display: flex; gap: 16px; flex: none; min-height: 100%; }
.et-ov-left { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.et-ov-right { width: 400px; flex: none; display: flex; flex-direction: column; gap: 8px; padding-left: 16px; border-left: var(--border); }
.et-sec { margin-top: 4px; }
.et-sec-row { display: flex; justify-content: space-between; align-items: center; }
.et-chart { height: 200px; }
.et-err { display: flex; align-items: center; gap: 8px; padding: 6px 0; white-space: nowrap; }
.et-err-m { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.et-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.et-stats > div { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; padding: 6px 12px; border: var(--border); border-radius: var(--radius); white-space: nowrap; }
.et-desc.et-desc :deep(.ant-descriptions-view > table) { table-layout: fixed; width: 100%; }
.et-desc :deep(.ant-descriptions-item-label) { width: 96px; white-space: nowrap; }
.et-desc :deep(.ant-descriptions-item-content) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.et-pg-nt > :deep(.num:first-child) { display: none; }
.et-ell { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.et-sub { display: grid; grid-template-columns: 72px 1fr auto; gap: 8px; align-items: center; padding: 6px 0; border-bottom: var(--border); }
.et-empty { margin-top: 32px; }
.et-tool { display: flex; align-items: center; gap: 8px; flex: none; }
.et-gap { margin-left: 4px; }
.et-ev { display: grid; grid-template-columns: minmax(0, 190px) 150px minmax(0, 1fr) auto; gap: 8px; align-items: center; min-width: 0; padding: 6px 0; border-bottom: var(--border); }
.et-ev-s { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 900px) { .et-ev { grid-template-columns: minmax(0, 1fr) auto; } }
.et-form :deep(.ant-form-item) { margin-bottom: 12px; }
:deep(.et-del td) { text-decoration: line-through; color: var(--g7); }
.et-dis { color: var(--g7); cursor: not-allowed; }
</style>
