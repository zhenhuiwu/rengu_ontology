<script setup lang="ts">
import InfoTip from '@/components/InfoTip.vue';
/**
 * 对象类型 · 行为 · 生命周期：状态机（X6）+ 迁移定义 / 近期状态变化 + 右侧详情。
 * props：objectId（对象类型 id，如 'order'）、readonly（只有草稿视图里为 false）。
 * 草稿单用 useDraftView('object', () => props.objectId) 取；草稿视图里可新增状态、新增 / 修改 / 删除迁移，改动标灰色“新增 / 修改 / 删除”。
 * 查看历史版本（?ver=）时显示该次发布时生效的生命周期，全部只读并写明原因。
 * URL：?lc= 选中项（s:状态 / t:迁移编号 / x:非法迁移），?lcv= 下方列表（defs / changes），?lcpage= 状态变化页码。
 * 数据：开关 dynamic 切到真接口（dynamicApi.isReal()）时状态机 / 非法迁移 / 状态变化来自后台 object-types/{id}/lifecycle*，
 * 草稿改动每次改完整份 PUT …/lifecycle/draft（键 = 变更单:对象类型）；否则用 mock/lifecycle（不变）。
 */
import { computed, reactive, ref, shallowRef, watch, nextTick, onScopeDispose } from 'vue';
import { useRoute, useRouter, RouterLink } from 'vue-router';
import type { Cell, Graph } from '@antv/x6';
import GraphCanvas from '@/components/GraphCanvas.vue';
import ObjRef from '@/components/ObjRef.vue';
import { canResolve } from '@/components/objectRegistry';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { paginate } from '@/mock/paginate';
import { useAppStore } from '@/stores/app';
import { releaseByVer } from '@/api/releases';
import { BRAND, GRAY, STATUS } from '@/theme';
import { useDraftView } from '../useDraftView';
import { useConfirmStore } from '@/stores/confirm';
import { useUnsavedGuard, confirmDiscard } from '@/composables/useUnsavedGuard';
import {
  LC_DRAFT_EDITS, OBJ_TYPE_KIND, OBJ_TYPE_NAME, changesOf, emptyLcDraft, illegalOf, lifecycleAsOf, trName,
  type LcDraft, type LcState, type LcStateKind, type LcTransition, type LcTrigger,
} from '@/mock/lifecycle';
import { actionApi, actionTypes, eventTypes } from '@/api/action';
import { message } from 'ant-design-vue';
import { currentApiContext, isApiError, type PageResult } from '@/api/http';
import {
  dynamicApi, getLifecycle, listLifecycleChanges, listLifecycleIllegal, saveLifecycleDraft, crErrText, type LifecycleDto,
} from '@/api/dynamic';
import { OBJECT_TYPE_LIST, isStaticReal } from '@/api/static';
import type { LcChange, LcIllegal } from '@/mock/lifecycle';

const props = defineProps<{ objectId: string; readonly: boolean }>();
const route = useRoute();
const router = useRouter();
const app = useAppStore();
const confirm = useConfirmStore();
const { draft, isDraft, editTip, readonly: histRo, reason } = useDraftView('object', () => props.objectId);

const editable = computed(() => !props.readonly && isDraft.value && (!REAL_API || (!!remote.lc && !lcLoading.value && !loadError.value)));
const roTip = computed(() => (histRo.value ? reason.value : editTip.value || '只读'));
/* 对象类型目录：static 开关切到真接口时取本体对象类型（GET /object-types），否则用 mock 的对象类型表 */
const objType = computed(() => OBJECT_TYPE_LIST.find((o) => o.id === props.objectId));
const objName = computed(() => objType.value?.name ?? (isStaticReal() ? props.objectId : OBJ_TYPE_NAME[props.objectId] ?? props.objectId));

/* ───── 生效版本（历史版本取当时发布时间） ───── */
const histAt = computed(() => (histRo.value && app.viewVersion ? releaseByVer(app.viewVersion)?.publishedAt ?? null : null));
const REAL_API = dynamicApi.isReal();
const live = computed(() => REAL_API);
let mounted = true;
onScopeDispose(() => { mounted = false; });
const lcLoading = ref(false);
const loadError = ref('');
const remote = reactive({
  lc: null as LifecycleDto | null, key: '', illegal: [] as LcIllegal[],
  changes: { items: [] as LcChange[], total: 0 }, edits: {} as Record<string, LcDraft>,
});
const base = computed(() => (live.value ? remote.lc ?? undefined : lifecycleAsOf(props.objectId, histAt.value)));

/* ───── 草稿改动（本会话，按 变更单:对象 存） ───── */
const dkey = computed(() => (draft.value ? `${draft.value.id}:${props.objectId}` : ''));
const EDIT_STORE = live.value ? remote.edits : LC_DRAFT_EDITS;
const edits = computed<LcDraft>(() => (isDraft.value && dkey.value ? (EDIT_STORE[dkey.value] ??= emptyLcDraft()) : emptyLcDraft()));
/** 真接口：草稿改动改完后整份保存；失败时重新拉后台草稿，界面回到已保存的样子 */
async function persist() {
  if (!REAL_API || !isDraft.value || !draft.value) return;
  const cr = draft.value.id; const key = dkey.value;
  const context = lcKey.value;
  const requestSeq = lcSeq;
  try {
    const r = await saveLifecycleDraft(props.objectId, cr, edits.value);
    if (mounted && requestSeq === lcSeq && context === lcKey.value) remote.edits[key] = r.edits;
  } catch (e) {
    if (!mounted || requestSeq !== lcSeq || context !== lcKey.value) return;
    message.error(isApiError(e) ? crErrText(e) : String(e));
    await loadLc();
  }
}

type Mark = '新增' | '修改' | '';
interface VState extends LcState { mark: Mark }
interface VTr extends LcTransition { mark: Mark }
const states = computed<VState[]>(() => {
  if (!base.value) return [];
  return [...base.value.states.map((s) => ({ ...s, mark: '' as Mark })), ...edits.value.addStates.map((s) => ({ ...s, mark: '新增' as Mark }))];
});
const transitions = computed<VTr[]>(() => {
  if (!base.value) return [];
  const e = edits.value;
  return [
    ...base.value.transitions.filter((t) => !e.removeTransitions.includes(t.id))
      .map((t) => (e.editTransitions[t.id] ? { ...t, ...e.editTransitions[t.id], mark: '修改' as Mark } : { ...t, mark: '' as Mark })),
    ...e.addTransitions.map((t) => ({ ...t, mark: '新增' as Mark })),
  ];
});
const illegal = computed(() => (live.value ? remote.illegal : histAt.value ? [] : illegalOf(props.objectId)));
const illegalTotal = computed(() => illegal.value.reduce((n, x) => n + x.count, 0));
const showIllegal = ref(true);
const verText = computed(() => (base.value ? (isDraft.value ? `v${base.value.version} → 草稿 v${base.value.version + 1}` : `v${base.value.version}`) : ''));

/* ───── 选中项（URL ?lc=） ───── */
const sel = computed(() => (route.query.lc as string) || '');
const setSel = (v: string) => router.replace({ query: { ...route.query, lc: v || undefined } });
const selState = computed(() => (sel.value.startsWith('s:') ? states.value.find((s) => s.key === sel.value.slice(2)) : undefined));
// 迁移定义表分页：一页 TR_SIZE 条，1920×1080 下表格不内部滚动；数据变化回到第 1 页，选中迁移时跳到所在页
const TR_SIZE = 6;
const trPage = ref(1);
const trSize = ref(TR_SIZE);
const trPageRows = computed(() => transitions.value.slice((trPage.value - 1) * trSize.value, trPage.value * trSize.value));
watch(() => [props.objectId, transitions.value.length], () => { trPage.value = 1; });
const selTr = computed(() => (sel.value.startsWith('t:') ? transitions.value.find((t) => t.id === sel.value.slice(2)) : undefined));
const selIll = computed(() => (sel.value.startsWith('x:') ? illegal.value.find((x) => `${x.from}→${x.to}` === sel.value.slice(2)) : undefined));

/* ───── 校验（草稿里新增状态后常见） ───── */
const warnings = computed(() => {
  const out: string[] = [];
  for (const s of states.value) {
    const inc = transitions.value.some((t) => t.to === s.key && t.from !== s.key);
    const out_ = transitions.value.some((t) => t.from === s.key && t.to !== s.key);
    if (s.kind !== '初始' && !inc) out.push(`“${s.name}”没有进入它的迁移，永远不会到达`);
    if (s.kind !== '终态' && !out_) out.push(`“${s.name}”不是终态，但没有离开它的迁移`);
  }
  return out;
});

/* ───── 画图 ───── */
const NW = 148;
const NH = 56;
const graph = shallowRef<Graph>();
const fmt = (n: number) => n.toLocaleString('en-US');
const short = (by: string) => {
  const m = by.match(/（(.+)）/);
  const t = m ? m[1] : by;
  return t.length > 12 ? `${t.slice(0, 11)}…` : t;
};
const center = (s: LcState) => ({ x: s.x + NW / 2, y: s.y + NH / 2 });

function draw(fit: boolean) {
  const g = graph.value;
  if (!g) return;
  // 一次性 resetCells：先 clearCells 再按同一 id 逐个 addNode，X6 异步渲染会留下旧节点的 DOM（删状态后画布上还在）
  const cells: Cell[] = [];
  const sm = new Map(states.value.map((s) => [s.key, s]));
  for (const s of states.value) {
    const on = sel.value === `s:${s.key}`;
    const end = s.kind === '终态';
    cells.push(g.createNode({
      id: `s:${s.key}`, x: s.x, y: s.y, width: NW, height: NH, zIndex: 2,
      markup: [{ tagName: 'rect', selector: 'body' }, { tagName: 'rect', selector: 'inner' }, { tagName: 'text', selector: 'name' }, { tagName: 'text', selector: 'sub' }],
      attrs: {
        body: { width: NW, height: NH, rx: 4, ry: 4, fill: on ? '#e8effb' : GRAY.g1, stroke: on ? BRAND : s.kind === '初始' ? GRAY.g9 : GRAY.g7,
          strokeWidth: on || s.kind === '初始' ? 2 : 1, strokeDasharray: s.mark ? '4 3' : '', cursor: 'pointer' },
        inner: end ? { x: 3, y: 3, width: NW - 6, height: NH - 6, rx: 3, ry: 3, fill: 'none', stroke: on ? BRAND : GRAY.g7, strokeWidth: 1, pointerEvents: 'none' } : { display: 'none' },
        name: { text: s.name, refX: 0.5, refY: 22, textAnchor: 'middle', textVerticalAnchor: 'middle', fontSize: 14, fontWeight: 600, fill: on ? BRAND : GRAY.g10, pointerEvents: 'none' },
        sub: { text: s.mark ? `${s.mark} · ${s.kind}` : `${s.kind} · ${fmt(s.count)} 条`, refX: 0.5, refY: 41, textAnchor: 'middle', textVerticalAnchor: 'middle', fontSize: 12, fill: GRAY.g8, pointerEvents: 'none' },
      },
    }));
  }
  type E = { id: string; from: string; to: string; text: string; color: string; dash: string; width: number };
  const edges: E[] = transitions.value.map((t) => {
    const on = sel.value === `t:${t.id}`;
    return { id: `t:${t.id}`, from: t.from, to: t.to, text: `${t.trigger} · ${short(t.by)}${t.mark ? `（${t.mark}）` : ''}`,
      color: on ? BRAND : GRAY.g8, dash: t.mark ? '5 4' : '', width: on ? 2 : 1.5 };
  });
  if (showIllegal.value) {
    for (const x of illegal.value) {
      if (!sm.has(x.from) || !sm.has(x.to)) continue;
      const on = sel.value === `x:${x.from}→${x.to}`;
      edges.push({ id: `x:${x.from}→${x.to}`, from: x.from, to: x.to, text: `非法 · 近 7 天 ${x.count} 次`, color: on ? BRAND : STATUS.warning, dash: '4 4', width: on ? 2 : 1.5 });
    }
  }
  const pairKey = (a: string, b: string) => `${a}|${b}`;
  const all = new Set(edges.map((e) => pairKey(e.from, e.to)));
  for (const e of edges) {
    const a = sm.get(e.from)!;
    const b = sm.get(e.to)!;
    if (!a || !b) continue;
    let vertices: { x: number; y: number }[] = [];
    let source: { cell: string; anchor?: { name: string; args?: Record<string, unknown> } } = { cell: `s:${e.from}` };
    let target: { cell: string; anchor?: { name: string; args?: Record<string, unknown> } } = { cell: `s:${e.to}` };
    if (e.from === e.to) {
      source = { cell: `s:${e.from}`, anchor: { name: 'top', args: { dx: -36 } } };
      target = { cell: `s:${e.to}`, anchor: { name: 'top', args: { dx: 36 } } };
      vertices = [{ x: a.x + NW / 2 - 36, y: a.y - 44 }, { x: a.x + NW / 2 + 36, y: a.y - 44 }];
    } else {
      const ca = center(a);
      const cb = center(b);
      const between = states.value.some((s) => s !== a && s !== b && Math.abs(center(s).y - ca.y) < 20 && Math.abs(ca.y - cb.y) < 20
        && center(s).x > Math.min(ca.x, cb.x) && center(s).x < Math.max(ca.x, cb.x));
      const paired = all.has(pairKey(e.to, e.from));
      // 有一对往返迁移、或中间隔着别的状态时，往法线方向弯开，避免重叠
      if (paired || between) {
        const dx = cb.x - ca.x;
        const dy = cb.y - ca.y;
        const len = Math.hypot(dx, dy) || 1;
        const off = between ? 96 : 26;
        vertices = [{ x: (ca.x + cb.x) / 2 - (dy / len) * off, y: (ca.y + cb.y) / 2 + (dx / len) * off }];
      }
    }
    cells.push(g.createEdge({
      id: e.id, source, target, vertices, zIndex: 1,
      connector: { name: 'smooth' },
      attrs: {
        line: { stroke: e.color, strokeWidth: e.width, strokeDasharray: e.dash, targetMarker: { name: 'block', width: 8, height: 6 }, cursor: 'pointer' },
        wrap: { cursor: 'pointer' },
      },
      labels: [{ position: 0.5, attrs: { label: { text: e.text, fontSize: 12, fill: e.color === GRAY.g8 ? GRAY.g9 : e.color, cursor: 'pointer' },
        body: { fill: GRAY.g2, stroke: 'none', rx: 2, ry: 2, cursor: 'pointer' } } }],
    }));
  }
  g.resetCells(cells);
  if (fit) nextTick(() => { g.zoomToFit({ padding: 48, maxScale: 1 }); g.centerContent(); });
}
function onReady(g: Graph) {
  graph.value = g;
  g.on('node:click', ({ node }) => setSel(String(node.id)));
  g.on('edge:click', ({ edge }) => setSel(String(edge.id)));
  g.on('blank:click', () => setSel(''));
  draw(true);
}
watch(() => [props.objectId, histAt.value, isDraft.value, remote.key], () => draw(true));
watch(sel, (v) => {
  if (!v.startsWith('t:')) return;
  const i = transitions.value.findIndex((t) => t.id === v.slice(2));
  if (i >= 0) trPage.value = Math.floor(i / trSize.value) + 1;
});
watch(() => [sel.value, showIllegal.value, states.value, transitions.value], () => draw(false), { deep: true });

/* ───── 下方列表 ───── */
const view = computed({
  get: () => (route.query.lcv === 'changes' ? 'changes' : 'defs'),
  set: (v: string) => router.replace({ query: { ...route.query, lcv: v === 'changes' ? 'changes' : undefined, lcpage: undefined } }),
});
const onlyIllegal = ref(false);
const pq = usePagedQuery({ defaultSize: 10, prefix: 'lc', filters: [() => props.objectId, onlyIllegal] });
const changes = computed(() => (histAt.value ? [] : changesOf(props.objectId)).filter((c) => !onlyIllegal.value || !c.trId));
const changePage = computed(() => (live.value ? remote.changes : paginate(changes.value, { page: pq.page.value, size: pq.size.value })));

/* ───── 真接口加载 ───── */
async function guard<T>(p: Promise<T>, apply: (v: T) => void, current: () => boolean) {
  try { const value = await p; if (current()) apply(value); }
  catch (e) { if (current()) { loadError.value = isApiError(e) ? e.message : String(e); message.error(loadError.value); } }
}
const lcKey = computed(() => JSON.stringify([props.objectId, currentApiContext(), isDraft.value ? draft.value?.id ?? '' : '']));
let lcSeq = 0;
async function loadLc() {
  const my = ++lcSeq;
  const k = lcKey.value; const cr = isDraft.value ? draft.value?.id : undefined;
  const current = () => mounted && my === lcSeq && k === lcKey.value;
  lcLoading.value = true; loadError.value = '';
  remote.lc = null; remote.illegal = []; remote.changes = { items: [], total: 0 }; remote.edits = {};
  if (!mounted || !props.objectId) { lcLoading.value = false; return; }
  await guard(getLifecycle(props.objectId, cr), (r) => {
    remote.lc = r; remote.key = k;
    if (cr && r.draft) remote.edits[`${cr}:${props.objectId}`] = r.draft.edits;
  }, current);
  if (!current()) return;
  await guard(listLifecycleIllegal(props.objectId), (r) => { remote.illegal = r; }, current);
  if (current()) lcLoading.value = false;
}
let changesSeq = 0;
async function loadChanges() {
  const my = ++changesSeq;
  if (!mounted || !props.objectId || view.value !== 'changes') return;
  const k = `${lcKey.value}|${onlyIllegal.value}|${pq.page.value}|${pq.size.value}`;
  await guard(listLifecycleChanges(props.objectId, onlyIllegal.value, pq.page.value, pq.size.value), (r) => {
    remote.changes = r;
    if (r.page && r.page !== pq.page.value) pq.page.value = r.page;
  }, () => mounted && my === changesSeq && k === `${lcKey.value}|${onlyIllegal.value}|${pq.page.value}|${pq.size.value}`);
}
if (live.value) {
  watch(lcKey, loadLc, { immediate: true, flush: 'sync' });
  watch(() => [lcKey.value, view.value, onlyIllegal.value, pq.page.value, pq.size.value], loadChanges, { immediate: true, flush: 'sync' });
}
/** 状态变化里的对象可点开：对象类型在本体里（真接口）或 mock 对象类型表里、且注册表有这个 kind 的解析器，就按对象类型 id 引用 */
const refKind = computed(() => ((objType.value || OBJ_TYPE_KIND[props.objectId]) && canResolve(props.objectId) ? props.objectId : ''));
const defCols = [
  { title: '编号', key: 'id', width: 64 },
  { title: '迁移', key: 'tr', width: 170 },
  { title: '触发', key: 'trigger', width: 64 },
  { title: '来源', key: 'by', ellipsis: true },
  { title: '条件', key: 'guard', width: 210, ellipsis: true },
  { title: '结果', key: 'effect', width: 210, ellipsis: true },
  { title: '近 7 天', key: 'weekly', width: 90, align: 'right' as const },
];
const chgCols = computed(() => [
  { title: '时间', key: 'at', width: 170 },
  { title: objName.value, key: 'obj', width: 260 },
  { title: '变化', key: 'tr', width: 170 },
  { title: '依据', key: 'rule' },
  { title: '来源', key: 'by', width: 240 },
]);
/* 动作 / 事件类型目录：action 开关切到真接口时读其接口（只读，src/api/action.ts），否则读 mock */
const remoteCatalog = ref<{ actions: { id: string; name: string }[]; events: { id: string; name: string }[] }>({ actions: [], events: [] });
let catalogSeq = 0;
if (actionApi.isReal()) watch(lcKey, async (key) => {
  const my = ++catalogSeq;
  remoteCatalog.value = { actions: [], events: [] };
  async function list(path: string) {
    const rows: { id: string; name: string }[] = [];
    for (let page = 1; ; page++) {
      const r = await actionApi.get<PageResult<{ id: string; name: string }>>(path, { query: { page, size: 200 } });
      rows.push(...r.items);
      if (!r.items.length || rows.length >= r.total) return rows;
    }
  }
  await guard(Promise.all([list('/action-types'), list('/event-types')]), ([actions, events]) => { remoteCatalog.value = { actions, events }; }, () => mounted && my === catalogSeq && key === lcKey.value);
}, { immediate: true, flush: 'sync' });
const actionTypeById = (id: string) => (REAL_API ? remoteCatalog.value.actions : actionTypes()).find((a) => a.id === id);
const EVENT_NAME = computed(() => Object.fromEntries((REAL_API ? remoteCatalog.value.events : eventTypes()).map((e) => [e.id, e.name])));
const byLink = (t: LcTransition) => (t.actionId ? { path: '/build/ontology/action-types', query: { action: t.actionId } }
  : t.eventId ? { path: '/build/ontology/event-types', query: { event: t.eventId } } : null);
const byTitle = (t: LcTransition) => (t.actionId ? `动作类型 ${actionTypeById(t.actionId)?.name ?? t.actionId}` : t.eventId ? `事件类型 ${EVENT_NAME.value[t.eventId] ?? t.eventId}` : '');

/* ───── 草稿编辑 ───── */
const stOpen = ref(false);
const stForm = reactive({ name: '', kind: '中间' as LcStateKind, desc: '', sla: '' });
const trOpen = ref(false);
const trEditId = ref('');
const trForm = reactive({ from: '', to: '', trigger: '事件' as LcTrigger, by: '', guard: '', effect: '' });
const TRIGGERS: LcTrigger[] = ['事件', '动作', '系统', '人工'];
const stErr = computed(() => (!stForm.name.trim() ? '请填写状态名' : states.value.some((s) => s.key === stForm.name.trim()) ? '状态名已存在' : ''));
const trErr = computed(() => {
  if (!trForm.from || !trForm.to) return '请选择原状态和新状态';
  if (!trForm.by.trim()) return '请填写触发来源';
  if (!trEditId.value && transitions.value.some((t) => t.from === trForm.from && t.to === trForm.to)) return '这条迁移已存在';
  return '';
});
/* 弹窗未保存保护：表单和打开时不同即视为有改动；关闭（取消 / 遮罩 / Esc）和离开页面时提示 */
const stInit = ref('');
const trInit = ref('');
const stDirty = computed(() => stOpen.value && JSON.stringify(stForm) !== stInit.value);
const trDirty = computed(() => trOpen.value && JSON.stringify(trForm) !== trInit.value);
useUnsavedGuard(() => stDirty.value || trDirty.value);
async function closeSt() { if (stDirty.value && !(await confirmDiscard())) return; stOpen.value = false; }
async function closeTr() { if (trDirty.value && !(await confirmDiscard())) return; trOpen.value = false; }
function openState() { Object.assign(stForm, { name: '', kind: '中间', desc: '', sla: '' }); stInit.value = JSON.stringify(stForm); stOpen.value = true; }
function saveState() {
  if (stErr.value) return;
  const maxY = Math.max(...states.value.map((s) => s.y));
  const n = edits.value.addStates.length;
  edits.value.addStates.push({ key: stForm.name.trim(), name: stForm.name.trim(), kind: stForm.kind, desc: stForm.desc || '—', count: 0,
    x: 40 + n * 220, y: maxY + 150, sla: stForm.sla || undefined });
  stOpen.value = false;
  setSel(`s:${stForm.name.trim()}`);
  persist();
}
function openTr(t?: VTr) {
  trEditId.value = t?.id ?? '';
  Object.assign(trForm, t ? { from: t.from, to: t.to, trigger: t.trigger, by: t.by, guard: t.guard, effect: t.effect }
    : { from: selState.value?.key ?? '', to: '', trigger: '事件', by: '', guard: '', effect: '' });
  trInit.value = JSON.stringify(trForm);
  trOpen.value = true;
}
function saveTr() {
  if (trErr.value) return;
  const e = edits.value;
  if (trEditId.value) {
    const added = e.addTransitions.find((t) => t.id === trEditId.value);
    if (added) Object.assign(added, { trigger: trForm.trigger, by: trForm.by, guard: trForm.guard || '—', effect: trForm.effect || '—' });
    else e.editTransitions[trEditId.value] = { trigger: trForm.trigger, by: trForm.by, guard: trForm.guard || '—', effect: trForm.effect || '—' };
  } else {
    const nums = [...(base.value?.transitions ?? []), ...e.addTransitions].map((t) => Number(t.id.slice(1)) || 0);
    const id = `T${Math.max(0, ...nums) + 1}`;
    e.addTransitions.push({ id, from: trForm.from, to: trForm.to, trigger: trForm.trigger, by: trForm.by, guard: trForm.guard || '—', effect: trForm.effect || '—', weekly: 0 });
    setSel(`t:${id}`);
  }
  trOpen.value = false;
  persist();
}
async function removeTr(t: VTr) {
  const added = t.mark === '新增';
  const ok = await confirm.ask({
    title: '删除迁移', target: `迁移 ${t.id} ${trName(t)}`, okText: '删除迁移', danger: true,
    fields: [{ label: '触发', value: `${t.trigger} · ${t.by}` }, ...(added ? [] : [{ label: '近 7 天', value: `${fmt(t.weekly)} 次` }])],
    impact: added ? '从草稿中移除这条新增的迁移。'
      : `草稿里删除这条迁移${t.mark === '修改' ? '（连同草稿里对它的修改）' : ''}；发布后“${trName(t)}”不再合法，这类状态变化会被${base.value?.ruleId ? `规则 ${base.value.ruleId} ` : ''}判为非法迁移。发布前可在概要的草稿改动里撤销。`,
  });
  if (!ok) return;
  const e = edits.value;
  if (t.mark === '新增') e.addTransitions = e.addTransitions.filter((x) => x.id !== t.id);
  else { delete e.editTransitions[t.id]; e.removeTransitions.push(t.id); }
  setSel('');
  persist();
}
/** 删除草稿新增的状态；从“撤销”进来且没有连带迁移时不必确认 */
async function removeState(s: VState, fromUndo = false) {
  const e = edits.value;
  const gone = e.addTransitions.filter((t) => t.from === s.key || t.to === s.key);
  const ok = (fromUndo && !gone.length) || await confirm.ask({
    title: '删除状态', target: `状态“${s.name}”（${s.kind} · 草稿新增）`, okText: '删除状态', danger: true,
    fields: gone.length ? [{ label: `同时删除迁移 ${gone.length} 条`, value: gone.map((t) => `${t.id} ${trName(t)}`).join('；') }] : [],
    impact: gone.length ? `删除状态后，进入或离开“${s.name}”的 ${gone.length} 条新增迁移一并从草稿移除。` : `从草稿中移除状态“${s.name}”，没有相关迁移。`,
  });
  if (!ok) return;
  e.addStates = e.addStates.filter((x) => x.key !== s.key);
  e.addTransitions = e.addTransitions.filter((t) => t.from !== s.key && t.to !== s.key);
  setSel('');
  persist();
}
const changeList = computed(() => {
  const e = edits.value;
  const bt = (id: string) => base.value?.transitions.find((t) => t.id === id);
  return [
    ...e.addStates.map((s) => ({ key: `as:${s.key}`, mark: '新增', text: `状态“${s.name}”（${s.kind}）`, undo: () => removeState({ ...s, mark: '新增' }, true) })),
    ...e.addTransitions.map((t) => ({ key: `at:${t.id}`, mark: '新增', text: `迁移 ${t.id} ${trName(t)}`, undo: () => { e.addTransitions = e.addTransitions.filter((x) => x.id !== t.id); persist(); } })),
    ...Object.keys(e.editTransitions).map((id) => ({ key: `et:${id}`, mark: '修改', text: `迁移 ${id} ${bt(id) ? trName(bt(id)!) : ''}`, undo: () => { delete e.editTransitions[id]; persist(); } })),
    ...e.removeTransitions.map((id) => ({ key: `rt:${id}`, mark: '删除', text: `迁移 ${id} ${bt(id) ? trName(bt(id)!) : ''}`, undo: () => { e.removeTransitions = e.removeTransitions.filter((x) => x !== id); persist(); } })),
  ];
});
const trsOf = (key: string, dir: 'in' | 'out') => transitions.value.filter((t) => (dir === 'in' ? t.to === key : t.from === key));
</script>

<template>
  <div class="ot-body lc">
    <a-alert v-if="loadError" type="error" show-icon :message="loadError"><template #action><a-button size="small" @click="loadLc">重试</a-button></template></a-alert>
    <a-spin v-if="lcLoading" tip="正在读取生命周期…" />
    <template v-if="base">
      <div class="lc-bar">
        <span class="t-14 c-1 fw-600">{{ objName }}生命周期</span>
        <span class="t-14 c-3 num">{{ base.field }} · {{ verText }} · {{ states.length }} 个状态 · {{ transitions.length }} 条迁移 · 负责人 {{ base.owner }} · 更新于 {{ base.updatedAt }}</span>
        <span v-if="histAt" class="t-12 c-3">{{ reason }}；显示该版本发布时生效的 v{{ base.version }}</span>
        <span class="lc-sp" />
        <a-checkbox v-if="illegal.length" v-model:checked="showIllegal"><span class="t-14">显示近 7 天非法迁移</span></a-checkbox>
        <a-tooltip :title="editable ? '' : roTip">
          <a-button :disabled="!editable" @click="openState">新增状态</a-button>
        </a-tooltip>
        <a-tooltip :title="editable ? '' : roTip">
          <a-button :type="editable ? 'primary' : 'default'" :disabled="!editable" @click="openTr()">新增迁移</a-button>
        </a-tooltip>
      </div>

      <div class="lc-main">
        <div class="lc-left">
          <div class="lc-graph"><GraphCanvas @ready="onReady" /></div>
          <div class="lc-list">
            <div class="lc-list-bar">
              <a-segmented v-model:value="view" size="small" :options="[{ label: '迁移定义', value: 'defs' }, { label: '近期状态变化', value: 'changes' }]" />
              <span class="lc-sp" />
              <a-checkbox v-if="view === 'changes' && base.ruleId" v-model:checked="onlyIllegal"><span class="t-14">只看非法迁移</span></a-checkbox>
            </div>
            <a-table v-if="view === 'defs'" class="lc-table" size="small" :columns="defCols" :data-source="trPageRows" row-key="id" :pagination="false"
              :row-class-name="(r: VTr) => (sel === `t:${r.id}` ? 'lc-row-on' : '')" :custom-row="(r: VTr) => ({ onClick: () => setSel(`t:${r.id}`) })">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'id'"><span class="num">{{ record.id }}</span></template>
                <template v-else-if="column.key === 'tr'">
                  <span class="lc-nowrap">{{ trName(record) }}</span>
                  <span v-if="record.mark" class="t-12 c-3 lc-mark">{{ record.mark }}</span>
                </template>
                <template v-else-if="column.key === 'trigger'">{{ record.trigger }}</template>
                <template v-else-if="column.key === 'by'">
                  <RouterLink v-if="byLink(record)" :to="byLink(record)!" :title="byTitle(record)">{{ record.by }}</RouterLink>
                  <span v-else>{{ record.by }}</span>
                </template>
                <template v-else-if="column.key === 'guard'"><span class="c-2" :title="record.guard">{{ record.guard }}</span></template>
                <template v-else-if="column.key === 'effect'"><span class="c-2" :title="record.effect">{{ record.effect }}</span></template>
                <template v-else-if="column.key === 'weekly'"><span class="num">{{ fmt(record.weekly) }}</span></template>
              </template>
            </a-table>
            <AppPagination v-if="view === 'defs'" v-model:page="trPage" v-model:size="trSize" variant="simple" :total="transitions.length" unit="条迁移" :empty="false" />
            <template v-else>
              <a-table class="lc-table" size="small" :columns="chgCols" :data-source="changePage.items" row-key="id" :pagination="false"
                :locale="{ emptyText: histAt ? '历史版本不显示状态变化记录' : '暂无记录' }">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'at'"><span class="num">{{ record.at }}</span></template>
                  <template v-else-if="column.key === 'obj'">
                    <ObjRef v-if="refKind" :kind="refKind" :id="record.objId"><span class="num">{{ record.objLabel }}</span></ObjRef>
                    <span v-else class="num">{{ record.objLabel }}</span>
                  </template>
                  <template v-else-if="column.key === 'tr'">
                    <span :class="record.trId ? '' : 'tl-attention'" class="lc-nowrap">{{ record.from }} → {{ record.to }}</span>
                  </template>
                  <template v-else-if="column.key === 'rule'">
                    <a v-if="record.trId" @click="setSel(`t:${record.trId}`)">迁移 {{ record.trId }}</a>
                    <span v-else class="tl-attention">非法迁移 · {{ base.ruleId }}
                      <template v-if="record.alertId"> · <RouterLink :to="{ path: '/use/ops/alerts', query: { alert: record.alertId } }">{{ record.alertId }}</RouterLink></template>
                      <span v-else class="c-3"> · 冷却期内已去重</span>
                    </span>
                  </template>
                  <template v-else-if="column.key === 'by'"><span class="c-2">{{ record.by }}</span></template>
                </template>
              </a-table>
              <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="changePage.total" :default-size="pq.defaultSize" :empty="false" />
            </template>
          </div>
        </div>

        <!-- 右侧详情 -->
        <div class="lc-side">
          <template v-if="selState">
            <div class="lc-side-head">
              <span class="t-16 fw-600 c-1">{{ selState.name }}</span>
              <span class="t-12 c-3">状态 · {{ selState.kind }}<template v-if="selState.mark"> · {{ selState.mark }}</template></span>
              <span class="lc-sp" /><a class="t-14" @click="setSel('')">返回概要</a>
            </div>
            <a-descriptions :column="1" size="small" bordered class="lc-desc">
              <a-descriptions-item label="说明">{{ selState.desc }}</a-descriptions-item>
              <a-descriptions-item label="当前对象数"><span class="num">{{ fmt(selState.count) }}</span> 条</a-descriptions-item>
              <a-descriptions-item label="时限">{{ selState.sla ?? '—' }}</a-descriptions-item>
            </a-descriptions>
            <div class="t-14 fw-600 c-1">进入（{{ trsOf(selState.key, 'in').length }}）</div>
            <ul class="lc-ul">
              <li v-for="t in trsOf(selState.key, 'in')" :key="t.id"><a @click="setSel(`t:${t.id}`)">{{ t.id }} {{ trName(t) }}</a><span class="t-12 c-3">{{ t.trigger }} · {{ short(t.by) }}</span></li>
              <li v-if="!trsOf(selState.key, 'in').length" class="t-14 c-3">{{ selState.kind === '初始' ? '初始状态，创建时进入' : '无' }}</li>
            </ul>
            <div class="t-14 fw-600 c-1">离开（{{ trsOf(selState.key, 'out').length }}）</div>
            <ul class="lc-ul">
              <li v-for="t in trsOf(selState.key, 'out')" :key="t.id"><a @click="setSel(`t:${t.id}`)">{{ t.id }} {{ trName(t) }}</a><span class="t-12 c-3">{{ t.trigger }} · {{ short(t.by) }}</span></li>
              <li v-if="!trsOf(selState.key, 'out').length" class="t-14 c-3">{{ selState.kind === '终态' ? '终态，不再变化' : '无' }}</li>
            </ul>
            <div v-for="w in warnings.filter((x) => x.includes(`“${selState!.name}”`))" :key="w" class="t-12 tl-attention">校验：{{ w }}</div>
            <div v-if="editable" class="lc-acts">
              <a-button @click="openTr()">从这里新增迁移</a-button>
              <a-button v-if="selState.mark === '新增'" danger @click="removeState(selState)">删除状态</a-button>
            </div>
          </template>

          <template v-else-if="selTr">
            <div class="lc-side-head">
              <span class="t-16 fw-600 c-1">{{ trName(selTr) }}</span>
              <span class="t-12 c-3 num">迁移 {{ selTr.id }}<template v-if="selTr.mark"> · {{ selTr.mark }}</template></span>
              <InfoTip v-if="selTr.actionId" text="经动作执行的迁移：动作在动作中心确认、外部系统返回成功后才改状态；结果未知时状态不变，待对账。" />
              <span class="lc-sp" /><a class="t-14" @click="setSel('')">返回概要</a>
            </div>
            <a-descriptions :column="1" size="small" bordered class="lc-desc">
              <a-descriptions-item label="触发方式">{{ selTr.trigger }}</a-descriptions-item>
              <a-descriptions-item label="来源">
                <RouterLink v-if="byLink(selTr)" :to="byLink(selTr)!">{{ selTr.by }}</RouterLink><span v-else>{{ selTr.by }}</span>
              </a-descriptions-item>
              <a-descriptions-item label="条件">{{ selTr.guard }}</a-descriptions-item>
              <a-descriptions-item label="结果">{{ selTr.effect }}</a-descriptions-item>
              <a-descriptions-item label="近 7 天"><span class="num">{{ fmt(selTr.weekly) }}</span> 次</a-descriptions-item>
            </a-descriptions>
            <div v-if="editable" class="lc-acts">
              <a-button @click="openTr(selTr)">修改</a-button>
              <a-button danger @click="removeTr(selTr)">删除迁移</a-button>
            </div>
          </template>

          <template v-else-if="selIll">
            <div class="lc-side-head">
              <span class="t-16 fw-600 tl-attention">{{ selIll.from }} → {{ selIll.to }}</span>
              <span class="t-12 c-3">非法迁移</span>
              <InfoTip text="不在迁移定义里的状态变化。规则产生告警，并在动作中心暂存“订单状态修正”待确认；若确属合法业务，请在草稿里补这条迁移。" />
              <span class="lc-sp" /><a class="t-14" @click="setSel('')">返回概要</a>
            </div>
            <a-descriptions :column="1" size="small" bordered class="lc-desc">
              <a-descriptions-item label="近 7 天"><span class="num">{{ selIll.count }}</span> 次</a-descriptions-item>
              <a-descriptions-item label="最近一次"><span class="num">{{ selIll.lastAt }}</span></a-descriptions-item>
              <a-descriptions-item label="检测规则"><RouterLink :to="{ path: '/build/ontology/rules', query: { rule: base.ruleId } }">{{ base.ruleId }}</RouterLink></a-descriptions-item>
              <a-descriptions-item label="告警">
                <RouterLink v-if="selIll.alertId" :to="{ path: '/use/ops/alerts', query: { alert: selIll.alertId } }">{{ selIll.alertId }}</RouterLink>
                <span v-else class="c-3">冷却期内已去重，未单独产生告警</span>
              </a-descriptions-item>
            </a-descriptions>
          </template>

          <template v-else>
            <div class="lc-side-head"><span class="t-16 fw-600 c-1">概要</span></div>
            <div class="t-14 c-2">{{ base.desc }}</div>

            <template v-if="isDraft">
              <div class="t-14 fw-600 c-1">草稿改动（{{ draft?.id }}）</div>
              <ul class="lc-ul">
                <li v-for="c in changeList" :key="c.key"><span><span class="t-12 c-3 lc-mark">{{ c.mark }}</span>{{ c.text }}</span><a v-if="editable" class="t-12" @click="c.undo()">撤销</a></li>
                <li v-if="!changeList.length" class="t-14 c-3">还没有改动。可新增状态、新增迁移，或选中迁移后修改、删除。</li>
              </ul>
            </template>

            <template v-if="warnings.length">
              <div class="t-14 fw-600 c-1">校验</div>
              <ul class="lc-ul"><li v-for="w in warnings" :key="w" class="tl-attention t-14">{{ w }}</li></ul>
            </template>

            <template v-if="base.ruleId && !histAt">
              <div class="t-14 fw-600 c-1">近 7 天非法迁移 <span class="num tl-attention">{{ illegalTotal }}</span> 次
                <InfoTip :text="`由规则 ${base.ruleId} 按本表检测；统计含冷却期内被去重的命中。`" />
                <RouterLink class="t-12 fw-400 lc-rl" :to="{ path: '/build/ontology/rules', query: { rule: base.ruleId } }">{{ base.ruleId }}</RouterLink></div>
              <ul class="lc-ul">
                <li v-for="x in illegal" :key="`${x.from}→${x.to}`">
                  <a @click="setSel(`x:${x.from}→${x.to}`)">{{ x.from }} → {{ x.to }}</a><span class="num tl-attention">{{ x.count }} 次</span>
                </li>
              </ul>
            </template>

            <div class="t-14 fw-600 c-1">版本记录</div>
            <ul class="lc-ul">
              <a-tooltip v-for="v in base.versions" :key="v.ver" :title="`${v.change}${v.release ? `（发布 ${v.release}）` : ''}`" placement="left">
                <li class="lc-ver">
                  <span class="lc-ver-l"><span class="num c-1">v{{ v.ver }}</span> <span class="t-12 c-2">{{ v.status }}</span></span>
                  <span class="t-12 c-1 num lc-ver-r">{{ v.by }} · {{ v.at }}</span>
                </li>
              </a-tooltip>
            </ul>
          </template>
        </div>
      </div>
    </template>

    <div v-else-if="!lcLoading && !loadError" class="lc-empty">
      <div class="t-14 c-2">{{ objName }}没有状态属性，未定义生命周期。</div>
      <div class="t-12 c-3">需要跟踪状态变化时，先在属性里加状态字段，再在草稿里定义状态和迁移。</div>
    </div>

    <a-modal :open="stOpen" title="新增状态" @cancel="closeSt" :ok-button-props="{ disabled: !!stErr }" ok-text="加入草稿" @ok="saveState">
      <a-form layout="vertical">
        <a-form-item label="状态名" required :help="stForm.name ? stErr : ''" :validate-status="stForm.name && stErr ? 'error' : ''"><a-input v-model:value="stForm.name" placeholder="如：装修中" /></a-form-item>
        <a-form-item label="类型"><a-radio-group v-model:value="stForm.kind"><a-radio value="中间">中间状态</a-radio><a-radio value="终态">终态</a-radio></a-radio-group></a-form-item>
        <a-form-item label="说明"><a-input v-model:value="stForm.desc" /></a-form-item>
        <a-form-item label="时限（可选）"><a-input v-model:value="stForm.sla" placeholder="如：最长 60 天" /></a-form-item>
      </a-form>
    </a-modal>
    <a-modal :open="trOpen" @cancel="closeTr" :title="trEditId ? `修改迁移 ${trEditId}` : '新增迁移'" :ok-button-props="{ disabled: !!trErr }" ok-text="加入草稿" @ok="saveTr">
      <a-form layout="vertical">
        <div class="lc-form-row">
          <a-form-item label="原状态" required class="lc-f"><a-select v-model:value="trForm.from" :disabled="!!trEditId" :options="states.filter((s) => s.kind !== '终态').map((s) => ({ value: s.key, label: s.name }))" /></a-form-item>
          <a-form-item label="新状态" required class="lc-f"><a-select v-model:value="trForm.to" :disabled="!!trEditId" :options="states.map((s) => ({ value: s.key, label: s.name }))" /></a-form-item>
        </div>
        <a-form-item label="触发方式"><a-radio-group v-model:value="trForm.trigger"><a-radio v-for="t in TRIGGERS" :key="t" :value="t">{{ t }}</a-radio></a-radio-group></a-form-item>
        <a-form-item label="触发来源" required><a-input v-model:value="trForm.by" placeholder="事件类型、动作类型、系统任务或人工操作" /></a-form-item>
        <a-form-item label="条件"><a-input v-model:value="trForm.guard" /></a-form-item>
        <a-form-item label="结果"><a-input v-model:value="trForm.effect" /></a-form-item>
        <div v-if="trErr && (trForm.from || trForm.to)" class="t-12 tl-attention">{{ trErr }}</div>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.lc { gap: 12px; }
.lc-bar { display: flex; align-items: center; gap: 12px; flex: none; min-width: 0; }
.lc-sp { flex: 1; }
.lc-main { flex: 1; min-height: 0; display: flex; gap: 12px; }
.lc-left { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.lc-graph { flex: 1; min-height: 200px; border: var(--border); border-radius: var(--radius); overflow: hidden; }
.lc-list { flex: none; display: flex; flex-direction: column; gap: 8px; }
.lc-list-bar { display: flex; align-items: center; gap: 12px; }
.lc-list > .lc-table { flex: none; }
.lc-table :deep(.ant-table-row) { cursor: pointer; }
.lc-table :deep(.lc-row-on > td) { background: var(--brand-bg) !important; }
.lc-nowrap { white-space: nowrap; }
.lc-mark { margin: 0 6px; }
.lc-side { width: 400px; flex: none; border: var(--border); border-radius: var(--radius); padding: 12px 16px; display: flex; flex-direction: column; gap: 10px; overflow: hidden; }
.lc-side-head { display: flex; align-items: baseline; gap: 8px; }
.lc-desc :deep(.ant-descriptions-item-label) { width: 96px; }
.lc-ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.lc-ul li { display: flex; justify-content: space-between; gap: 8px; }
.lc-ul li.lc-ver { align-items: baseline; }
.lc-ver-l { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lc-ver-r { flex: none; white-space: nowrap; }
.lc-rl { margin-left: 8px; }
.lc-acts { display: flex; gap: 8px; }
.lc-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; }
.lc-form-row { display: flex; gap: 12px; }
.lc-f { flex: 1; }
</style>
