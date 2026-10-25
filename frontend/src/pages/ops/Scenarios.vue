<script setup lang="ts">
import InfoTip from '@/components/InfoTip.vue';
/**
 * 情景推演：左侧方案列表（分页 ?page=，选中 ?sc=），右侧方案配置（范围 + 驱动）与推演结果。
 * 对比模式 ?mode=cmp：勾选最多 3 个已完成方案（?cmp=a,b,c），并排看 KPI 与周销售；?pick= 为选定方案。
 * 选定方案可“转为暂存动作”：生成动作中心“待确认”记录（stageAction），只暂存、不执行；确认人在动作中心确认后才进入审批。
 * 历史版本 ?ver= 只读：不能运行、修改或转动作。
 * 真接口模式（dynamicApi.isReal()，开关 VITE_API_REAL_MODULES 含 dynamic）：列表 / meta / 对比 / 新建·复制 / 改名 / 运行后取结果 /
 * 影响明细服务端分页 / 转动作预览（stage-plan）与暂存（:stage）都走 /scenarios 接口；默认 mock 路径不变。
 */
import { computed, nextTick, reactive, ref, watch } from 'vue';
import { backend, fallBackIfDown } from '@/api/fallback';
import { useRoute, useRouter, onBeforeRouteUpdate } from 'vue-router';
import { message } from 'ant-design-vue';
import { PlusOutlined, CopyOutlined, ExclamationCircleOutlined, LoadingOutlined } from '@ant-design/icons-vue';
import { useUnsavedGuard, confirmDiscard } from '@/composables/useUnsavedGuard';
import { CURRENT_USER } from '@/mock/changes';
import EChart from '@/components/EChart.vue';
import ObjRef from '@/components/ObjRef.vue';
import AppPagination from '@/components/AppPagination.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { paginate } from '@/mock/paginate';
import { BRAND, GRAY } from '@/theme';
import { stageAction } from '@/mock/actions';
import {
  SCENARIOS, SC_AREAS, SC_CATEGORIES, PROMO_TYPES, BASE_DRIVERS, simulate, stagePlanOf, scenarioById, nextScenarioId, fmtPct,
  type Scenario, type ScStatus, type ScScope, type ScDrivers, type StagePlan, type ScKpi,
  type ScImpact,
} from '@/mock/scenarios';
import { isApiError } from '@/api/http';
import {
  dynamicApi, listScenarios, getScenariosMeta, compareScenarios, createScenario, getScenario, renameScenario, runScenario,
  getScenarioResult, listScenarioImpacts, getScenarioStagePlan, stageScenario, type ScenarioDto, type ScenarioMetaDto,
} from '@/api/dynamic';

const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const { readonly, reason: roReason } = useReadonly();
const ME = CURRENT_USER;
let tick = 0;
const now = () => { tick += 1; return `2026-10-25 16:${String(50 + (tick % 10)).padStart(2, '0')}:${String(tick * 7 % 60).padStart(2, '0')}`; };
const q1 = (k: string) => { const v = route.query[k]; return typeof v === 'string' ? v : ''; };
/* ───── 真接口模式状态（mock 模式下不用） ───── */
const REAL_API = dynamicApi.isReal();
/** 后台没起来（api/fallback.ts）：读取回退 mock 并只提示一次；写操作照旧调接口、照旧报错 */
const live = computed(() => REAL_API && !backend.down);
const readFail = (e: unknown) => { if (!fallBackIfDown(e)) apiFail(e); };
const remote = reactive<{
  meta: ScenarioMetaDto | null; list: { items: Scenario[]; total: number }; rev: number;
  detail: Scenario | null; cmp: Scenario[]; imp: { items: ScImpact[]; total: number }; plans: Record<string, StagePlan[]>;
}>({ meta: null, list: { items: [], total: 0 }, rev: 0, detail: null, cmp: [], imp: { items: [], total: 0 }, plans: {} });
/** 接口的 result 不含 impacts（影响明细走分页接口），这里补空数组以复用页面的 Scenario 类型 */
const asSc = (d: ScenarioDto): Scenario => ({ ...d, result: d.result ? { ...d.result, impacts: [] } : undefined }) as Scenario;
const apiFail = (e: unknown) => { message.error(isApiError(e) ? e.message : String((e as Error)?.message ?? e)); };
const setQ = (patch: Record<string, string | null>) => {
  const q = { ...route.query } as Record<string, string>;
  Object.entries(patch).forEach(([k, v]) => { if (v == null || v === '') delete q[k]; else q[k] = v; });
  router.replace({ query: q });
};

/* ───── 列表 ───── */
const statusF = ref<'全部' | ScStatus>('全部');
const kw = ref('');
const pq = usePagedQuery({ defaultSize: 12, filters: [statusF, kw] });
const rows = computed(() => SCENARIOS.filter((s) => (statusF.value === '全部' || s.status === statusF.value)
  && (!kw.value || s.name.includes(kw.value) || s.id.includes(kw.value.toUpperCase()))));
const paged = computed(() => (live.value ? remote.list : paginate(rows.value, { page: pq.page.value, size: pq.size.value })));
const badge = (s: ScStatus) => (s === '失败' ? 'error' : 'default');

const mode = computed<'one' | 'cmp'>({ get: () => (q1('mode') === 'cmp' ? 'cmp' : 'one'), set: (v) => setQ({ mode: v === 'cmp' ? 'cmp' : null }) });
/** 未指定 ?sc= 时默认第一次载入列表的首个方案（与 mock 的 SCENARIOS[0] 一致，筛选不改变选中） */
const defaultId = ref('');
const scId = computed(() => (live.value ? q1('sc') || defaultId.value : ''));
const current = computed<Scenario | undefined>(() => (live.value ? (remote.detail && remote.detail.id === scId.value ? remote.detail : undefined)
  : scenarioById(q1('sc')) ?? SCENARIOS[0]));
const cmpIds = computed<string[]>(() => (live.value ? q1('cmp').split(',').filter(Boolean).slice(0, 3)
  : q1('cmp').split(',').filter((id) => scenarioById(id)?.status === '已完成').slice(0, 3)));
const cmpList = computed<Scenario[]>(() => (live.value ? remote.cmp : cmpIds.value.map((id) => scenarioById(id)!)));
/** 真接口：列表 / 详情 / 对比 / meta 加载；只采用与当前条件一致的最后一次响应 */
let listSeq = 0;
async function loadList() {
  const seq = ++listSeq;
  try {
    const r = await listScenarios({ status: statusF.value, kw: kw.value, page: pq.page.value, size: pq.size.value });
    if (seq === listSeq) { remote.list = { items: r.items.map(asSc), total: r.total }; if (!defaultId.value) defaultId.value = r.items[0]?.id ?? ''; }
  } catch (e) { readFail(e); }
}
async function loadDetail() {
  const id = scId.value; if (!id) { remote.detail = null; return; }
  try { const d = await getScenario(id); if (id === scId.value) remote.detail = asSc(d); } catch (e) { readFail(e); }
}
async function loadCmp() {
  const ids = cmpIds.value; const key = ids.join(',');
  if (mode.value !== 'cmp' || !ids.length) { remote.cmp = []; return; }
  try { const r = await compareScenarios(ids); if (key === cmpIds.value.join(',')) remote.cmp = r.items.map(asSc); } catch (e) { remote.cmp = []; readFail(e); }
}
if (live.value) {
  getScenariosMeta().then((m) => { remote.meta = m; }).catch(readFail);
  watch([statusF, kw, pq.page, pq.size, () => remote.rev], loadList, { immediate: true });
  watch(scId, loadDetail, { immediate: true });
  watch([mode, () => cmpIds.value.join(',')], loadCmp, { immediate: true });
}
const areaOpts = computed(() => (remote.meta?.areas ?? [...SC_AREAS]).map((v) => ({ value: v, label: v })));
const catOpts = computed(() => (remote.meta?.categories ?? [...SC_CATEGORIES]).map((v) => ({ value: v, label: v })));
const promoOpts = computed(() => (remote.meta?.promoTypes ?? [...PROMO_TYPES]).map((v) => ({ value: v, label: v })));
const statusOpts = computed(() => (remote.meta?.statuses ?? ['全部', '草稿', '运行中', '已完成', '失败']).map((v) => ({ value: v, label: v === '全部' ? '全部状态' : v })));
async function pick(s: Scenario) {
  if (mode.value !== 'one') { toggleCmp(s); return; }
  if (s.id === current.value?.id) return;
  if (dirty.value && !(await confirmDiscard())) return;
  passGuard = true;
  setQ({ sc: s.id });
}
function toggleCmp(s: Scenario) {
  if (s.status !== '已完成') return;
  const ids = [...cmpIds.value];
  const i = ids.indexOf(s.id);
  if (i >= 0) ids.splice(i, 1); else if (ids.length < 3) ids.push(s.id); else { message.warning('最多对比 3 个方案'); return; }
  setQ({ cmp: ids.join(',') || null, pick: ids.includes(q1('pick')) ? q1('pick') : null });
}
const cmpTip = (s: Scenario) => (s.status !== '已完成' ? `${s.status}的方案没有推演结果，不能对比`
  : !cmpIds.value.includes(s.id) && cmpIds.value.length >= 3 ? '最多对比 3 个方案' : '');

/* ───── 当前方案：编辑中的配置（与已运行配置比较判断是否改动） ───── */
const form = reactive<{ name: string; scope: ScScope; drivers: ScDrivers }>({ name: '', scope: { area: '', category: '', weeks: 8 }, drivers: BASE_DRIVERS() });
const load = (s?: Scenario) => { if (!s) return; form.name = s.name; form.scope = { ...s.scope }; form.drivers = { ...s.drivers }; };
watch(() => current.value?.id, () => load(current.value), { immediate: true });
/** cfgDirty：范围 / 驱动改了（结果作废，需重新运行）；dirty：再加上改名（离开 / 切换要确认） */
/** 浅比较（与键顺序无关：后端 JSONB 返回的键顺序与 mock 不同） */
const sameCfg = (a: object, b: object) => {
  const x = a as Record<string, unknown>, y = b as Record<string, unknown>;
  const ks = Object.keys(x);
  return ks.length === Object.keys(y).length && ks.every((k) => x[k] === y[k]);
};
const cfgDirty = computed(() => !!current.value && (!sameCfg(form.scope, current.value.scope)
  || !sameCfg(form.drivers, current.value.drivers)));
const nameDirty = computed(() => !!current.value && form.name.trim() !== current.value.name);
const dirty = computed(() => cfgDirty.value || nameDirty.value);
useUnsavedGuard(dirty);
/** 浏览器后退 / 直接改 ?sc= 或 ?mode= 切换方案时同样提示；pick / 新建已确认过的跳过一次 */
let passGuard = false;
onBeforeRouteUpdate(async (to, from) => {
  if (passGuard) { passGuard = false; return true; }
  if (!dirty.value || (to.query.sc === from.query.sc && to.query.mode === from.query.mode)) return true;
  return await confirmDiscard();
});
const saveName = async () => {
  const s = current.value; if (!s || !form.name.trim()) return;
  if (REAL_API) {
    try { const d = asSc(await renameScenario(s.id, form.name.trim())); remote.detail = d; form.name = d.name; remote.rev += 1; message.success('名称已保存'); } catch (e) { apiFail(e); }
    return;
  }
  s.name = form.name.trim(); form.name = s.name; message.success('名称已保存');
};
/** 别人的方案只读：不能改、运行、转动作，可复制为自己的新方案 */
const notMine = computed(() => !!current.value && current.value.owner !== CURRENT_USER);
const lockReason = computed(() => readonly.value ? roReason.value
  : notMine.value ? `${current.value!.owner} 的方案，只读（不能修改、运行或转为动作）；如需调整请“复制为新方案”`
  : current.value?.status === '运行中' ? '推演运行中，完成后可修改'
  : current.value?.staged.length ? `已转为 ${current.value.staged.length} 条暂存动作，配置锁定；如需调整请“复制为新方案”` : '');
const noDriver = computed(() => form.drivers.pricePct === 0 && form.drivers.replenishPct === 0 && form.drivers.safetyDays === 7 && (form.drivers.promo === '无' || form.drivers.promoDepth === 0));
watch(() => form.drivers.promo, (p) => { if (p === '无') { form.drivers.promoDepth = 0; form.drivers.promoWeeks = 0; } else if (!form.drivers.promoDepth) { form.drivers.promoDepth = 10; form.drivers.promoWeeks = 2; } });

const needRun = computed(() => !!current.value && (current.value.status !== '已完成' || cfgDirty.value));
const runBlocked = computed(() => lockReason.value || (noDriver.value ? '至少调整一个驱动（价格 / 库存 / 促销）' : ''));
async function run() {
  const s = current.value; if (!s || runBlocked.value) return;
  if (s.result) {
    const ok = await confirm.ask({
      title: '重新运行推演', target: `${s.id} ${s.name}`, okText: '重新运行',
      fields: [{ label: '上一次运行', value: s.result.ranAt }],
      impact: '会用当前配置重新计算，覆盖上一次的结果（指标、影响明细、对比）；已暂存到动作中心的动作不受影响。',
    });
    if (!ok || current.value?.id !== s.id) return;
  }
  if (REAL_API) {
    const id = s.id;
    const body = { name: form.name.trim() || undefined, scope: { ...form.scope }, drivers: { ...form.drivers } };
    remote.detail = { ...s, status: '运行中', failReason: undefined, result: undefined };
    try {
      const d = asSc(await runScenario(id, body));
      const res = await getScenarioResult(id); // 运行后单独取结果（推演引擎改异步后这里改为轮询）
      delete remote.plans[id];
      if (scId.value === id) { remote.detail = { ...d, result: { ...res, impacts: [] } }; load(remote.detail); }
      remote.rev += 1;
      message.success(`${id} 推演完成`);
    } catch (e) { apiFail(e); await loadDetail(); }
    return;
  }
  s.name = form.name.trim() || s.name; s.scope = { ...form.scope }; s.drivers = { ...form.drivers };
  s.status = '运行中'; s.failReason = undefined; s.result = undefined;
  const id = s.id;
  window.setTimeout(() => {
    const x = scenarioById(id); if (!x || x.status !== '运行中') return;
    x.result = simulate(x.scope, x.drivers, now()); x.status = '已完成';
    message.success(`${x.id} 推演完成`);
  }, 1200);
}
async function create(from?: Scenario) {
  if (readonly.value) return;
  if (dirty.value && !(await confirmDiscard())) return;
  passGuard = true;
  if (REAL_API) {
    try {
      const d = asSc(await createScenario(from?.id));
      remote.detail = d; statusF.value = '全部'; kw.value = ''; remote.rev += 1;
      await nextTick(); // 先让筛选重置（usePagedQuery 会改 URL 回第 1 页）生效，再写 ?sc=，避免被旧 query 覆盖
      passGuard = true;
      router.replace({ query: { ...route.query, sc: d.id, mode: undefined, page: undefined } });
      message.success(`已新建 ${d.id}`);
    } catch (e) { passGuard = false; apiFail(e); }
    return;
  }
  const s: Scenario = {
    id: nextScenarioId(), name: from ? `${from.name}（副本）` : '新方案', owner: ME, createdAt: now(), baseVer: 'v2026.10.3', status: '草稿',
    scope: from ? { ...from.scope } : { area: '华东大区全部', category: '全部品类', weeks: 8 }, drivers: from ? { ...from.drivers } : BASE_DRIVERS(), staged: [],
  };
  SCENARIOS.unshift(s);
  statusF.value = '全部'; kw.value = '';
  router.replace({ query: { ...route.query, sc: s.id, mode: undefined, page: undefined } });
}

/* ───── 结果 ───── */
const kpiDelta = (k: ScKpi) => (k.key === 'promo' || k.base === 0 ? k.value - k.base : (k.value - k.base) / k.base);
const kpiText = (k: ScKpi) => (k.key === 'promo' || k.base === 0 ? `+${k.value.toFixed(1)} ${k.unit}`
  : k.unit === '%' ? `${k.value >= k.base ? '+' : '−'}${Math.abs(k.value - k.base).toFixed(1)} pt` : fmtPct(kpiDelta(k)));
const fmtV = (k: ScKpi, v: number) => (k.unit === '%' ? `${v.toFixed(1)}%` : k.unit === '天' ? `${v.toFixed(1)} 天` : `${v.toFixed(1)} 万`);

const lineOpt = computed(() => {
  const r = current.value?.result; if (!r) return {};
  return {
    tooltip: { trigger: 'axis' }, legend: { top: 0, right: 0, itemWidth: 14 },
    grid: { left: 48, right: 16, top: 28, bottom: 24 },
    xAxis: { type: 'category', data: r.weeks, axisLine: { lineStyle: { color: GRAY.g5 } } },
    yAxis: { type: 'value', name: '万元', splitLine: { lineStyle: { color: GRAY.g4 } } },
    series: [
      { name: '基线销售额', type: 'line', data: r.baseSales.map((v) => +(v / 1e4).toFixed(1)), lineStyle: { type: 'dashed', color: GRAY.g7 }, itemStyle: { color: GRAY.g7 }, symbol: 'none' },
      { name: '方案销售额', type: 'line', data: r.sales.map((v) => +(v / 1e4).toFixed(1)), itemStyle: { color: BRAND }, symbol: 'circle', symbolSize: 5 },
    ],
  };
});
const ipq = usePagedQuery({ defaultSize: 6, prefix: 'im', filters: [() => current.value?.id, () => current.value?.result?.ranAt] });
const impPaged = computed(() => (live.value ? remote.imp : paginate(current.value?.result?.impacts ?? [], { page: ipq.page.value, size: ipq.size.value })));
let impSeq = 0;
async function loadImp() {
  const s = current.value; const seq = ++impSeq;
  if (!s?.result) { remote.imp = { items: [], total: 0 }; return; }
  try { const r = await listScenarioImpacts(s.id, ipq.page.value, ipq.size.value); if (seq === impSeq) remote.imp = { items: r.items, total: r.total }; } catch (e) { readFail(e); }
}
if (live.value) watch([() => current.value?.id, () => current.value?.result?.ranAt, ipq.page, ipq.size], loadImp, { immediate: true });
const impCols = [
  { title: '商品', key: 'sku', ellipsis: true }, { title: '门店', key: 'store', ellipsis: true },
  { title: '基线销售额', key: 'base', align: 'right', width: 110 }, { title: '方案销售额', key: 'sales', align: 'right', width: 110 },
  { title: '变化', key: 'delta', align: 'right', width: 84 }, { title: '缺货率', key: 'so', align: 'right', width: 132 },
  { title: '建议动作', key: 'suggest', width: 170 },
];
const money = (v: number) => `${(v / 1e4).toFixed(2)} 万`;

/* ───── 对比 ───── */
const pickId = computed(() => (cmpIds.value.includes(q1('pick')) ? q1('pick') : cmpIds.value[0] ?? ''));
const KPI_KEYS = ['sales', 'gp', 'rate', 'stockout', 'turn', 'promo'];
const kpiOf = (s: Scenario, key: string) => s.result!.kpis.find((k) => k.key === key)!;
const scopeDiff = computed(() => new Set(cmpList.value.map((s) => `${s.scope.area}|${s.scope.category}|${s.scope.weeks}`)).size > 1);
const CMP_COLORS = [BRAND, GRAY.g9, GRAY.g7];
const barOpt = computed(() => ({
  tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${v}%` }, legend: { top: 0, right: 0, itemWidth: 14 },
  grid: { left: 44, right: 12, top: 28, bottom: 24 },
  xAxis: { type: 'category', data: ['销售额', '毛利额', '毛利率', '缺货率', '周转天数'] },
  yAxis: { type: 'value', name: '较基线 %', splitLine: { lineStyle: { color: GRAY.g4 } } },
  series: cmpList.value.map((s, i) => ({ name: s.id, type: 'bar', barMaxWidth: 18, itemStyle: { color: CMP_COLORS[i] },
    data: KPI_KEYS.slice(0, 5).map((k) => { const x = kpiOf(s, k); return +(((x.value - x.base) / x.base) * 100).toFixed(1); }) })),
}));
const cmpLineOpt = computed(() => {
  const longest = cmpList.value.reduce((a, s) => Math.max(a, s.result!.weeks.length), 0);
  const base = cmpList.value[0]?.result;
  return {
    tooltip: { trigger: 'axis' }, legend: { top: 0, right: 0, itemWidth: 14 }, grid: { left: 48, right: 12, top: 28, bottom: 24 },
    xAxis: { type: 'category', data: Array.from({ length: longest }, (_, i) => `W${i + 1}`) },
    yAxis: { type: 'value', name: '万元', splitLine: { lineStyle: { color: GRAY.g4 } } },
    series: [
      ...(base && !scopeDiff.value ? [{ name: '基线', type: 'line', symbol: 'none', data: base.baseSales.map((v) => +(v / 1e4).toFixed(1)), lineStyle: { type: 'dashed', color: GRAY.g6 }, itemStyle: { color: GRAY.g6 } }] : []),
      ...cmpList.value.map((s, i) => ({ name: s.id, type: 'line', symbolSize: 4, itemStyle: { color: CMP_COLORS[i] }, data: s.result!.sales.map((v) => +(v / 1e4).toFixed(1)) })),
    ],
  };
});

/* ───── 转为暂存动作 ───── */
const target = computed<Scenario | undefined>(() => (mode.value === 'cmp'
  ? (live.value ? cmpList.value.find((x) => x.id === pickId.value) : scenarioById(pickId.value)) : current.value));
/** 转动作建议：mock 本地算；真接口按方案取 /stage-plan 缓存（运行 / 暂存后作废） */
const planOf = (s: Scenario): StagePlan[] | undefined => (live.value ? remote.plans[s.id] : stagePlanOf(s));
async function loadPlans() {
  const ss = (mode.value === 'cmp' ? cmpList.value : current.value ? [current.value] : [])
    .filter((x) => x.status === '已完成' && !x.staged.length && !remote.plans[x.id]);
  await Promise.all(ss.map(async (x) => { try { remote.plans[x.id] = await getScenarioStagePlan(x.id); } catch (e) { readFail(e); } }));
}
if (live.value) watch([mode, () => current.value?.id, () => current.value?.status, () => current.value?.result?.ranAt, () => cmpList.value.map((x) => x.id).join(',')], loadPlans, { immediate: true });
const convertBlocked = computed(() => {
  const s = target.value;
  if (readonly.value) return roReason.value;
  if (!s) return mode.value === 'cmp' ? '先勾选要对比的方案' : '';
  if (s.status !== '已完成') return '方案推演完成后才能转为动作';
  if (s.owner !== CURRENT_USER) return `${s.owner} 的方案，只读；如需转为动作请“复制为新方案”后运行`;
  if (mode.value === 'one' && cfgDirty.value) return '配置已改动，请先重新运行';
  if (s.staged.length) return `已转为 ${s.staged.length} 条暂存动作，请到动作中心确认`;
  const pl = planOf(s);
  if (!pl) return '正在加载可生成的动作…';
  if (!pl.length) return '该方案没有可生成的动作';
  return '';
});
const planOpen = ref(false);
const plan = ref<StagePlan[]>([]);
const planKeys = ref<string[]>([]);
const planFor = ref('');
function openPlan() {
  const s = target.value; if (!s || convertBlocked.value) return;
  // 同一方案再次打开（例如确认卡取消后）保留上次的勾选
  if (planFor.value !== s.id || !plan.value.length) { plan.value = [...(planOf(s) ?? [])]; planKeys.value = plan.value.map((p) => p.key); planFor.value = s.id; }
  planOpen.value = true;
}
const planCols = [
  { title: '动作', dataIndex: 'action', width: 150 }, { title: '对象', key: 'obj', width: 200, ellipsis: true },
  { title: '参数', dataIndex: 'params', ellipsis: true }, { title: '依据', dataIndex: 'reason', width: 190, ellipsis: true },
];
async function doStage() {
  const s = target.value; if (!s) return;
  const items = plan.value.filter((p) => planKeys.value.includes(p.key));
  if (!items.length) { message.warning('至少选择一条动作'); return; }
  planOpen.value = false;
  const count = (n: string) => items.filter((p) => p.action === n).length;
  const ok = await confirm.ask({
    title: '转为暂存动作', tag: '待确认', okText: `暂存 ${items.length} 条`,
    target: `情景推演 ${s.id}「${s.name}」`,
    fields: [
      { label: '动作', value: ['发起调价审批', '补货申请', '发放复购召回优惠券'].filter((n) => count(n)).map((n) => `${n} × ${count(n)}`).join('，') },
      { label: '去向', value: '动作中心 · 待确认' },
      { label: '确认人', value: ME },
      { label: '推演基于', value: `本体 ${s.baseVer} · 运行于 ${s.result?.ranAt}` },
    ],
    impact: '只生成“待确认”动作，不提交审批、不写外部系统。需要你在动作中心逐条确认提交后才会进入审批；未确认前可撤回。',
  });
  if (!ok) { planOpen.value = true; return; } // 取消确认卡：回到预览，勾选保持不变
  if (REAL_API) {
    try {
      const r = await stageScenario(s.id, items.map((p) => p.key));
      planFor.value = ''; plan.value = []; delete remote.plans[s.id];
      message.success(r.message);
      if (mode.value === 'cmp') await loadCmp(); else await loadDetail();
      remote.rev += 1;
    } catch (e) { apiFail(e); }
    return;
  }
  const at = now();
  const ids = items.map((p) => stageAction({
    action: p.action, objKind: p.objKind, objId: p.objId, objLabel: p.objLabel, params: p.params, initiator: ME, at,
    source: { type: '推演', scenarioId: s.id, scenarioName: s.name, confirmer: ME },
  }).id);
  s.staged = ids; planFor.value = ''; plan.value = [];
  message.success(`已暂存 ${ids.length} 条动作到动作中心（待确认）`);
}
const pendingLink = { path: '/use/ops/actions', query: { tab: 'pending' } };

const primary = computed<'run' | 'convert' | 'copy' | ''>(() => {
  if (mode.value === 'cmp') return 'convert';
  if (notMine.value) return 'copy';
  if (needRun.value) return 'run';
  return current.value?.staged.length ? '' : 'convert';
});
</script>

<template>
  <div class="sc">
    <!-- 方案列表 -->
    <div class="panel sc-list">
      <div class="panel-head">
        <a-radio-group v-model:value="mode" size="small" button-style="solid">
          <a-radio-button value="one">单个方案</a-radio-button>
          <a-radio-button value="cmp">方案对比</a-radio-button>
        </a-radio-group>
        <a-tooltip :title="roReason"><a-button size="small" :disabled="readonly" @click="create()"><PlusOutlined />新建方案</a-button></a-tooltip>
      </div>
      <div class="sc-filters">
        <a-select v-model:value="statusF" size="small" class="sc-w96"
          :options="statusOpts" />
        <a-input-search v-model:value="kw" size="small" placeholder="方案名称 / 编号" class="sc-kw" />
      </div>
      <div class="sc-items">
        <div v-for="s in paged.items" :key="s.id" class="sc-item"
          :class="{ 'sc-item-on': mode === 'one' ? s.id === current?.id : cmpIds.includes(s.id), 'sc-item-off': mode === 'cmp' && !!cmpTip(s) }"
          :data-id="s.id" @click="pick(s)">
          <a-tooltip v-if="mode === 'cmp'" :title="cmpTip(s)" placement="right">
            <a-checkbox :checked="cmpIds.includes(s.id)" :disabled="!!cmpTip(s)" class="sc-ck" @click.stop="toggleCmp(s)" />
          </a-tooltip>
          <div class="sc-item-name" :title="`${s.id} · ${s.owner} · ${s.createdAt.slice(5, 16)}${s.staged.length ? ` · 已暂存 ${s.staged.length} 条` : ''}`">{{ s.name }}</div>
          <span class="sc-item-owner t-12">{{ s.owner }}</span>
          <span class="sc-item-st"><a-badge :status="badge(s.status)" /><span class="t-12 c-2">{{ s.status }}</span></span>
        </div>
      </div>
      <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="simple" class="sc-pager"
        :total="paged.total" :default-size="pq.defaultSize" unit="个" empty-text="没有符合条件的方案" />
    </div>

    <!-- 单个方案 -->
    <div v-if="mode === 'one' && current" class="panel sc-main">
      <div class="panel-head">
        <div class="sc-title">
          <span class="t-16 fw-600">{{ current.name }}</span>
          <span class="num c-3 t-12">{{ current.id }}</span>
          <span><a-badge :status="badge(current.status)" /><span class="t-12 c-2">{{ current.status }}</span></span>
          <span v-if="cfgDirty && current.result" class="t-12 tl-attention"><ExclamationCircleOutlined /> 配置已改动，结果对应上一次运行</span>
          <span v-else-if="nameDirty" class="t-12 c-3">名称未保存</span>
          <span v-if="notMine" class="t-12 c-3">{{ current.owner }} 的方案 · 只读</span>
          <InfoTip :text="`${current.id} · ${current.owner} · 创建于 ${current.createdAt.slice(0, 16)}；基于本体 ${current.baseVer} · 价格弹性 −1.6（示意模型）`" />
        </div>
        <div class="sc-btns">
          <a-tooltip :title="roReason"><a-button :type="primary === 'copy' ? 'primary' : 'default'" :disabled="readonly" data-act="copy" @click="create(current)"><CopyOutlined />复制为新方案</a-button></a-tooltip>
          <a-tooltip :title="runBlocked">
            <a-button :type="primary === 'run' ? 'primary' : 'default'" :disabled="!!runBlocked || !needRun" :loading="current.status === '运行中'" data-act="run" @click="run">
              {{ current.status === '失败' ? '重新运行' : current.status === '运行中' ? '推演中' : '运行推演' }}
            </a-button>
          </a-tooltip>
          <a-tooltip :title="convertBlocked">
            <a-button :type="primary === 'convert' ? 'primary' : 'default'" :disabled="!!convertBlocked" data-act="convert" @click="openPlan">转为暂存动作</a-button>
          </a-tooltip>
        </div>
      </div>
      <div class="sc-body">
        <!-- 配置 -->
        <div class="sc-cfg">
          <div v-if="lockReason" class="sc-lock t-12 c-2">{{ lockReason }}</div>
          <a-form layout="horizontal" :label-col="{ style: { width: '84px' } }" size="small" :disabled="!!lockReason" class="sc-form">
            <div class="sc-sec t-12 c-3">方案</div>
            <a-form-item label="名称">
              <div class="sc-pair"><a-input v-model:value="form.name" :maxlength="30" @press-enter="saveName" />
                <a-button v-if="nameDirty" size="small" :disabled="!form.name.trim()" data-act="save-name" @click="saveName">保存名称</a-button></div>
            </a-form-item>
            <div class="sc-sec t-12 c-3">范围</div>
            <a-form-item label="区域"><a-select v-model:value="form.scope.area" :options="areaOpts" /></a-form-item>
            <a-form-item label="品类"><a-select v-model:value="form.scope.category" :options="catOpts" /></a-form-item>
            <a-form-item label="推演周数">
              <a-radio-group v-model:value="form.scope.weeks" size="small"><a-radio-button :value="4">4 周</a-radio-button><a-radio-button :value="8">8 周</a-radio-button><a-radio-button :value="12">12 周</a-radio-button></a-radio-group>
            </a-form-item>
            <div class="sc-sec t-12 c-3">驱动 · 价格</div>
            <a-form-item label="价格调整">
              <div class="sc-slide"><a-slider v-model:value="form.drivers.pricePct" :min="-20" :max="10" :step="1" :disabled="!!lockReason" class="sc-slider" /><span class="num sc-val">{{ form.drivers.pricePct > 0 ? '+' : '' }}{{ form.drivers.pricePct }}%</span></div>
            </a-form-item>
            <div class="sc-sec t-12 c-3">驱动 · 库存</div>
            <a-form-item label="补货量">
              <div class="sc-slide"><a-slider v-model:value="form.drivers.replenishPct" :min="-30" :max="50" :step="5" :disabled="!!lockReason" class="sc-slider" /><span class="num sc-val">{{ form.drivers.replenishPct > 0 ? '+' : '' }}{{ form.drivers.replenishPct }}%</span></div>
            </a-form-item>
            <a-form-item label="安全库存"><a-input-number v-model:value="form.drivers.safetyDays" :min="3" :max="14" addon-after="天" class="sc-w120" /></a-form-item>
            <div class="sc-sec t-12 c-3">驱动 · 促销</div>
            <a-form-item label="促销类型"><a-select v-model:value="form.drivers.promo" :options="promoOpts" /></a-form-item>
            <a-form-item label="力度 / 周数">
              <div class="sc-pair">
                <a-input-number v-model:value="form.drivers.promoDepth" :min="0" :max="30" addon-after="%" :disabled="form.drivers.promo === '无' || !!lockReason" class="sc-w110" />
                <a-input-number v-model:value="form.drivers.promoWeeks" :min="0" :max="4" addon-after="周" :disabled="form.drivers.promo === '无' || !!lockReason" class="sc-w110" />
              </div>
            </a-form-item>
          </a-form>
        </div>

        <!-- 结果 -->
        <div class="sc-res">
          <template v-if="current.status === '运行中'">
            <div class="sc-empty"><LoadingOutlined class="c-3 t-20" /><div class="c-2">推演运行中：按 {{ current.scope.area }} · {{ current.scope.category }} · {{ current.scope.weeks }} 周计算…</div></div>
          </template>
          <template v-else-if="current.status === '失败'">
            <div class="sc-empty">
              <div class="tl-failed fw-600">推演失败 <InfoTip text="数据补齐后可“重新运行”；失败不会自动重试。" /></div>
              <div class="c-2 sc-fail">{{ current.failReason }}</div>
            </div>
          </template>
          <template v-else-if="!current.result">
            <div class="sc-empty"><div class="c-2">草稿方案尚未运行：在左侧设置范围和驱动后点击“运行推演”</div></div>
          </template>
          <template v-else>
            <div class="sc-kpis">
              <a-tooltip v-for="k in current.result.kpis" :key="k.key" :title="k.key === 'promo' ? '基线无促销成本' : `基线 ${fmtV(k, k.base)}`">
                <div class="sc-kpi">
                  <div class="t-12 c-2">{{ k.label }}</div>
                  <div class="sc-kpi-v"><span class="t-20 num fw-600">{{ fmtV(k, k.value) }}</span><span class="t-12 num c-2">{{ kpiText(k) }}</span></div>
                </div>
              </a-tooltip>
            </div>
            <div v-if="current.staged.length" class="sc-staged t-12">
              <span class="c-2">已暂存 {{ current.staged.length }} 条</span><span class="c-3"> · </span><router-link :to="pendingLink">去动作中心</router-link>
            </div>
            <div class="sc-chart"><EChart :option="lineOpt" /></div>
            <div class="sc-imp-head"><span class="fw-600">影响明细</span><span class="t-12 c-3">按销售额变化绝对值排序 · 运行于 <span class="num">{{ current.result.ranAt }}</span></span></div>
            <div class="sc-imp">
              <a-table :columns="impCols" :data-source="impPaged.items" :row-key="(r: any) => `${r.sku}-${r.storeId}`" size="small" :pagination="false">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'sku'"><ObjRef kind="product" :id="record.sku">{{ record.name }}</ObjRef></template>
                  <template v-else-if="column.key === 'store'"><ObjRef kind="store" :id="record.storeId">{{ record.storeName }}</ObjRef></template>
                  <template v-else-if="column.key === 'base'"><span class="num">{{ money(record.baseSales) }}</span></template>
                  <template v-else-if="column.key === 'sales'"><span class="num">{{ money(record.sales) }}</span></template>
                  <template v-else-if="column.key === 'delta'"><span class="num">{{ fmtPct(record.sales / record.baseSales - 1) }}</span></template>
                  <template v-else-if="column.key === 'so'"><span class="num sc-nowrap">{{ record.stockoutBase }}% → {{ record.stockout }}%</span></template>
                  <template v-else-if="column.key === 'suggest'"><span :class="record.suggest === '—' ? 'c-3' : ''">{{ record.suggest }}</span></template>
                </template>
              </a-table>
            </div>
            <AppPagination v-model:page="ipq.page.value" v-model:size="ipq.size.value" variant="full" :total="impPaged.total" :default-size="ipq.defaultSize" :empty="false" class="sc-imp-pg" />
          </template>
        </div>
      </div>
    </div>

    <!-- 对比 -->
    <div v-else-if="mode === 'cmp'" class="panel sc-main">
      <div class="panel-head">
        <div class="sc-title">
          <span class="t-16 fw-600">方案对比</span>
          <span class="t-12 c-2 num">已选 {{ cmpIds.length }} / 3</span>
          <InfoTip text="在左侧勾选最多 3 个已完成方案对比" />
          <span v-if="scopeDiff" class="t-12 c-2">方案范围不同（区域 / 品类 / 周数），基线不同，仅供参考</span>
        </div>
        <div class="sc-btns">
          <span v-if="target" class="t-12 c-2">选定：<span class="num">{{ target.id }}</span></span>
          <a-tooltip :title="convertBlocked"><a-button type="primary" :disabled="!!convertBlocked" data-act="convert" @click="openPlan">将选定方案转为暂存动作</a-button></a-tooltip>
        </div>
      </div>
      <div v-if="!cmpList.length" class="sc-empty"><div class="c-2">尚未选择方案：在左侧勾选最多 3 个已完成的方案</div></div>
      <div v-else class="sc-cmp">
        <div class="sc-cmp-charts">
          <div class="sc-cmp-chart"><div class="t-12 fw-600">关键指标较基线变化</div><div class="sc-cmp-ec"><EChart :option="barOpt" /></div></div>
          <div class="sc-cmp-chart"><div class="t-12 fw-600">周销售额</div><div class="sc-cmp-ec"><EChart :option="cmpLineOpt" /></div></div>
        </div>
        <table class="sc-ct">
          <thead>
            <tr>
              <th class="sc-ct-k">指标</th>
              <th v-for="s in cmpList" :key="s.id" :class="{ 'sc-ct-on': s.id === pickId }">
                <div class="sc-ct-name" :title="s.name"><a-radio :checked="s.id === pickId" @change="setQ({ pick: s.id })"><span class="num">{{ s.id }}</span></a-radio>{{ s.name }}</div>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr><td class="c-3">范围</td><td v-for="s in cmpList" :key="s.id" :class="{ 'sc-ct-on': s.id === pickId }" class="t-12">{{ s.scope.area }} · {{ s.scope.category }} · {{ s.scope.weeks }} 周</td></tr>
            <tr><td class="c-3">价格 / 补货</td><td v-for="s in cmpList" :key="s.id" :class="{ 'sc-ct-on': s.id === pickId }" class="t-12 num">
              价格 {{ s.drivers.pricePct > 0 ? '+' : '' }}{{ s.drivers.pricePct }}% · 补货 {{ s.drivers.replenishPct > 0 ? '+' : '' }}{{ s.drivers.replenishPct }}% · 安全库存 {{ s.drivers.safetyDays }} 天</td></tr>
            <tr><td class="c-3">促销</td><td v-for="s in cmpList" :key="s.id" :class="{ 'sc-ct-on': s.id === pickId }" class="t-12 num">
              {{ s.drivers.promo === '无' ? '无' : `${s.drivers.promo} ${s.drivers.promoDepth}% × ${s.drivers.promoWeeks} 周` }}</td></tr>
            <tr v-for="key in KPI_KEYS" :key="key">
              <td class="c-3">{{ kpiOf(cmpList[0], key).label }}</td>
              <td v-for="s in cmpList" :key="s.id" :class="{ 'sc-ct-on': s.id === pickId }">
                <span class="num">{{ fmtV(kpiOf(s, key), kpiOf(s, key).value) }}</span>
                <span class="num t-12 sc-ct-d c-3">{{ kpiText(kpiOf(s, key)) }}</span>
              </td>
            </tr>
            <tr><td class="c-3">暂存动作</td><td v-for="s in cmpList" :key="s.id" :class="{ 'sc-ct-on': s.id === pickId }" class="t-12">
              <template v-if="s.staged.length">已暂存 {{ s.staged.length }} 条 · <router-link :to="pendingLink">去动作中心</router-link></template>
              <span v-else-if="s.owner !== CURRENT_USER" class="c-3">{{ s.owner }} 的方案 · 只读</span>
              <span v-else class="c-3">可生成 {{ planOf(s)?.length ?? '…' }} 条</span>
            </td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 转为暂存动作：预览 -->
    <a-modal v-model:open="planOpen" :width="920" ok-text="下一步" cancel-text="取消" @ok="doStage">
      <template #title>转为暂存动作 <span class="t-12 c-2 fw-400">· 方案 <span class="num">{{ target?.id }}</span>「{{ target?.name }}」</span>
        <InfoTip text="按方案的驱动生成以下动作，勾选要暂存的条目。暂存后在动作中心“待确认”，不会自动提交或执行。" /></template>
      <a-table :columns="planCols" :data-source="plan" row-key="key" size="small" :pagination="false"
        :row-selection="{ selectedRowKeys: planKeys, onChange: (k: any[]) => (planKeys = k as string[]) }" class="sc-plan">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'obj'">
            <ObjRef :kind="record.objKind" :id="record.objId">{{ record.objLabel }}</ObjRef>
          </template>
        </template>
      </a-table>
    </a-modal>
  </div>
</template>

<style scoped>
.sc { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.sc-list { width: 340px; flex: none; }
.sc-filters { display: flex; gap: 8px; padding: 8px 16px 4px; }
.sc-w96 { width: 104px; flex: none; }
.sc-kw { flex: 1; }
.sc-cmp-hint { margin-left: auto; margin-right: 8px; white-space: nowrap; }
.sc-items { flex: 1; min-height: 0; padding: 4px 8px; }
.sc-item { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.sc-item:hover { background: var(--g2); }
.sc-item-on, .sc-item-on:hover { background: var(--brand-bg); }
.sc-item-off { cursor: default; }
.sc-item-off .sc-item-name { color: var(--g8); }
.sc-item-name { flex: 1; min-width: 0; }
.sc-item-owner { flex: none; color: var(--g10); }
.sc-item-main { flex: 1; min-width: 0; }
.sc-item-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sc-item-st { flex: none; width: 64px; white-space: nowrap; }
.sc-pager { padding: 4px 16px 8px; }
.sc-main { flex: 1; }
.sc-title { display: flex; align-items: center; gap: 12px; min-width: 0; }
.sc-btns { display: flex; align-items: center; gap: 8px; flex: none; }
.sc-body { flex: 1; min-height: 0; display: flex; }
.sc-cfg { width: 360px; flex: none; border-right: var(--border); padding: 8px 16px; display: flex; flex-direction: column; }
.sc-lock { padding: 6px 8px; background: var(--g2); border: var(--border); border-radius: var(--radius); margin-bottom: 4px; }
.sc-form :deep(.ant-form-item) { margin-bottom: 8px; }
.sc-sec { margin: 8px 0 6px; padding-top: 6px; border-top: var(--border); }
.sc-sec:first-of-type { border-top: 0; padding-top: 0; }
.sc-slide { display: flex; align-items: center; gap: 8px; }
.sc-slider { flex: 1; margin: 6px 4px; }
.sc-val { width: 44px; text-align: right; }
.sc-pair { display: flex; gap: 8px; }
.sc-w110 { width: 112px; }
.sc-w120 { width: 128px; }
.sc-cfg-foot { margin-top: auto; padding-top: 8px; }
.sc-res { flex: 1; min-width: 0; display: flex; flex-direction: column; padding: 12px 16px 0; }
.sc-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; }
.sc-fail { max-width: 560px; text-align: center; }
.sc-kpis { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; flex: none; }
.sc-kpi { border: var(--border); border-radius: var(--radius); padding: 8px 12px; min-width: 0; }
.sc-kpi > div { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sc-kpi-v { display: flex; align-items: baseline; gap: 8px; }
.sc-staged { margin-top: 8px; white-space: nowrap; }
.sc-chart { flex: 1 1 200px; min-height: 160px; max-height: 280px; margin-top: 8px; }
.sc-imp-head { display: flex; justify-content: space-between; align-items: center; margin: 4px 0; flex: none; }
.sc-imp { flex: none; }
.sc-imp-pg { padding: 4px 0 8px; }
.sc-cmp { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 12px 16px; gap: 12px; }
.sc-cmp-charts { flex: 1 1 260px; min-height: 200px; max-height: 340px; display: flex; gap: 16px; }
.sc-cmp-chart { flex: 1; min-width: 0; display: flex; flex-direction: column; border: var(--border); border-radius: var(--radius); padding: 8px 12px; }
.sc-cmp-ec { flex: 1; min-height: 0; }
.sc-ct { width: 100%; border-collapse: collapse; flex: none; table-layout: fixed; }
.sc-ct th, .sc-ct td { border-bottom: var(--border); padding: 6px 12px; text-align: left; vertical-align: top; }
.sc-ct th { font-weight: var(--fw-strong); background: var(--g2); }
.sc-ct-k { width: 120px; }
.sc-ct .sc-ct-on { background: var(--brand-bg); }
.sc-ct-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sc-ct td { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sc-ct-d { margin-left: 8px; }
.sc-nowrap { white-space: nowrap; }
</style>
