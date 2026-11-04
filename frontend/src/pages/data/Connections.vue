<script setup lang="ts">
/**
 * 数据连接：左侧数据源列表（simple 分页），右侧详情（连接参数 / 库表 / 同步计划）。
 * URL：?ds=DS-001&tab=params|tables|sync&form=new|edit，刷新可还原。
 * 新建 / 编辑在抽屉表单里（达梦显示兼容模式，金仓显示 database_mode，ERP 显示系统和应用 ID）；删除走 ConfirmActionModal。
 * 历史版本只读（useReadonly）；表单有未保存修改时，关闭抽屉或离开页面要确认（useUnsavedGuard）。
 * 开关 data 切到真接口时只显示数据库结果；失败保留真实模式并提示，不切演示分支。
 */
import { computed, reactive, ref, watch, onScopeDispose } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import InfoTip from '@/components/InfoTip.vue';
import ObjectMappingModal from '@/pages/ontology/parts/ObjectMappingModal.vue';
import GovernedBindingModal from './GovernedBindingModal.vue';
import ExternalReadBindingModal from './ExternalReadBindingModal.vue';
import CdcEventMappingModal from './CdcEventMappingModal.vue';
import { fetchDatasourceMappings, type DatasourceMapping } from '@/api/objectImport';
import { useConfirmStore } from '@/stores/confirm';
import { useAuthStore } from '@/stores/auth';
import { useReadonly } from '@/composables/useReadonly';
import { useUnsavedGuard, confirmDiscard } from '@/composables/useUnsavedGuard';
import { paginate, fmtInt, EMPTY } from '@/mock';
import {
  DATASOURCES, DS_TYPES, DS_DEFAULT_PORT, DM_COMPAT, KB_MODES, ERP_SYSTEMS, dsTables,
  type Datasource, type DsType, type DsStatus, type DsTable,
} from '@/mock/datasources';
import { currentApiContext, isApiError } from '@/api/http';
import {
  isDataReal, queryDatasources, queryDsTables, queryDsRuns, getDatasource, createDatasource, updateDatasource, deleteDatasource,
  testDatasource, testDatasourceParams, listDatasourceSchemas, refreshDatasourceTables, getObjectMirror, startObjectMirror, cancelObjectMirror, listObjectMirrorRuns, getMirrorScheduler, type MirrorRun, type MirrorState, type DatasourceWrite,
  getObjectCdc, probeObjectCdc, planObjectCdc, installObjectCdc, enableObjectCdc, pauseObjectCdc, resumeObjectCdc, consumeObjectCdcOnce, type CdcState, type CdcProbe, type CdcPlan,
} from '@/api/datasources';

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) =>
  router.replace({ query: { ...route.query, ...patch } });

const { readonly, reason } = useReadonly();
const real = ref(isDataReal());
const list = ref<Datasource[]>(real.value ? [] : DATASOURCES.map((d) => ({ ...d })));
const contextKey = () => JSON.stringify(currentApiContext());
let listSeq = 0;
const apiFail = (what: string, e: unknown) => {
  console.error(`[api] 数据连接：${what}失败`, e);
  message.error(`${what}失败：${isApiError(e) ? e.message || e.code : e instanceof Error ? e.message : '未知错误'}`);
};

// ---------- 左侧列表 ----------
const LIST_SIZE = real.value ? 10 : 12;
const listTotal = ref(0), listContext = ref(''), listLoading = ref(false);
const kw = ref('');
const type = ref<'全部' | DsType>('全部');
const listPage = ref(1);
watch([kw, type], () => { listPage.value = 1; });
const filtered = computed(() => list.value.filter((d) =>
  (type.value === '全部' || d.type === type.value) &&
  (!kw.value || d.name.includes(kw.value) || d.host.includes(kw.value) || d.database.toLowerCase().includes(kw.value.toLowerCase()))));
const listPaged = computed(() => real.value ? { items: listContext.value === contextKey() ? list.value : [], total: listContext.value === contextKey() ? listTotal.value : 0 } : paginate(filtered.value, { page: listPage.value, size: LIST_SIZE }));
async function loadSources() {
  const n = ++listSeq, key = contextKey();
  if (!real.value) return;
  if (listContext.value !== key) { list.value = []; listTotal.value = 0; listContext.value = key; }
  listLoading.value = true;
  try { const result = await queryDatasources({ page: listPage.value, size: LIST_SIZE, kw: kw.value || undefined, type: type.value });
    if (n !== listSeq || key !== contextKey()) return;
    list.value = result.items; listTotal.value = result.total;
    if (result.page !== listPage.value) listPage.value = result.page;
  } catch (e) { if (n === listSeq && key === contextKey()) apiFail('加载数据连接', e); }
  finally { if (n === listSeq && key === contextKey()) listLoading.value = false; }
}
watch([contextKey, listPage, kw, type], loadSources, { immediate: true });

const selectedId = computed(() => q('ds') || (listContext.value === contextKey() ? list.value[0]?.id : ''));
const detail = ref<{ key: string; id: string; item: Datasource }>();
const selected = computed(() => real.value ? detail.value?.key === contextKey() && detail.value.id === selectedId.value ? detail.value.item : null : list.value.find((d) => d.id === q('ds')) ?? filtered.value[0] ?? null);
let detailSeq = 0;
watch([contextKey, selectedId], async ([key, id]) => {
  const n = ++detailSeq; detail.value = undefined;
  if (!real.value || !id) return;
  try { const item = await getDatasource(id); if (n === detailSeq && key === contextKey() && id === selectedId.value) detail.value = { key, id, item }; }
  catch (e) { if (n === detailSeq && key === contextKey()) apiFail('加载连接详情', e); }
}, { immediate: true });
// 选中项不在当前页时，翻到它所在的页
watch(() => q('ds'), (id) => {
  if (real.value) return;
  const i = filtered.value.findIndex((d) => d.id === id);
  if (i >= 0) listPage.value = Math.floor(i / LIST_SIZE) + 1;
}, { immediate: true });
const tab = computed<string>({
  get: () => (['params', 'tables', 'sync'].includes(q('tab')) ? q('tab') : 'params'),
  set: (v: string) => setQuery({ tab: v === 'params' ? undefined : v }),
});
const select = (id: string) => setQuery({ ds: id });

const statusBadge = (s: DsStatus) => (s === '异常' ? 'error' : 'default');
const typeShort: Record<DsType, string> = { PostgreSQL: 'PG', '达梦 DM8': 'DM8', '人大金仓 KingbaseES': 'KES', MySQL: 'MySQL', ERP: 'ERP' };

// ---------- 详情：连接参数 ----------
const jdbc = (d: Datasource) => ({
  PostgreSQL: `jdbc:postgresql://${d.host}:${d.port}/${d.database}`,
  '达梦 DM8': `jdbc:dm://${d.host}:${d.port}${d.database ? `?schema=${encodeURIComponent(d.database)}` : ''}`,
  '人大金仓 KingbaseES': `jdbc:kingbase8://${d.host}:${d.port}/${d.database}`,
  MySQL: `jdbc:mysql://${d.host}:${d.port}/${d.database}?useSSL=${d.ssl}`,
  ERP: `https://${d.host}/openapi/${d.database.toLowerCase()}`,
}[d.type]);

// ---------- 详情：库表（simple 分页） ----------
const TABLE_SIZE = real.value ? 10 : 12;
const tablePage = ref(1);
const tableKw = ref('');
watch([() => selected.value?.id, tableKw], () => { tablePage.value = 1; });
const realTables = ref<{ id: string; key: string; items: DsTable[]; total: number } | null>(null);
const tableLoading = ref(false);
const mappings = ref<DatasourceMapping[]>([]);
const mappingError = ref(''), mappingOpen = ref(false), mappingTable = ref<DsTable>();
const governedOpen = ref(false), governedObject = ref('');
function mappedObjects(table: DsTable) { return mappings.value.filter((m) => m.schema === table.schema && m.table === table.name); }
function editMapping(table: DsTable) { if (!real.value || readonly.value) return; mappingTable.value = table; mappingOpen.value = true; }
let tableSeq = 0;
async function loadTables() {
  const n = ++tableSeq, id = selected.value?.id, key = contextKey();
  if (!real.value || !id || tab.value !== 'tables') return;
  if (realTables.value?.key !== key || realTables.value.id !== id) realTables.value = { id, key, items: [], total: 0 };
  tableLoading.value = true;
  try { const result = await queryDsTables(id, { page: tablePage.value, size: TABLE_SIZE, kw: tableKw.value || undefined });
    if (n !== tableSeq || key !== contextKey() || selected.value?.id !== id) return;
    realTables.value = { id, key, items: result.items, total: result.total };
    if (result.page !== tablePage.value) tablePage.value = result.page;
  } catch (e) { if (n === tableSeq && key === contextKey()) apiFail('加载库表', e); }
  finally { if (n === tableSeq && key === contextKey()) tableLoading.value = false; }
}
watch([() => selected.value?.id, contextKey, tab, tablePage, tableKw], loadTables, { immediate: true });
let mappingSeq = 0;
async function loadMappings() {
  const n = ++mappingSeq, key = contextKey(), id = selected.value?.id; mappings.value = []; mappingError.value = '';
  if (!real.value || !id) return;
  try { const rows = await fetchDatasourceMappings(id); if (n === mappingSeq && key === contextKey()) mappings.value = rows; }
  catch (e) { if (n === mappingSeq && key === contextKey()) mappingError.value = `映射加载失败：${e instanceof Error ? e.message : String(e)}`; }
}
watch([() => selected.value?.id, contextKey], () => { mappingOpen.value = false; void loadMappings(); }, { immediate: true });
const tableSource = (d: Datasource): DsTable[] => !real.value ? dsTables(d) : realTables.value?.id === d.id && realTables.value.key === contextKey() ? realTables.value.items : [];
const tables = computed(() => (selected.value ? tableSource(selected.value) : []).filter((t) => real.value || !tableKw.value || t.name.toLowerCase().includes(tableKw.value.toLowerCase()) || t.comment.includes(tableKw.value)));
const tableTotal = computed(() => real.value ? realTables.value?.key === contextKey() && realTables.value.id === selected.value?.id ? realTables.value.total : 0 : tables.value.length);
const tablesPaged = computed(() => real.value ? { items: tables.value, total: tableTotal.value } : paginate(tables.value, { page: tablePage.value, size: TABLE_SIZE }));
const runPage = ref(1), runTotal = ref(0), realRuns = ref<Datasource['runs']>([]);
const runsKey = () => JSON.stringify([contextKey(), selected.value?.id]);
const loadedRunsKey = ref(''); let runSeq = 0;
watch([runsKey, runPage, tab], async () => {
  const n = ++runSeq, key = runsKey(), id = selected.value?.id;
  if (loadedRunsKey.value !== key) { runTotal.value = 0; realRuns.value = []; loadedRunsKey.value = key; }
  if (!real.value || !id || tab.value !== 'sync') return;
  try { const result = await queryDsRuns(id, runPage.value, 10); if (n !== runSeq || key !== runsKey()) return;
    realRuns.value = result.items; runTotal.value = result.total; if (runPage.value !== result.page) runPage.value = result.page;
  } catch (e) { if (n === runSeq && key === runsKey()) apiFail('加载同步记录', e); }
});
watch(runsKey, () => { runPage.value = 1; });
const syncRuns = computed(() => real.value ? loadedRunsKey.value === runsKey() ? realRuns.value : [] : selected.value?.runs ?? []);

const tableCols = [
  { title: 'Schema', dataIndex: 'schema', key: 'schema', width: 110 },
  { title: '表名', dataIndex: 'name', key: 'name', width: 200, ellipsis: true },
  { title: '说明', dataIndex: 'comment', key: 'comment', width: 140, ellipsis: true },
  { title: '行数', key: 'rows', width: 120, align: 'right' as const },
  { title: '字段数', dataIndex: 'cols', key: 'cols', width: 80, align: 'right' as const },
  { title: '映射对象类型', key: 'objectType', width: 220, ellipsis: true },
  { title: '配置', key: 'mappingActions', width: 90 },
  { title: '最近更新', key: 'updatedAt', width: 150 },
];

const schemaOpen = ref(false), schemaBusy = ref(false), schemas = ref<string[]>([]), chosenSchemas = ref<string[]>([]), schemaTarget = ref('');
async function openSchemas() {
  const id = selected.value?.id, ctx = contextKey();
  if (!id || schemaBusy.value) return;
  schemaBusy.value = true; schemas.value = []; chosenSchemas.value = []; schemaTarget.value = id;
  try { const result = await listDatasourceSchemas(id); if (ctx !== contextKey() || id !== selected.value?.id) return; schemas.value = result.items; schemaOpen.value = true; }
  catch (e) { if (ctx === contextKey()) apiFail('加载 Schema', e); } finally { schemaBusy.value = false; }
}
async function refreshSchemas() {
  const id = schemaTarget.value, ctx = contextKey();
  if (!id || !chosenSchemas.value.length || schemaBusy.value) return;
  schemaBusy.value = true;
  try { await refreshDatasourceTables(id, chosenSchemas.value); if (ctx !== contextKey() || id !== selected.value?.id) return; schemaOpen.value = false; message.success('所选 Schema 的库表目录已更新；字段在导入时读取'); await loadTables(); }
  catch (e) { if (ctx === contextKey()) apiFail('更新库表目录', e); } finally { schemaBusy.value = false; }
}
watch(contextKey, () => { schemaOpen.value = false; });

const mirrorObjects = computed(() => Array.from(new Map(mappings.value.map(m => [m.objectId, m])).values()));
const cdcStates = ref<Record<string, CdcState>>({});
const cdcErrors = ref<Record<string, string>>({});
const cdcBusy = ref(''), cdcDetail = ref('');
const cdcProbes = ref<Record<string, CdcProbe>>({});
const cdcPlan = ref<CdcPlan>(), cdcPlanVersion = ref(0);
const cdcPhase: Record<string, string> = { not_installed: '未安装捕获', installed: '已安装，待启用', snapshot: '初始同步中', catching_up: '追赶增量', streaming: '持续同步中', paused: '已暂停消费', reconnecting: '正在重连', failed: '同步失败' };
const cdcCurrent = computed(() => cdcStates.value[cdcDetail.value]);
const cdcBinlog = computed(() => selected.value?.type === 'MySQL');
const mysqlServerId = ref(160081);
const cdcEventMappingOpen = ref(false);
const cdcObject = computed(() => mirrorObjects.value.find(o => o.objectId === cdcDetail.value));
const cdcActive = (id: string) => !!cdcStates.value[id]?.enabled;
let cdcSeq = 0, cdcPending = false, pollingDisposed = false;
async function loadCdc(poll = false) {
  if ((poll && cdcPending) || pollingDisposed) return;
  const n = ++cdcSeq;
  const key = contextKey(), ds = selected.value?.id;
  if (!real.value || !['达梦 DM8', 'MySQL'].includes(selected.value?.type ?? '') || tab.value !== 'sync') { cdcPending = false; return; }
  cdcPending = true;
  try {
    const objects = mirrorObjects.value;
    const results = await Promise.allSettled(objects.map(o => getObjectCdc(o.objectId)));
    if (pollingDisposed || n !== cdcSeq || key !== contextKey() || ds !== selected.value?.id || tab.value !== 'sync') return;
    const states: Record<string, CdcState> = {}, errors: Record<string, string> = {};
    results.forEach((r, i) => { const id = objects[i]!.objectId; if (r.status === 'fulfilled') states[id] = r.value; else errors[id] = isApiError(r.reason) ? r.reason.message : 'CDC 状态暂不可用'; });
    cdcStates.value = states; cdcErrors.value = errors;
  } finally { if (n === cdcSeq) cdcPending = false; }
}
watch([contextKey, () => selected.value?.id, tab, mirrorObjects], () => { cdcStates.value = {}; cdcErrors.value = {}; cdcProbes.value = {}; cdcDetail.value = ''; cdcPlan.value = undefined; void loadCdc(); }, { immediate: true });
const cdcTimer = window.setInterval(() => { if (!document.hidden) void loadCdc(true); }, 3000);
onScopeDispose(() => window.clearInterval(cdcTimer));
function openCdc(id: string) { cdcDetail.value = id; mysqlServerId.value = cdcStates.value[id]?.serverId ?? 160081; cdcPlan.value = undefined; }
async function cdcAction(action: 'probe' | 'plan' | 'install' | 'enable' | 'pause' | 'resume' | 'reinitialize' | 'consume') {
  const id = cdcDetail.value, state = cdcStates.value[id], key = contextKey();
  if (!id || !state || state.supported !== true || cdcBusy.value || (readonly.value && action !== 'probe')) return;
  if (['plan', 'enable', 'resume', 'reinitialize', 'consume'].includes(action) && state.version == null) { message.warning('请先配置读取授权'); return; }
  if (action === 'install' && !cdcPlan.value) return;
  if (['install', 'enable', 'pause', 'reinitialize'].includes(action)) {
    const impact = action === 'install' ? cdcBinlog.value ? '确认复制server-id与授权表；只验证现有binlog，不在源库创建触发器或表。' : '将在此对象绑定的源表安装事务触发器及专用捕获资源。仅执行下方已预览的 SQL。' : action === 'pause' ? cdcBinlog.value ? '暂停消费并保留提交游标；日志保留不足时须重建基线。' : '暂停读取和应用增量，源端触发器继续捕获变更，待处理记录将保留。' : action === 'reinitialize' ? '重新执行初始同步，再应用捕获的增量；同步期间读取结果可能尚未完整。' : '先执行初始同步，再持续应用已捕获的增量。';
    if (!await useConfirmStore().ask({ title: action === 'install' ? cdcBinlog.value ? '确认 MySQL binlog 订阅' : '安装事务触发器 CDC' : action === 'pause' ? '暂停 CDC 消费' : action === 'reinitialize' ? '重新初始化 CDC' : '启用 CDC', target: cdcObject.value?.name || id, fields: action === 'install' ? [{ label: '源表', value: `${cdcPlan.value?.capture.schema || ''}.${cdcPlan.value?.capture.table || ''}` }] : undefined, impact, okText: '确认执行' })) return;
    if (key !== contextKey() || id !== cdcDetail.value || readonly.value || cdcStates.value[id]?.supported !== true) return;
  }
  cdcBusy.value = action;
  try {
    if (action === 'probe') { const result = await probeObjectCdc(id); if (key === contextKey() && id === cdcDetail.value) cdcProbes.value[id] = result; }
    else if (action === 'plan') { const result = await planObjectCdc(id, state.version!, cdcBinlog.value ? mysqlServerId.value : undefined); if (key === contextKey() && id === cdcDetail.value) { cdcPlan.value = result; cdcPlanVersion.value = state.version!; } }
    else if (action === 'install') { await installObjectCdc(id, cdcPlanVersion.value, cdcPlan.value!.planDigest); if (key === contextKey() && id === cdcDetail.value) cdcPlan.value = undefined; }
    else if (action === 'pause') await pauseObjectCdc(id);
    else if (action === 'resume') await resumeObjectCdc(id, state.version!);
    else if (action === 'consume') await consumeObjectCdcOnce(id, state.version!);
    else await enableObjectCdc(id, state.version!, action === 'reinitialize', mirrorBatch.value);
    if (key === contextKey()) { await loadCdc(); await loadMirrors(); }
  } catch (e) { if (key === contextKey()) apiFail('CDC 操作', e); } finally { cdcBusy.value = ''; }
}
const cdcRecovery: Record<string, string> = { install: '请检测能力并预览安装 SQL，再确认安装捕获。', enable: '请确认源捕获已安装后启用。', resume: '源连接恢复后，可恢复消费。', reinitialize: '请先核对源表和读取授权，重新预览并安装捕获，再重新初始化。' };
const cdcSql = computed(() => !cdcPlan.value ? '' : Array.isArray(cdcPlan.value.sql) ? cdcPlan.value.sql.join(';\n\n') : cdcPlan.value.sql);
const cdcFenceSql = computed(() => cdcPlan.value?.bootstrapFenceSql ? `${cdcPlan.value.bootstrapFenceSql.replace(/;\s*$/, '')};\nCOMMIT;` : '');
function downloadCdcSql() {
  const plan = cdcPlan.value;
  if (!plan) return;
  const statements = Array.isArray(plan.sql) ? plan.sql : [plan.sql];
  const ddl = statements.map(sql => /^\s*CREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\b/i.test(sql) ? `${sql.trim()}\n/` : `${sql.trim().replace(/;\s*$/, '')};`).join('\n\n');
  const content = `-- DM disql 安装参考脚本：请先核对源表及专用捕获资源。\n-- DDL 可隐式提交，触发器体为完整语句，不可按分号拆分。\n-- DBA 执行后仍需在界面确认安装，由 API 校验资源并记录事务栅栏。\n${ddl}\n\nCOMMIT;\n\n${plan.bootstrapFenceSql ? `-- 以下是独立事务栅栏，不属于 DDL。请在关闭自动提交的事务会话中执行。\n-- NOWAIT 请求源表排他锁；存在未结束事务或缺少 LOCK 权限时失败。\n-- 如取锁失败，请停止执行并处理后重试同一安装计划，不可跳过栅栏启用基线。\n-- 成功取锁后 COMMIT 立即释放锁，不在持锁期间进行初始扫描。\n${cdcFenceSql.value}\n` : ''}`;
  const url = URL.createObjectURL(new Blob([content], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a'); link.href = url; link.download = `cdc-${cdcDetail.value.replace(/[^a-zA-Z0-9_-]/g, '_')}.sql`; link.click(); URL.revokeObjectURL(url);
}
const mirrorStates = ref<Record<string, MirrorState>>({});
const mirrorErrors = ref<Record<string, string>>({});
const schedulerState = ref<{ active: boolean; lastHeartbeat: string | null; error: string | null } | null>(null);
const mirrorBusy = ref('');
const mirrorBatch = ref(100);
let mirrorSeq = 0, mirrorPending = false;
async function loadMirrors(poll = false) {
  if ((poll && mirrorPending) || pollingDisposed) return;
  const n = ++mirrorSeq, key = contextKey(), ds = selected.value?.id;
  if (!real.value || !['达梦 DM8', 'MySQL'].includes(selected.value?.type ?? '') || tab.value !== 'sync') { mirrorPending = false; return; }
  mirrorPending = true;
  try {
    const objects = mirrorObjects.value;
    const results = await Promise.allSettled(objects.map(m => getObjectMirror(m.objectId)));
    if (pollingDisposed || n !== mirrorSeq || key !== contextKey() || ds !== selected.value?.id || tab.value !== 'sync') return;
    const states: Record<string, MirrorState> = {}, errors: Record<string, string> = {};
    results.forEach((result, index) => { const id = objects[index]!.objectId;
      if (result.status === 'fulfilled') states[id] = result.value;
      else errors[id] = isApiError(result.reason) ? result.reason.message : '同步状态暂不可用，请刷新重试';
    });
    mirrorStates.value = states; mirrorErrors.value = errors;
  } finally { if (n === mirrorSeq) mirrorPending = false; }
}
watch([contextKey, () => selected.value?.id, tab, mirrorObjects], () => { mirrorStates.value = {}; mirrorErrors.value = {}; void loadMirrors(); }, { immediate: true });
let schedulerSeq = 0, schedulerPending = false;
async function loadScheduler(poll = false) {
  if ((poll && schedulerPending) || pollingDisposed) return;
  const n = ++schedulerSeq, key = contextKey();
  if (!real.value || tab.value !== 'sync') { schedulerPending = false; return; }
  schedulerPending = true;
  const current = () => !pollingDisposed && n === schedulerSeq && key === contextKey() && tab.value === 'sync';
  try { const state = await getMirrorScheduler(); if (current()) schedulerState.value = state; }
  catch { if (current()) schedulerState.value = { active: false, lastHeartbeat: null, error: '无法获取调度状态' }; }
  finally { if (n === schedulerSeq) schedulerPending = false; }
}
watch([contextKey, tab], () => { schedulerState.value = null; void loadScheduler(); }, { immediate: true });
const schedulerTimer = window.setInterval(() => { if (!document.hidden) void loadScheduler(true); }, 10000);
onScopeDispose(() => window.clearInterval(schedulerTimer));
const mirrorTimer = window.setInterval(() => { if (!document.hidden && (Object.keys(mirrorErrors.value).length || Object.values(cdcStates.value).some(x => x.enabled) || Object.values(mirrorStates.value).some(x => x.status === 'queued' || x.status === 'running'))) void loadMirrors(true); }, 2500);
onScopeDispose(() => window.clearInterval(mirrorTimer));
onScopeDispose(() => { pollingDisposed = true; ++cdcSeq; ++mirrorSeq; ++schedulerSeq; });
async function runMirror(id: string) {
  const state = mirrorStates.value[id];
  if (readonly.value || mirrorBusy.value || !state || state.migrationRequired || mirrorActive(state) || cdcActive(id)) return;
  const key = contextKey(), ds = selected.value?.id; mirrorBusy.value = id;
  try { await startObjectMirror(id, { maxRows: mirrorBatch.value, restart: state.status !== 'not_started' && !state.resumable }); if (key === contextKey() && ds === selected.value?.id) { message.info(state.resumable ? '已提交继续同步任务' : '已提交同步任务，将自动读取至本轮完成'); await loadMirrors(); } }
  catch (e) { if (key === contextKey()) apiFail('启动数据同步', e); } finally { mirrorBusy.value = ''; }
}
async function cancelMirror(id: string) {
  if (readonly.value || mirrorBusy.value || !mirrorActive(mirrorStates.value[id]) || cdcActive(id)) return;
  const key = contextKey(), ds = selected.value?.id; mirrorBusy.value = id;
  try { await cancelObjectMirror(id); if (key === contextKey() && ds === selected.value?.id) { message.info('已请求取消，保留已完成的数据'); await loadMirrors(); } }
  catch (e) { if (key === contextKey()) apiFail('取消同步', e); } finally { mirrorBusy.value = ''; }
}
const mirrorHistory = ref(''), mirrorHistoryPage = ref(1), mirrorHistoryTotal = ref(0), mirrorHistoryRows = ref<MirrorRun[]>([]), mirrorHistoryBusy = ref(false);
const mirrorHistoryState = computed(() => mirrorStates.value[mirrorHistory.value]);
watch([contextKey, () => selected.value?.id], () => { mirrorHistory.value = ''; });
let mirrorHistorySeq = 0;
watch([mirrorHistory, mirrorHistoryPage, contextKey], async ([id]) => {
  const n = ++mirrorHistorySeq, key = contextKey(); mirrorHistoryRows.value = []; mirrorHistoryTotal.value = 0;
  if (!id) return;
  mirrorHistoryBusy.value = true;
  try { const result = await listObjectMirrorRuns(id, mirrorHistoryPage.value, 10); if (n === mirrorHistorySeq && key === contextKey()) { mirrorHistoryRows.value = result.items; mirrorHistoryTotal.value = result.total; } }
  catch (e) { if (n === mirrorHistorySeq && key === contextKey()) apiFail('加载同步记录', e); }
  finally { if (n === mirrorHistorySeq) mirrorHistoryBusy.value = false; }
});
function showMirrorHistory(id: string) { mirrorHistoryPage.value = 1; mirrorHistory.value = id; }
const runLabels: Record<string, string> = { queued: '排队中', running: '同步中', succeeded: '已完成', failed: '失败', cancelled: '已取消' };
const mirrorActive = (state?: MirrorState) => state?.status === 'queued' || state?.status === 'running';
const mirrorNeedsReview = (state?: MirrorState) => !!state && !state.migrationRequired && (state.stale || (state.restartRequired && !state.complete));
const mirrorText = (state?: MirrorState) => !state ? '状态加载中' : state.migrationRequired ? '需升级数据库' : mirrorNeedsReview(state) ? '需核对读取配置' : state.status === 'not_started' ? '未同步' : state.status === 'succeeded' ? state.complete ? state.deletionsAligned === true ? '本轮已完成' : '扫描完成 · 删除待核对' : '尚未完整同步' : `${runLabels[state.status]}${state.complete ? '' : ' · 尚未完整同步'}`;
const mirrorUpgradeMessage = '数据库尚未完成 0048 增量升级，请联系管理员应用该迁移，完成后刷新状态；无需重建或解除源绑定。';
const mirrorAction = (state?: MirrorState) => state?.resumable ? '继续同步' : state?.status === 'not_started' ? '同步数据' : state?.complete ? '更新镜像' : '重新同步';
function mirrorTime(value?: string | null) { if (!value) return EMPTY; const date = new Date(value); return Number.isNaN(date.getTime()) ? EMPTY : date.toLocaleString('zh-CN', { hour12: false }); }

// ---------- 测试连接 ----------
const testing = ref(false);
function connectionFailure(detail: string) {
  return /^连接失败[：:]/.test(detail) ? detail : `连接失败：${detail}`;
}
async function testConn(d: Datasource) {
  if (readonly.value || testing.value) return;
  const context = contextKey();
  const n = listSeq;
  const current = () => context === contextKey() && n === listSeq;
  testing.value = true;
  if (real.value) {
    try {
      const t = await testDatasource(d.id);
      if (!current()) return;
      const stored = await getDatasource(d.id);
      if (!current()) return;
      Object.assign(d, stored);
      const listed = list.value.find((item) => item.id === d.id);
      if (listed) Object.assign(listed, stored);
      if (t.ok) {
        if (t.msg.includes('库表清单刷新失败')) message.warning(`${t.msg}（连接耗时 ${t.ms} ms）`);
        else message.success(`连接成功，耗时 ${t.ms} ms${t.msg && t.msg !== '连接成功' ? `；${t.msg}` : ''}`);
        // 后台真连成功后重新读取持久库表清单。
        if (selected.value?.id === d.id) await loadTables();
      } else message.error(connectionFailure(t.msg));
    } catch (e) { if (current()) apiFail('测试连接', e); } finally { testing.value = false; }
    return;
  }
  testing.value = false;
  message.warning('真实连接测试未启用');
}

// ---------- 删除 ----------
const confirm = useConfirmStore();
async function remove(d: Datasource) {
  if (readonly.value) return;
  const context = contextKey();
  const n = listSeq;
  const current = () => context === contextKey() && n === listSeq && !readonly.value;
  const ok = await confirm.ask({
    title: '删除数据连接', target: `${d.name}（${d.id}）`, danger: true, okText: '删除',
    fields: [
      { label: '类型', value: d.type },
      { label: '地址', value: `${d.host}:${d.port} / ${d.database}` },
      { label: '映射的对象类型', value: d.objectTypes.length ? d.objectTypes.join('、') : '无' },
    ],
    impact: d.objectTypes.length
      ? `删除后同步计划停止，${d.objectTypes.join('、')} 的数据不再更新（已同步的数据保留）。`
      : '没有对象类型使用这个连接，删除后同步计划一起删除。',
  });
  if (!ok || !current()) return;
  if (real.value) {
    try { await deleteDatasource(d.id); } catch (e) { if (current()) apiFail('删除', e); return; }
    if (!current()) return;
  }
  list.value = list.value.filter((x) => x.id !== d.id);
  if (real.value) void loadSources();
  setQuery({ ds: undefined, tab: undefined });
  message.success(`已删除：${d.name}`);
}

// ---------- 新建 / 编辑（抽屉表单） ----------
interface Form { name: string; type: DsType; host: string; port: number; database: string; user: string; password: string; compatMode: string; databaseMode: string; erpSystem: string; appId: string; ssl: boolean; owner: string; mode: Datasource['sync']['mode']; cron: string }
const blank = (): Form => ({ name: '', type: 'PostgreSQL', host: '', port: 5432, database: '', user: '', password: '', compatMode: 'Oracle', databaseMode: 'oracle', erpSystem: ERP_SYSTEMS[0], appId: '', ssl: true, owner: auth.user?.name || auth.user?.account || '', mode: '实时（CDC）', cron: '-' });
const form = reactive<Form>(blank());
const formInit = ref('');
const formMode = computed(() => (q('form') === 'new' || q('form') === 'edit' ? q('form') : ''));
const formDirty = computed(() => !!formMode.value && JSON.stringify(form) !== formInit.value);
const guard = useUnsavedGuard(formDirty);
watch([formMode, () => formMode.value === 'edit' ? selected.value?.id : '', contextKey], ([m]) => {
  const d = selected.value;
  Object.assign(form, m === 'edit' && d ? {
    name: d.name, type: d.type, host: d.host, port: d.port, database: d.database, user: d.user, password: '',
    compatMode: d.compatMode ?? 'Oracle', databaseMode: d.databaseMode ?? 'oracle', erpSystem: d.erpSystem ?? ERP_SYSTEMS[0], appId: d.appId ?? '',
    ssl: d.ssl, owner: d.owner, mode: d.sync.mode, cron: d.sync.cron,
  } : blank());
  formInit.value = JSON.stringify(form);
}, { immediate: true });
watch(() => form.type, (t, old) => { if (old && form.port === DS_DEFAULT_PORT[old]) form.port = DS_DEFAULT_PORT[t]; if (old && t === '达梦 DM8' && formMode.value === 'new') { form.ssl = false; form.mode = '实时（CDC）'; form.cron = '-'; } });
watch(() => form.mode, (mode) => { if (form.type === '达梦 DM8' && mode === '定时全量' && (!form.cron || form.cron === '-')) form.cron = '0 * * * *'; });
const openForm = (m: 'new' | 'edit') => setQuery({ form: m });
const closeForm = async () => { if (formDirty.value && !(await confirmDiscard())) return; setQuery({ form: undefined }); };
const formTesting = ref(false);
async function formTest() {
  if (formTesting.value) return;
  const context = contextKey();
  const n = listSeq;
  const current = () => context === contextKey() && n === listSeq;
  if (!form.host) { message.warning('请先填写主机'); return; }
  const saved = formMode.value === 'edit' ? selected.value : undefined;
  const unchanged = !!saved && form.type === saved.type && form.host === saved.host && form.port === saved.port
    && form.database === saved.database && form.user === saved.user && form.ssl === saved.ssl
    && (form.type !== '达梦 DM8' || form.compatMode === (saved.compatMode ?? 'Oracle'))
    && (form.type !== '人大金仓 KingbaseES' || form.databaseMode === (saved.databaseMode ?? 'oracle'))
    && (form.type !== 'ERP' || (form.erpSystem === (saved.erpSystem ?? '') && form.appId === (saved.appId ?? '')));
  if (!form.password && !unchanged) { message.warning('请填写密码以测试当前连接参数'); return; }
  formTesting.value = true;
  if (real.value) {
    try {
      const t = !form.password && saved && unchanged ? await testDatasource(saved.id) : await testDatasourceParams(writeBody());
      if (!current()) return;
      if (t.ok) message.success(`连接成功：${form.host}:${form.port}，耗时 ${t.ms} ms`); else message.error(connectionFailure(t.msg));
    } catch (e) { if (current()) apiFail('测试连接', e); } finally { formTesting.value = false; }
    return;
  }
  formTesting.value = false;
  message.warning('真实连接测试未启用');
}
const writeBody = (): DatasourceWrite => ({
  name: form.name, type: form.type, host: form.host, port: form.port, database: form.database, user: form.user, password: form.password,
  compatMode: form.compatMode, databaseMode: form.databaseMode, erpSystem: form.erpSystem, appId: form.appId,
  ssl: form.ssl, owner: form.owner, mode: form.mode, cron: form.cron,
});
const saving = ref(false);
async function saveReal() {
  const context = contextKey();
  const n = listSeq;
  const current = () => context === contextKey() && n === listSeq;
  saving.value = true;
  try {
    if (formMode.value === 'edit' && selected.value) {
      const d = await updateDatasource(selected.value.id, writeBody());
      if (!current()) return;
      list.value = list.value.map((x) => (x.id === d.id ? d : x));
      detail.value = { key: context, id: d.id, item: d };
      void loadSources();
      formInit.value = JSON.stringify(form);
      setQuery({ form: undefined });
      message.success(`已保存：${d.name}`);
    } else {
      const d = await createDatasource(writeBody());
      if (!current()) return;
      list.value.unshift(d);
      void loadSources();
      formInit.value = JSON.stringify(form);
      setQuery({ form: undefined, ds: d.id });
      message.success(`已创建：${d.name}`);
    }
  } catch (e) { if (current()) apiFail('保存', e); } finally { saving.value = false; }
}
function save() {
  if (readonly.value || saving.value) return;
  if (!form.name || !form.host || !form.user || (form.type !== '达梦 DM8' && !form.database)) { message.warning('请填写名称、主机、用户名及所需的数据库'); return; }
  if (form.type === '达梦 DM8' && !['手动', '定时全量', '实时（CDC）'].includes(form.mode)) { message.warning('请选择手动、定时全量或实时（CDC）同步'); return; }
  if (formMode.value === 'new' && !form.password) { message.warning('请填写密码'); return; }
  if (real.value) { void saveReal(); return; }
  const extra = { compatMode: form.type === '达梦 DM8' ? form.compatMode : undefined, databaseMode: form.type === '人大金仓 KingbaseES' ? form.databaseMode : undefined, erpSystem: form.type === 'ERP' ? form.erpSystem : undefined, appId: form.type === 'ERP' ? form.appId : undefined };
  if (formMode.value === 'edit' && selected.value) {
    const d = selected.value;
    Object.assign(d, { name: form.name, type: form.type, host: form.host, port: form.port, database: form.database, user: form.user, ssl: form.ssl, owner: form.owner, ...extra });
    d.sync = { ...d.sync, mode: form.mode, cron: form.cron };
    formInit.value = JSON.stringify(form);
    setQuery({ form: undefined });
    message.success(`已保存：${d.name}`);
    return;
  }
  const id = `DS-${String(Math.max(...list.value.map((d) => Number(d.id.slice(3)))) + 1).padStart(3, '0')}`;
  list.value.unshift({
    id, name: form.name, type: form.type, host: form.host, port: form.port, database: form.database, user: form.user, ssl: form.ssl, owner: form.owner, ...extra,
    status: '未测试', lastTest: null, objectTypes: [], runs: [], tableCount: 0, createdAt: '2026-10-25',
    sync: { mode: form.mode, cron: form.cron, cronText: form.cron, window: '全天', cursorField: 'updated_at', nextRun: null },
  });
  formInit.value = JSON.stringify(form);
  setQuery({ form: undefined, ds: id });
  message.success(`已创建：${form.name}`);
}
const onSelect = (id: string) => guard(() => select(id));
const runColor = (r: string) => (r === '成功' ? 'tl-normal' : r === '延迟' ? 'c-1' : 'tl-failed');
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="reason">
      <a-button type="primary" :disabled="readonly" @click="openForm('new')">新建连接</a-button>
    </a-tooltip>
  </Teleport>
  <div class="cn">
    <!-- 左：数据源列表 -->
    <div class="panel cn-list side-list side-list-wide">
      <div class="panel-head">
        <span class="t-16 fw-600">数据源</span>
        <span class="t-12 c-3">{{ list.filter((d) => d.status === '异常').length }} 个异常</span>
      </div>
      <div class="cn-filter">
        <a-select v-model:value="type" size="small" class="cn-w140" :options="['全部', ...DS_TYPES].map((v) => ({ value: v, label: v === '全部' ? '全部类型' : v }))" />
        <a-input-search v-model:value="kw" size="small" placeholder="名称 / 主机 / 库名" />
      </div>
      <div class="cn-items side-list-items">
        <div
          v-for="d in listPaged.items" :key="d.id" class="cn-item side-list-item" :class="{ on: selected?.id === d.id, active: selected?.id === d.id }"
          @click="onSelect(d.id)"
        >
          <div class="cn-item-l1 side-list-row" :title="`${d.name}（${d.type}）· 负责人 ${d.owner} · 最近测试 ${d.lastTest ? d.lastTest.time : '未测试'}`">
            <span class="cn-type t-12">{{ typeShort[d.type] }}</span>
            <span class="t-14 cn-ellip side-list-name">{{ d.name }}</span>
            <a-badge :status="statusBadge(d.status)" :text="d.status" class="cn-badge" />
          </div>
        </div>
      </div>
      <div class="side-list-foot"><AppPagination v-model:page="listPage" variant="simple" :total="real ? listTotal : filtered.length" :size="LIST_SIZE" unit="个" /></div>
    </div>

    <!-- 右：详情 -->
    <div v-if="selected" class="panel cn-detail">
      <div class="cn-head">
        <div class="cn-title">
          <span class="t-20 fw-600 c-1 cn-ellip" :title="selected.name">{{ selected.name }}</span>
          <a-tag>{{ selected.type }}</a-tag>
          <a-badge :status="statusBadge(selected.status)" :text="selected.status" />
          <span v-if="selected.lastTest" class="t-14 cn-ellip" :class="selected.lastTest.ok ? 'c-2' : 'tl-failed'" :title="selected.lastTest.ok ? '' : selected.lastTest.msg">
            最近测试 <span class="num">{{ selected.lastTest.time }}</span> · {{ selected.lastTest.ok ? `成功，${selected.lastTest.ms} ms` : selected.lastTest.msg }}
          </span>
          <span v-else class="t-14 c-2">尚未测试连接</span>
          <InfoTip :text="`${selected.id} · 负责人 ${selected.owner} · 创建于 ${selected.createdAt} · 映射对象类型 ${selected.objectTypes.length ? selected.objectTypes.join('、') : EMPTY}${selected.note ? '。' + selected.note : ''}`" />
        </div>
        <a-space>
          <a-tooltip :title="readonly ? reason : !real ? '真实连接测试未启用' : ''"><a-button :disabled="readonly || !real" :loading="testing" @click="testConn(selected)">测试连接</a-button></a-tooltip>
          <a-tooltip :title="reason"><a-button :disabled="readonly" @click="openForm('edit')">编辑</a-button></a-tooltip>
          <a-tooltip :title="reason"><a-button danger :disabled="readonly" @click="remove(selected)">删除</a-button></a-tooltip>
        </a-space>
      </div>
      <a-tabs v-model:activeKey="tab" class="cn-tabs">
        <a-tab-pane key="params" tab="连接参数" />
        <a-tab-pane key="tables" tab="库表" />
        <a-tab-pane v-if="real && ['达梦 DM8', 'MySQL'].includes(selected.type)" key="sync" tab="同步计划" />
      </a-tabs>
      <a-alert v-if="selected.lastTest?.msg.includes('库表清单刷新失败')" type="warning" :message="selected.lastTest.msg" show-icon style="margin: 12px 16px" />

      <div v-if="tab === 'params'" class="cn-body">
        <a-descriptions :column="2" bordered size="small" class="cn-desc">
          <a-descriptions-item label="类型">{{ selected.type }}</a-descriptions-item>
          <a-descriptions-item :label="selected.type === 'ERP' ? '接口地址' : '主机 : 端口'"><span class="num">{{ selected.host }}:{{ selected.port }}</span></a-descriptions-item>
          <a-descriptions-item :label="selected.type === 'ERP' ? '账套 / 应用' : '数据库'"><span class="num">{{ selected.database }}</span></a-descriptions-item>
          <a-descriptions-item label="用户名"><span class="num">{{ selected.user }}</span></a-descriptions-item>
          <a-descriptions-item label="密码"><span class="num">••••••••</span><span class="t-12 c-3 cn-gap">已加密保存，不回显</span></a-descriptions-item>
          <a-descriptions-item label="SSL">{{ selected.ssl ? '启用' : '未启用' }}</a-descriptions-item>
          <a-descriptions-item v-if="selected.type === '达梦 DM8'" label="兼容模式" :span="2">{{ selected.compatMode }}</a-descriptions-item>
          <a-descriptions-item v-if="selected.type === '人大金仓 KingbaseES'" label="database_mode" :span="2">{{ selected.databaseMode }}</a-descriptions-item>
          <a-descriptions-item v-if="selected.type === 'ERP'" label="ERP 系统">{{ selected.erpSystem }}</a-descriptions-item>
          <a-descriptions-item v-if="selected.type === 'ERP'" label="应用 ID"><span class="num">{{ selected.appId }}</span></a-descriptions-item>
          <a-descriptions-item label="连接串" :span="2"><span class="num">{{ jdbc(selected) }}</span></a-descriptions-item>
          <a-descriptions-item label="连接池">{{ real ? '—' : '最小 2 · 最大 16 · 空闲 10 分钟回收' }}</a-descriptions-item>
          <a-descriptions-item label="超时">{{ real ? '—' : '连接 10 秒 · 查询 300 秒' }}</a-descriptions-item>
          <a-descriptions-item label="读写">只读（数据接入只查询，不写回源库）</a-descriptions-item>
          <a-descriptions-item label="数据同步">平台后台托管，连接页只负责连接、库表和读取授权</a-descriptions-item>
        </a-descriptions>
      </div>

      <div v-else-if="tab === 'tables'" class="cn-body cn-col">
        <a-alert v-if="mappingError" type="warning" :message="mappingError" show-icon />
        <div class="cn-tbar">
          <a-button v-if="real && selected.type === '达梦 DM8'" size="small" :disabled="readonly" :loading="schemaBusy" @click="openSchemas">选择 Schema 并更新目录</a-button>
          <a-input-search v-model:value="tableKw" size="small" placeholder="表名 / 说明" class="cn-w200" />
        </div>
        <div class="paged-scroll cn-tscroll">
          <a-table size="small" :row-key="(r: { schema: string; name: string }) => r.schema + '.' + r.name" :columns="tableCols" :data-source="tablesPaged.items" :pagination="false" :scroll="{ x: 1110 }" table-layout="fixed">
            <template #bodyCell="{ column, record }">
              <span v-if="column.key === 'name'" class="num c-1">{{ record.name }}</span>
              <span v-else-if="column.key === 'rows'" class="num">{{ record.rows < 0 ? '—' : fmtInt(record.rows) }}</span>
              <span v-else-if="column.key === 'cols'" class="num">{{ record.cols < 0 ? '—' : record.cols }}</span>
              <span v-else-if="column.key === 'objectType'" class="cn-map-name" :title="real && mappedObjects(record).length ? mappedObjects(record).map(o => o.name).join('、') : record.objectType || ''">
                <template v-if="real && mappedObjects(record).length">{{ mappedObjects(record).map(o => o.name).join('、') }}</template>
                <template v-else-if="record.objectType">{{ record.objectType }}</template>
                <a-tooltip v-else-if="record.ref" :title="`只作参考，未映射；以 ${record.ref} 为准`"><span class="c-2">参考 · 未映射</span></a-tooltip>
                <span v-else class="c-3">未映射</span>
              </span>
              <span v-else-if="column.key === 'mappingActions' && real">
                <a-tooltip :title="readonly ? reason : ''"><a-button v-if="!mappedObjects(record).length" type="link" size="small" :disabled="readonly" @click="editMapping(record)">映射对象</a-button><a-dropdown v-else :disabled="readonly" :trigger="['click']"><a-button type="link" size="small" :disabled="readonly">配置 ▾</a-button><template #overlay><a-menu><a-menu-item key="mapping" @click="editMapping(record)">编辑映射</a-menu-item><a-menu-item v-for="object in mappedObjects(record)" :key="`object:${object.objectId}`" @click="governedObject = object.objectId; governedOpen = true"><span class="cn-menu-name" :title="object.name">读取授权 · {{ object.name }}</span></a-menu-item></a-menu></template></a-dropdown></a-tooltip>
              </span>
              <span v-else-if="column.key === 'updatedAt'" class="num">{{ record.updatedAt }}</span>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="tablePage" variant="simple" :total="tableTotal" :size="TABLE_SIZE" unit="张表" class="cn-tpager" />
      </div>

      <div v-else class="cn-body cn-sync">
        <template v-if="real && ['达梦 DM8', 'MySQL'].includes(selected.type)">
          <div class="cn-mirror-head"><strong>镜像同步</strong><span class="c-3">定时调度：{{ schedulerState?.active ? '运行中' : '未运行或状态不可用' }}</span><a-button size="small" @click="loadMirrors()">刷新状态</a-button></div>
          <div v-if="!mirrorObjects.length" class="c-3">请先导入对象并配置只读列授权。</div>
          <div v-for="obj in mirrorObjects" :key="obj.objectId" class="cn-mirror-row">
            <div class="cn-mirror-summary"><strong>{{ obj.name }}</strong><div class="t-12 c-3">{{ mirrorErrors[obj.objectId] ? '状态暂不可用' : mirrorText(mirrorStates[obj.objectId]) }}<template v-if="mirrorStates[obj.objectId]"> · 已处理 {{ fmtInt(mirrorStates[obj.objectId].processed) }} 行 · 当前镜像 {{ fmtInt(mirrorStates[obj.objectId].currentStored) }} 行</template></div><div v-if="mirrorStates[obj.objectId]?.dataCheckedAt" class="t-12 c-3">源数据核验：{{ mirrorTime(mirrorStates[obj.objectId].dataCheckedAt) }}<span v-if="mirrorStates[obj.objectId]?.dataUpdatedAt"> · 镜像写入：{{ mirrorTime(mirrorStates[obj.objectId].dataUpdatedAt) }}</span></div><div v-if="mirrorNeedsReview(mirrorStates[obj.objectId])" class="t-12">连接、读取授权或源结构已变化，核对后重新同步。</div><div v-if="mirrorStates[obj.objectId]?.migrationRequired || mirrorErrors[obj.objectId] || mirrorStates[obj.objectId]?.error" class="tl-failed t-12">{{ mirrorStates[obj.objectId]?.migrationRequired ? mirrorUpgradeMessage : mirrorErrors[obj.objectId] || mirrorStates[obj.objectId].error }}</div></div>
            <div v-if="selected.type === '达梦 DM8'" class="cn-cdc-summary t-12"><strong>事务触发器 CDC</strong> · {{ cdcErrors[obj.objectId] || (cdcStates[obj.objectId] ? cdcPhase[cdcStates[obj.objectId].phase] || cdcStates[obj.objectId].phase : '状态加载中') }}<template v-if="cdcStates[obj.objectId]?.installed"> · 待处理 {{ cdcStates[obj.objectId].pendingCount ?? '未测得' }} 条 · 最近应用 {{ mirrorTime(cdcStates[obj.objectId].lastAppliedAt) }} · 采集{{ cdcStates[obj.objectId].workerActive ? '运行中' : '未运行' }}</template><div v-if="cdcStates[obj.objectId]?.error" class="tl-failed">{{ cdcStates[obj.objectId].error }}</div></div>
            <div class="cn-mirror-actions"><a-tooltip title="每批行数；提交后自动读取至本轮完成"><a-input-number v-model:value="mirrorBatch" aria-label="同步批量行数" :min="1" :max="1000" size="small" /></a-tooltip><a-button size="small" :loading="mirrorBusy === obj.objectId && !mirrorActive(mirrorStates[obj.objectId])" :disabled="readonly || !!mirrorBusy || !mirrorStates[obj.objectId] || mirrorStates[obj.objectId]?.migrationRequired || mirrorActive(mirrorStates[obj.objectId]) || cdcActive(obj.objectId)" @click="runMirror(obj.objectId)">{{ mirrorAction(mirrorStates[obj.objectId]) }}</a-button><a-tooltip v-if="mirrorActive(mirrorStates[obj.objectId])" :title="cdcActive(obj.objectId) ? '请在配置 CDC 中暂停消费' : undefined"><a-button size="small" :loading="mirrorBusy === obj.objectId" :disabled="readonly || !!mirrorBusy || cdcActive(obj.objectId)" @click="cancelMirror(obj.objectId)">{{ cdcActive(obj.objectId) ? '请先暂停 CDC' : '取消同步' }}</a-button></a-tooltip><a-button v-if="mirrorNeedsReview(mirrorStates[obj.objectId])" size="small" :disabled="readonly" @click="governedObject = obj.objectId; governedOpen = true">核对读取授权</a-button><a-button size="small" @click="showMirrorHistory(obj.objectId)">全量详情与记录</a-button><a-button v-if="['达梦 DM8', 'MySQL'].includes(selected.type)" size="small" @click="openCdc(obj.objectId)">配置 CDC</a-button></div>
          </div>
          <p class="t-12 c-3">{{ selected.type === '达梦 DM8' ? '全量同步与 CDC 可分别配置；CDC 消费运行时请先暂停，再执行同一对象的全量同步。' : 'MySQL 可配置binlog CDC；未启用CDC时使用显式镜像扫描，视图只支持镜像。' }}</p>
        </template>
        <a-descriptions :column="2" bordered size="small" class="cn-desc">
          <a-descriptions-item label="同步方式">{{ selected.sync.mode }}</a-descriptions-item>
          <a-descriptions-item v-if="real && ['达梦 DM8', 'MySQL'].includes(selected.type)" label="定时计划">{{ selected.sync.mirrorScheduleEnabled ? (schedulerState?.active ? '已启用' : '已配置，调度服务不可用') : selected.sync.mode === '实时（CDC）' ? (['达梦 DM8', 'MySQL'].includes(selected.type) ? 'CDC 按对象单独启用；视图仅支持镜像' : '该源 CDC 通道尚未实现，无法启用') : selected.sync.mode === '手动' ? '未启用' : '未启用，请重新保存配置' }}</a-descriptions-item>
          <a-descriptions-item label="频率">{{ selected.sync.cronText }}<span v-if="selected.sync.cron !== '-'" class="num c-3 cn-gap">{{ selected.sync.cron }}</span></a-descriptions-item>
          <a-descriptions-item label="运行时间窗">{{ selected.sync.window }}</a-descriptions-item>
          <a-descriptions-item label="增量字段"><span class="num">{{ selected.sync.cursorField }}</span></a-descriptions-item>
          <a-descriptions-item label="下次计划"><span class="num">{{ selected.sync.nextRun ?? EMPTY }}</span></a-descriptions-item>
          <a-descriptions-item label="失败重试" :span="real && ['达梦 DM8', 'MySQL'].includes(selected.type) ? 2 : 1">{{ real ? '—' : '3 次，间隔 5 分钟；仍失败通知负责人' }}</a-descriptions-item>
        </a-descriptions>
        <div class="t-14 fw-600 c-1 cn-runs-title">最近同步</div>
        <div v-if="!syncRuns.length" class="c-3 t-12">还没有同步记录</div>
        <div v-for="(r, k) in syncRuns" :key="k" class="cn-run">
          <span class="cn-dot" :class="runColor(r.result)" />
          <span class="num c-2 cn-run-time">{{ r.time }}</span>
          <span class="cn-run-res" :class="runColor(r.result)">{{ r.result }} <span class="c-3">{{ r.origin === 'demo' ? '· 演示记录' : r.origin === 'unverified' ? '· 来源未验证' : '' }}</span></span>
          <span class="num c-2 cn-run-n">{{ r.result === '失败' ? EMPTY : `${fmtInt(r.rows)} 行` }}</span>
          <span class="num c-3 cn-run-n">{{ r.secs }} 秒</span>
          <span class="c-3">{{ r.msg }}</span>
        </div>
        <AppPagination v-if="real" v-model:page="runPage" variant="simple" :total="runTotal" :size="10" unit="次" />
      </div>
    </div>
    <div v-else class="panel cn-detail"><a-empty description="没有数据源" class="cn-empty" /></div>
  </div>

  <ObjectMappingModal v-if="selected && mappingTable" v-model:open="mappingOpen" :datasource-id="selected.id" :datasource-name="selected.name" :schema="mappingTable.schema" :table="mappingTable.name" :current-object-id="mappedObjects(mappingTable)[0]?.objectId" @saved="loadMappings" />
  <a-modal :open="!!cdcDetail" :title="cdcCurrent?.supported ? cdcBinlog ? 'MySQL binlog CDC' : '事务触发器 CDC' : 'CDC 能力状态'" :z-index="900" :width="880" :footer="null" :closable="!cdcBusy" :mask-closable="!cdcBusy" :keyboard="!cdcBusy" :body-style="{ maxHeight: '70vh', overflowY: 'auto' }" @cancel="cdcDetail = ''">
    <p class="cn-cdc-source">{{ cdcObject?.name }} · {{ cdcDetail }}</p>
    <p v-if="cdcCurrent?.supported && !cdcBinlog" class="t-12 c-3">按键读取源当前态的 CDC 不能保证保留两次读取间的中间状态；需要完整转换历史时，请接入保存每次变化的源事件或审计日志。</p>
    <a-alert v-if="cdcErrors[cdcDetail]" type="error" :message="cdcErrors[cdcDetail]" show-icon />
    <template v-if="cdcCurrent">
      <p v-if="cdcBinlog" class="t-12">只消费已COMMIT的ROW/FULL行镜像；首次一致快照交叠回放到栅栏后发布。日志断档或DDL变化将停止并要求重建基线。</p>
      <a-form-item v-if="cdcBinlog && cdcCurrent.supported" label="此订阅独有的复制 server-id" extra="须不同于源服务器及其他复制客户端，不能复用其他订阅的server-id"><a-input-number v-model:value="mysqlServerId" :min="1" :max="4294967295" :precision="0" :disabled="!!cdcBusy || cdcCurrent.enabled" /></a-form-item>
      <p v-if="cdcCurrent.supported && selected?.sync.mode !== '实时（CDC）'" class="t-12">启用前，请先编辑数据源，将同步方式保存为“实时（CDC）”；保存不会自动安装或启用任何对象。</p>
      <a-alert v-if="cdcCurrent.supported && cdcCurrent.migrationRequired" type="warning" message="CDC 数据库尚未完成 0049 升级，请联系管理员应用迁移后刷新；无需清库或解除绑定。" show-icon />
      <a-alert v-else-if="cdcCurrent.error" type="error" :message="cdcCurrent.error" show-icon />
      <p v-if="cdcCurrent.recoveryAction" class="t-12">{{ cdcRecovery[cdcCurrent.recoveryAction] }}</p>
      <a-descriptions :column="2" size="small" class="cn-mirror-details">
        <a-descriptions-item label="状态">{{ cdcPhase[cdcCurrent.phase] || cdcCurrent.phase }}</a-descriptions-item><a-descriptions-item label="采集服务">{{ cdcCurrent.workerActive ? '运行中' : '未运行' }}</a-descriptions-item>
        <a-descriptions-item label="待处理变更">{{ cdcCurrent.pendingCount ?? '未测得' }}</a-descriptions-item><a-descriptions-item label="最近应用">{{ mirrorTime(cdcCurrent.lastAppliedAt) }}</a-descriptions-item>
        <a-descriptions-item label="最早待处理时长">{{ cdcCurrent.sourcePendingAgeSeconds == null ? '未测得' : `${Math.ceil(cdcCurrent.sourcePendingAgeSeconds)} 秒` }}</a-descriptions-item><a-descriptions-item label="最近源触发时间">{{ mirrorTime(cdcCurrent.lastSourceEventAt) }}</a-descriptions-item>
        <a-descriptions-item label="采集心跳">{{ mirrorTime(cdcCurrent.heartbeatAt) }}</a-descriptions-item><a-descriptions-item label="最近观测">{{ mirrorTime(cdcCurrent.lastObservedAt) }}</a-descriptions-item>
        <a-descriptions-item label="捕获代次">{{ cdcCurrent.sourceGeneration || EMPTY }}</a-descriptions-item><a-descriptions-item label="待确认应用">{{ cdcCurrent.pendingAckCount ?? EMPTY }}</a-descriptions-item>
      </a-descriptions>
      <p v-if="cdcCurrent.phase === 'paused'" class="t-12 c-3">{{ cdcBinlog ? '已暂停消费并保留binlog提交位置；若日志已清理，恢复时会要求重新建立基线。' : '已暂停增量消费，源端触发器仍捕获变更；恢复后继续处理积压。' }}</p>
      <p v-if="cdcCurrent.restartRequired" class="t-12">绑定或源结构已变化，请核对读取授权与源表，重新预览并确认安装后，再重新初始化。</p>
      <div v-if="cdcCurrent.supported" class="cn-cdc-actions">
        <a-button :loading="cdcBusy === 'probe'" :disabled="!!cdcBusy || cdcCurrent.migrationRequired" @click="cdcAction('probe')">检测 CDC 能力</a-button>
        <a-button v-if="cdcBinlog && cdcCurrent.installed" :disabled="!!cdcBusy || !cdcCurrent.version" @click="cdcEventMappingOpen = true">行事实事件映射</a-button>
        <a-button :loading="cdcBusy === 'plan'" :disabled="readonly || !!cdcBusy || cdcCurrent.migrationRequired || cdcProbes[cdcDetail]?.supported !== true || cdcCurrent.enabled" @click="cdcAction('plan')">{{ cdcBinlog ? '预览订阅配置' : '预览安装 SQL' }}</a-button>
        <a-button v-if="cdcCurrent.installed && !cdcCurrent.enabled && cdcCurrent.phase !== 'paused' && !cdcCurrent.restartRequired" type="primary" :disabled="readonly || !!cdcBusy || cdcCurrent.migrationRequired || selected?.sync.mode !== '实时（CDC）' || mirrorActive(mirrorStates[cdcDetail])" :loading="cdcBusy === 'enable'" @click="cdcAction('enable')">启用 CDC</a-button>
        <a-button v-if="cdcCurrent.enabled" :disabled="readonly || !!cdcBusy" :loading="cdcBusy === 'pause'" @click="cdcAction('pause')">暂停消费</a-button>
        <a-button v-if="cdcCurrent.enabled" :disabled="readonly || !!cdcBusy" :loading="cdcBusy === 'consume'" @click="cdcAction('consume')">推进当前对象</a-button>
        <a-button v-if="cdcCurrent.phase === 'paused' && !cdcCurrent.restartRequired && cdcCurrent.recoveryAction !== 'reinitialize'" type="primary" :disabled="readonly || !!cdcBusy || selected?.sync.mode !== '实时（CDC）' || mirrorActive(mirrorStates[cdcDetail])" :loading="cdcBusy === 'resume'" @click="cdcAction('resume')">恢复消费</a-button>
        <a-button v-if="cdcCurrent.installed && (cdcCurrent.restartRequired || cdcCurrent.recoveryAction === 'reinitialize') && !cdcCurrent.enabled" :disabled="readonly || !!cdcBusy || selected?.sync.mode !== '实时（CDC）' || mirrorActive(mirrorStates[cdcDetail])" :loading="cdcBusy === 'reinitialize'" @click="cdcAction('reinitialize')">重新初始化</a-button>
      </div>
      <p v-if="cdcProbes[cdcDetail]" class="cn-cdc-source">{{ cdcProbes[cdcDetail].message || cdcProbes[cdcDetail].error || cdcProbes[cdcDetail].details?.error || cdcProbes[cdcDetail].details?.message || (cdcProbes[cdcDetail].supported ? '当前对象支持事务触发器 CDC' : '当前对象不支持 CDC') }}<template v-if="cdcProbes[cdcDetail].requiredPrivileges?.length"> · 所需权限：{{ cdcProbes[cdcDetail].requiredPrivileges?.join('、') }}</template></p>
      <template v-if="cdcCurrent.supported && cdcPlan">
        <p class="cn-cdc-source">源表：{{ cdcPlan.capture.schema }}.{{ cdcPlan.capture.table }}。将创建下列 SQL 中的捕获资源，请核对后安装。</p>
        <p v-if="cdcBinlog" class="t-12">不执行源DDL；确认后可启用此授权表的binlog订阅。</p><pre v-else class="cn-cdc-sql">{{ cdcSql }}</pre>
        <template v-if="cdcPlan.bootstrapFenceSql"><p class="t-12">安装后事务栅栏：请求源表排他锁，成功后 COMMIT 释放锁；存在未结束事务或缺少 LOCK 权限时，须处理后重试同一安装计划。</p><pre class="cn-cdc-sql">{{ cdcFenceSql }}</pre></template>
        <div class="cn-cdc-actions"><a-button v-if="!cdcBinlog" @click="downloadCdcSql">下载 SQL</a-button><a-button type="primary" :disabled="readonly || !!cdcBusy || cdcCurrent.enabled || cdcCurrent.version !== cdcPlanVersion" :loading="cdcBusy === 'install'" @click="cdcAction('install')">{{ cdcBinlog ? '确认订阅配置' : '确认安装源捕获' }}</a-button></div>
      </template>
    </template>
  </a-modal>
  <CdcEventMappingModal v-if="cdcDetail && cdcCurrent?.version" v-model:open="cdcEventMappingOpen" :object-id="cdcDetail" :version="cdcCurrent.version" :paused="!cdcCurrent.enabled" />
  <a-modal :open="!!mirrorHistory" title="数据同步运行记录" :footer="null" :width="760" @cancel="mirrorHistory = ''">
    <a-descriptions v-if="mirrorHistoryState" :column="2" size="small" class="cn-mirror-details">
      <a-descriptions-item label="当前状态">{{ mirrorText(mirrorHistoryState) }}</a-descriptions-item><a-descriptions-item label="已处理 / 当前镜像">{{ fmtInt(mirrorHistoryState.processed) }} / {{ fmtInt(mirrorHistoryState.currentStored) }} 行</a-descriptions-item>
      <a-descriptions-item label="源数据核验">{{ mirrorTime(mirrorHistoryState.dataCheckedAt) }}</a-descriptions-item><a-descriptions-item label="镜像数据写入">{{ mirrorTime(mirrorHistoryState.dataUpdatedAt) }}</a-descriptions-item>
      <a-descriptions-item label="状态更新时间">{{ mirrorTime(mirrorHistoryState.updatedAt) }}</a-descriptions-item><a-descriptions-item label="最近任务结束">{{ mirrorTime(mirrorHistoryState.lastFinishedAt) }}</a-descriptions-item>
      <a-descriptions-item label="删除核对">{{ mirrorHistoryState.complete && mirrorHistoryState.deletionsAligned === true ? '已完成' : '待完整同步' }}</a-descriptions-item><a-descriptions-item label="完整扫描发布">{{ mirrorTime(mirrorHistoryState.dataPublishedAt) }}</a-descriptions-item>
      <a-descriptions-item label="任务 / 每批行数">{{ mirrorHistoryState.runId ?? EMPTY }} / {{ mirrorHistoryState.batchSize ?? EMPTY }}</a-descriptions-item><a-descriptions-item label="含旧配置保留行">{{ fmtInt(mirrorHistoryState.totalStored) }} 行</a-descriptions-item>
      <a-descriptions-item label="源配置版本" :span="2"><span class="num">{{ mirrorHistoryState.sourceFingerprint ?? EMPTY }}</span></a-descriptions-item><a-descriptions-item label="源结构版本" :span="2"><span class="num">{{ mirrorHistoryState.structureFingerprint ?? EMPTY }}</span></a-descriptions-item>
    </a-descriptions>
    <a-table size="small" :loading="mirrorHistoryBusy" row-key="id" :data-source="mirrorHistoryRows" :pagination="false" :columns="[{title:'来源',key:'trigger',width:80},{title:'状态',dataIndex:'status',key:'status',width:100},{title:'开始',dataIndex:'startedAt',key:'startedAt',width:170,ellipsis:true},{title:'读取 / 写入',key:'counts',width:110},{title:'结果',key:'result'}]">
      <template #bodyCell="{column,record}"><span v-if="column.key === 'trigger'">{{ record.trigger === 'cdc_snapshot' ? 'CDC 初始同步' : record.trigger === 'scheduled' ? '定时' : '手动' }}</span><span v-else-if="column.key === 'status'">{{ record.status === 'succeeded' && !record.complete ? '未完成' : runLabels[record.status] || record.status }}</span><span v-else-if="column.key === 'startedAt'" class="num" :title="record.startedAt || undefined">{{ mirrorTime(record.startedAt) }}</span><span v-else-if="column.key === 'counts'">{{ record.readCount }} / {{ record.writeCount }}</span><span v-else-if="column.key === 'result'">{{ record.error || (record.complete ? '本轮扫描完成' : '尚未完整同步') }}</span></template>
    </a-table><AppPagination v-model:page="mirrorHistoryPage" variant="simple" :total="mirrorHistoryTotal" :size="10" unit="次" />
  </a-modal>
  <a-modal v-model:open="schemaOpen" title="选择要更新的 Schema" ok-text="更新目录" :confirm-loading="schemaBusy" :ok-button-props="{disabled:!chosenSchemas.length || chosenSchemas.length > 32}" @ok="refreshSchemas">
    <p class="t-12 c-3">只读取所选 Schema 的表目录；字段和主键在选择表导入时按需读取，不复制源数据。</p>
    <a-select v-model:value="chosenSchemas" mode="multiple" show-search :options="schemas.map(s => ({value:s,label:s}))" style="width:100%" placeholder="选择 1 至 32 个 Schema" />
    <p v-if="!schemas.length" class="t-12 c-3">此连接没有可访问的 Schema。</p>
  </a-modal>
  <ExternalReadBindingModal v-if="governedObject && ['达梦 DM8', 'MySQL'].includes(selected?.type ?? '')" v-model:open="governedOpen" :object-id="governedObject" @saved="loadMirrors()" />
  <GovernedBindingModal v-else-if="governedObject" v-model:open="governedOpen" :object-id="governedObject" />
  <a-drawer
    :open="!!formMode" :width="560" :title="formMode === 'edit' ? `编辑连接 · ${selected?.name ?? ''}` : '新建数据连接'"
    :mask-closable="true" @close="closeForm"
  >
    <a-form layout="vertical" :model="form" class="cn-form">
      <a-form-item label="名称" required><a-input v-model:value="form.name" placeholder="例如：POS 交易库（华东）" /></a-form-item>
      <div class="cn-form-row">
        <a-form-item label="类型" required><a-select v-model:value="form.type" :options="DS_TYPES.map((v) => ({ value: v, label: v }))" /></a-form-item>
        <a-form-item v-if="form.type === '达梦 DM8'" label="兼容模式（COMPATIBLE_MODE）">
          <a-select v-model:value="form.compatMode" :options="DM_COMPAT.map((v) => ({ value: v, label: v }))" />
          <div class="t-12 c-3">仅记录数据库已有的兼容模式，不会修改数据库配置。</div>
        </a-form-item>
        <a-form-item v-else-if="form.type === '人大金仓 KingbaseES'" label="database_mode">
          <a-select v-model:value="form.databaseMode" :options="KB_MODES.map((v) => ({ value: v, label: v }))" />
        </a-form-item>
        <a-form-item v-else-if="form.type === 'ERP'" label="ERP 系统">
          <a-select v-model:value="form.erpSystem" :options="ERP_SYSTEMS.map((v) => ({ value: v, label: v }))" />
        </a-form-item>
        <a-form-item v-else label="SSL"><a-switch v-model:checked="form.ssl" /></a-form-item>
      </div>
      <a-form-item v-if="form.type === '达梦 DM8'" label="SSL" extra="当前尚不支持验证达梦 TLS；开启时连接测试将被阻止。"><a-switch v-model:checked="form.ssl" /></a-form-item>
      <div class="cn-form-row">
        <a-form-item :label="form.type === 'ERP' ? '接口域名' : '主机'" required><a-input v-model:value="form.host" placeholder="10.12.8.21" /></a-form-item>
        <a-form-item label="端口" required><a-input-number v-model:value="form.port" :min="1" :max="65535" class="cn-full" /></a-form-item>
      </div>
      <div class="cn-form-row">
        <a-form-item :label="form.type === 'ERP' ? '账套 / 应用' : form.type === '达梦 DM8' ? '限定 Schema（可选）' : '数据库'" :required="form.type !== '达梦 DM8'" :extra="form.type === '达梦 DM8' ? '填写后只使用该 Schema，名称大小写须与源端一致；留空时在库表页面选择。' : undefined"><a-input v-model:value="form.database" /></a-form-item>
        <a-form-item v-if="form.type === 'ERP'" label="应用 ID"><a-input v-model:value="form.appId" /></a-form-item>
        <a-form-item v-else label="负责人"><a-input v-model:value="form.owner" /></a-form-item>
      </div>
      <div class="cn-form-row">
        <a-form-item label="用户名" required><a-input v-model:value="form.user" /></a-form-item>
        <a-form-item label="密码" :required="formMode === 'new'">
          <a-input-password v-model:value="form.password" :placeholder="formMode === 'edit' ? '不修改请留空' : ''" autocomplete="new-password" />
        </a-form-item>
      </div>
      <p class="t-12 c-3">数据同步由平台后台统一托管，连接页不再要求配置同步方式或频率。保存后密码加密保存，不再显示。</p>
    </a-form>
    <template #footer>
      <div class="cn-foot">
        <a-tooltip :title="!real ? '真实连接测试未启用' : ''"><a-button :disabled="!real || readonly" :loading="formTesting" @click="formTest">测试连接</a-button></a-tooltip>
        <a-space>
          <span v-if="formDirty" class="t-12 c-2">有未保存的修改</span>
          <a-button @click="closeForm">取消</a-button>
          <a-button type="primary" :loading="saving" :disabled="readonly" @click="save">保存</a-button>
        </a-space>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped>
.cn-cdc-summary { flex: 1 0 100%; min-width: 0; overflow-wrap: anywhere; color: var(--g8); }
.cn-cdc-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.cn-cdc-source { overflow-wrap: anywhere; }
.cn-cdc-sql { max-height: 300px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; padding: 12px; background: var(--g2); border: var(--border); border-radius: var(--radius); font-size: 12px; }
.cn-map-name { display: block; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cn-menu-name { display: block; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cn { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.cn-list { flex: none; }
.cn-filter { display: flex; gap: 8px; padding: 10px 16px; border-bottom: var(--border); }
.cn-w140 { width: 140px; flex: none; }
.cn-w200 { width: 200px; }
.cn-items { flex: 1; min-height: 0; }
.cn-item-l1 { display: flex; align-items: center; gap: 8px; min-width: 0; }
.cn-type { flex: none; min-width: 42px; text-align: center; border: var(--border); border-radius: 4px; color: var(--g8); line-height: 18px; }
.cn-ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.cn-badge { margin-left: auto; flex: none; }
.cn-item :deep(.ant-badge-status-text) { color: var(--g9); font-size: 12px; line-height: 18px; font-weight: 400; }
.cn-detail { flex: 1; min-width: 0; }
.cn-head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px; padding: 14px 20px 6px; }
.cn-title { display: flex; flex: 1 1 480px; flex-wrap: wrap; align-items: center; gap: 10px; min-width: 0; }
.cn-title > .cn-ellip { max-width: 100%; }
.cn-meta { display: flex; flex-wrap: wrap; gap: 6px 20px; padding: 0 20px; }
.cn-note { margin: 8px 20px 0; padding: 6px 12px; background: var(--g2); border-radius: var(--radius); }
.cn-tabs { padding: 0 20px; flex: none; }
.cn-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.cn-body { flex: 1; min-height: 0; padding: 16px 20px; }
.cn-sync { overflow-y: auto; }
.cn-mirror-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin-bottom: 8px; }
.cn-mirror-head > strong { margin-right: auto; }
.cn-mirror-row { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 24px; padding: 12px 0; border-bottom: var(--border); }
.cn-mirror-summary { flex: 1 1 320px; min-width: 0; overflow-wrap: anywhere; }
.cn-mirror-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; max-width: 100%; }
.cn-mirror-actions :deep(.ant-input-number) { width: 80px; }
.cn-mirror-details :deep(.ant-descriptions-view > table) { width: 100%; table-layout: fixed; }
.cn-mirror-details :deep(.ant-descriptions-item-content) { overflow-wrap: anywhere; }
.cn-col { display: flex; flex-direction: column; padding-bottom: 6px; }
.cn-tbar { display: flex; flex-wrap: wrap; justify-content: flex-end; align-items: center; gap: 8px; margin-bottom: 8px; }
.cn-tscroll { flex: 1; min-height: 0; min-width: 0; }
.cn-tscroll :deep(.ant-table-content > table) { min-width: 1110px !important; }
/* Keep the table pager outside the fixed build-assistant launcher's 118px + edge/gap area. */
.cn-tpager { padding: 6px 160px 0 0; }
.cn-desc :deep(.ant-descriptions-item-label) { width: 140px; }
.cn-desc :deep(.ant-descriptions-view > table) { width: 100%; table-layout: fixed; }
.cn-desc :deep(.ant-descriptions-item-content) { overflow-wrap: anywhere; }
.cn-gap { margin-left: 8px; }
.cn-runs-title { margin: 16px 0 6px; }
.cn-run { display: flex; align-items: center; gap: 16px; height: 30px; border-bottom: 1px dashed var(--g4); }
.cn-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; }
.cn-run-time { width: 130px; }
.cn-run-res { width: 40px; }
.cn-run-n { width: 90px; text-align: right; }
.cn-empty { margin-top: 120px; }
.cn-form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
.cn-full { width: 100%; }
.cn-foot { display: flex; justify-content: space-between; align-items: center; }
</style>
