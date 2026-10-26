<script setup lang="ts">
/**
 * 数据连接：左侧数据源列表（simple 分页），右侧详情（连接参数 / 库表 / 同步计划）。
 * URL：?ds=DS-001&tab=params|tables|sync&form=new|edit，刷新可还原。
 * 新建 / 编辑在抽屉表单里（达梦显示兼容模式，金仓显示 database_mode，ERP 显示系统和应用 ID）；删除走 ConfirmActionModal。
 * 历史版本只读（useReadonly）；表单有未保存修改时，关闭抽屉或离开页面要确认（useUnsavedGuard）。
 * 开关 data 切到真接口时只显示数据库结果；失败保留真实模式并提示，不切演示分支。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import InfoTip from '@/components/InfoTip.vue';
import ObjectMappingModal from '@/pages/ontology/parts/ObjectMappingModal.vue';
import { fetchDatasourceMappings, type DatasourceMapping } from '@/api/objectImport';
import { useConfirmStore } from '@/stores/confirm';
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
  testDatasource, testDatasourceParams, type DatasourceWrite,
} from '@/api/datasources';

const route = useRoute();
const router = useRouter();
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
const tab = computed({ get: () => q('tab') || 'params', set: (v: string) => setQuery({ tab: v === 'params' ? undefined : v }) });
const select = (id: string) => setQuery({ ds: id });

const statusBadge = (s: DsStatus) => (s === '异常' ? 'error' : 'default');
const typeShort: Record<DsType, string> = { PostgreSQL: 'PG', '达梦 DM8': 'DM8', '人大金仓 KingbaseES': 'KES', MySQL: 'MySQL', ERP: 'ERP' };

// ---------- 详情：连接参数 ----------
const modeText = (d: Datasource) =>
  d.type === '达梦 DM8' ? `兼容模式：${d.compatMode}` : d.type === '人大金仓 KingbaseES' ? `database_mode：${d.databaseMode}` : d.type === 'ERP' ? d.erpSystem ?? '' : '';
const jdbc = (d: Datasource) => ({
  PostgreSQL: `jdbc:postgresql://${d.host}:${d.port}/${d.database}`,
  '达梦 DM8': `jdbc:dm://${d.host}:${d.port}?schema=${d.database}&compatibleMode=${(d.compatMode ?? '').toLowerCase()}`,
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
const mappingCr = ref('');
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
watch([() => selected.value?.id, contextKey], async ([id]) => {
  const n = ++mappingSeq, key = contextKey(); mappings.value = []; mappingError.value = ''; mappingOpen.value = false; mappingCr.value = '';
  if (!real.value || !id) return;
  try { const rows = await fetchDatasourceMappings(id); if (n === mappingSeq && key === contextKey()) mappings.value = rows; }
  catch (e) { if (n === mappingSeq && key === contextKey()) mappingError.value = `映射加载失败：${e instanceof Error ? e.message : String(e)}`; }
}, { immediate: true });
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
  { title: '映射对象类型', key: 'objectType', width: 220 },
  { title: '最近更新', key: 'updatedAt', width: 150 },
];

// ---------- 测试连接 ----------
const testing = ref(false);
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
      if (t.ok) {
        message.success(`连接成功，耗时 ${t.ms} ms`);
        // 后台真连成功后重新读取持久库表清单。
        if (selected.value?.id === d.id) await loadTables();
      } else message.error(`连接失败：${t.msg}`);
    } catch (e) { if (current()) apiFail('测试连接', e); } finally { testing.value = false; }
    return;
  }
  await new Promise((r) => setTimeout(r, 600));
  testing.value = false;
  const fail = d.status === '异常';
  d.lastTest = fail
    ? { time: '2026-10-25 16:30', ok: false, ms: 10000, msg: d.lastTest?.msg ?? '连接超时（10 秒）' }
    : { time: '2026-10-25 16:30', ok: true, ms: 42, msg: '连接成功' };
  if (!fail) d.status = '正常';
  if (fail) message.error(`连接失败：${d.lastTest.msg}`); else message.success(`连接成功，耗时 42 ms`);
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
const blank = (): Form => ({ name: '', type: 'PostgreSQL', host: '', port: 5432, database: '', user: '', password: '', compatMode: 'Oracle', databaseMode: 'oracle', erpSystem: ERP_SYSTEMS[0], appId: '', ssl: true, owner: '王敏', mode: '定时增量', cron: '0 * * * *' });
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
watch(() => form.type, (t, old) => { if (old && form.port === DS_DEFAULT_PORT[old]) form.port = DS_DEFAULT_PORT[t]; });
const openForm = (m: 'new' | 'edit') => setQuery({ form: m });
const closeForm = async () => { if (formDirty.value && !(await confirmDiscard())) return; setQuery({ form: undefined }); };
const formTesting = ref(false);
async function formTest() {
  if (formTesting.value) return;
  const context = contextKey();
  const n = listSeq;
  const current = () => context === contextKey() && n === listSeq;
  if (!form.host) { message.warning('请先填写主机'); return; }
  formTesting.value = true;
  if (real.value) {
    try {
      const t = await testDatasourceParams(writeBody());
      if (!current()) return;
      if (t.ok) message.success(`连接成功：${form.host}:${form.port}，耗时 ${t.ms} ms`); else message.error(`连接失败：${t.msg}`);
    } catch (e) { if (current()) apiFail('测试连接', e); } finally { formTesting.value = false; }
    return;
  }
  await new Promise((r) => setTimeout(r, 600));
  formTesting.value = false;
  message.success(`连接成功：${form.host}:${form.port}，耗时 37 ms`);
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
  if (!form.name || !form.host || !form.database || !form.user) { message.warning('请填写名称、主机、数据库和用户名'); return; }
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
    <div class="panel cn-list">
      <div class="panel-head">
        <span class="t-16 fw-600">数据源</span>
        <span class="t-12 c-3">{{ list.filter((d) => d.status === '异常').length }} 个异常</span>
      </div>
      <div class="cn-filter">
        <a-select v-model:value="type" size="small" class="cn-w140" :options="['全部', ...DS_TYPES].map((v) => ({ value: v, label: v === '全部' ? '全部类型' : v }))" />
        <a-input-search v-model:value="kw" size="small" placeholder="名称 / 主机 / 库名" />
      </div>
      <div class="cn-items">
        <div
          v-for="d in listPaged.items" :key="d.id" class="cn-item" :class="{ on: selected?.id === d.id }"
          @click="onSelect(d.id)"
        >
          <div class="cn-item-l1" :title="`${d.name}（${d.type}${modeText(d) ? ' · ' + modeText(d) : ''}）· 负责人 ${d.owner} · 最近测试 ${d.lastTest ? d.lastTest.time : '未测试'}`">
            <span class="cn-type t-12">{{ typeShort[d.type] }}</span>
            <span class="t-14 fw-600 c-1 cn-ellip">{{ d.name }}</span>
            <a-badge :status="statusBadge(d.status)" :text="d.status" class="cn-badge" />
          </div>
        </div>
      </div>
      <AppPagination v-model:page="listPage" class="cn-pager" variant="simple" :total="real ? listTotal : filtered.length" :size="LIST_SIZE" unit="个" />
    </div>

    <!-- 右：详情 -->
    <div v-if="selected" class="panel cn-detail">
      <div class="cn-head">
        <div class="cn-title">
          <span class="t-20 fw-600 c-1">{{ selected.name }}</span>
          <a-tag>{{ selected.type }}</a-tag>
          <a-tag v-if="modeText(selected)">{{ modeText(selected) }}</a-tag>
          <a-badge :status="statusBadge(selected.status)" :text="selected.status" />
          <span v-if="selected.lastTest" class="t-14 cn-ellip" :class="selected.lastTest.ok ? 'c-2' : 'tl-failed'" :title="selected.lastTest.ok ? '' : selected.lastTest.msg">
            最近测试 <span class="num">{{ selected.lastTest.time }}</span> · {{ selected.lastTest.ok ? `成功，${selected.lastTest.ms} ms` : selected.lastTest.msg }}
          </span>
          <span v-else class="t-14 c-2">尚未测试连接</span>
          <InfoTip :text="`${selected.id} · 负责人 ${selected.owner} · 创建于 ${selected.createdAt} · 映射对象类型 ${selected.objectTypes.length ? selected.objectTypes.join('、') : EMPTY}${selected.note ? '。' + selected.note : ''}`" />
        </div>
        <a-space>
          <a-tooltip :title="reason"><a-button :disabled="readonly" :loading="testing" @click="testConn(selected)">测试连接</a-button></a-tooltip>
          <a-tooltip :title="reason"><a-button :disabled="readonly" @click="openForm('edit')">编辑</a-button></a-tooltip>
          <a-tooltip :title="reason"><a-button danger :disabled="readonly" @click="remove(selected)">删除</a-button></a-tooltip>
        </a-space>
      </div>
      <a-tabs v-model:activeKey="tab" class="cn-tabs">
        <a-tab-pane key="params" tab="连接参数" />
        <a-tab-pane key="tables" tab="库表" />
        <a-tab-pane key="sync" tab="同步计划" />
      </a-tabs>

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
          <a-descriptions-item label="网络">{{ real ? '—' : '内网直连 · 白名单 10.12.40.0/24' }}</a-descriptions-item>
        </a-descriptions>
      </div>

      <div v-else-if="tab === 'tables'" class="cn-body cn-col">
        <a-alert v-if="mappingError" type="warning" :message="mappingError" show-icon />
        <a-alert v-if="mappingCr" type="info" :message="`映射改动在 ${mappingCr} 中，审核发布前此处仍显示已发布映射`" show-icon />
        <div class="cn-tbar">
          <span class="t-12 c-3">已映射到对象类型的表排在前面</span>
          <a-input-search v-model:value="tableKw" size="small" placeholder="表名 / 说明" class="cn-w200" />
        </div>
        <div class="paged-scroll cn-tscroll">
          <a-table size="small" :row-key="(r: { schema: string; name: string }) => r.schema + '.' + r.name" :columns="tableCols" :data-source="tablesPaged.items" :pagination="false">
            <template #bodyCell="{ column, record }">
              <span v-if="column.key === 'name'" class="num c-1">{{ record.name }}</span>
              <span v-else-if="column.key === 'rows'" class="num">{{ fmtInt(record.rows) }}</span>
              <span v-else-if="column.key === 'objectType'">
                <template v-if="real && mappedObjects(record).length"><a-tag v-for="object in mappedObjects(record)" :key="object.objectId">{{ object.name }}</a-tag></template>
                <a-tag v-else-if="record.objectType">{{ record.objectType }}</a-tag>
                <a-tooltip v-else-if="record.ref" :title="`只作参考，未映射；以 ${record.ref} 为准`"><span class="c-2">参考 · 未映射</span></a-tooltip>
                <span v-else class="c-3">未映射</span>
                <a-tooltip v-if="real" :title="readonly ? reason : '修改对象定义来源，发布后生效'"><a-button type="link" size="small" :disabled="readonly" @click="editMapping(record)">编辑映射</a-button></a-tooltip>
              </span>
              <span v-else-if="column.key === 'updatedAt'" class="num">{{ record.updatedAt }}</span>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="tablePage" variant="simple" :total="tableTotal" :size="TABLE_SIZE" unit="张表" class="cn-tpager" />
      </div>

      <div v-else class="cn-body cn-sync">
        <a-descriptions :column="2" bordered size="small" class="cn-desc">
          <a-descriptions-item label="同步方式">{{ selected.sync.mode }}</a-descriptions-item>
          <a-descriptions-item label="频率">{{ selected.sync.cronText }}<span v-if="selected.sync.cron !== '-'" class="num c-3 cn-gap">{{ selected.sync.cron }}</span></a-descriptions-item>
          <a-descriptions-item label="运行时间窗">{{ selected.sync.window }}</a-descriptions-item>
          <a-descriptions-item label="增量字段"><span class="num">{{ selected.sync.cursorField }}</span></a-descriptions-item>
          <a-descriptions-item label="下次运行"><span class="num">{{ selected.sync.nextRun ?? EMPTY }}</span></a-descriptions-item>
          <a-descriptions-item label="失败重试">{{ real ? '—' : '3 次，间隔 5 分钟；仍失败通知负责人' }}</a-descriptions-item>
        </a-descriptions>
        <div class="t-14 fw-600 c-1 cn-runs-title">最近同步</div>
        <div v-if="!syncRuns.length" class="c-3 t-12">还没有同步记录</div>
        <div v-for="(r, k) in syncRuns" :key="k" class="cn-run">
          <span class="cn-dot" :class="runColor(r.result)" />
          <span class="num c-2 cn-run-time">{{ r.time }}</span>
          <span class="cn-run-res" :class="runColor(r.result)">{{ r.result }}</span>
          <span class="num c-2 cn-run-n">{{ r.result === '失败' ? EMPTY : `${fmtInt(r.rows)} 行` }}</span>
          <span class="num c-3 cn-run-n">{{ r.secs }} 秒</span>
          <span class="c-3">{{ r.msg }}</span>
        </div>
        <AppPagination v-if="real" v-model:page="runPage" variant="simple" :total="runTotal" :size="10" unit="次" />
      </div>
    </div>
    <div v-else class="panel cn-detail"><a-empty description="没有数据源" class="cn-empty" /></div>
  </div>

  <ObjectMappingModal v-if="selected && mappingTable" v-model:open="mappingOpen" :datasource-id="selected.id" :datasource-name="selected.name" :schema="mappingTable.schema" :table="mappingTable.name" :current-object-id="mappedObjects(mappingTable)[0]?.objectId" @saved="(id) => mappingCr = id" />
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
        </a-form-item>
        <a-form-item v-else-if="form.type === '人大金仓 KingbaseES'" label="database_mode">
          <a-select v-model:value="form.databaseMode" :options="KB_MODES.map((v) => ({ value: v, label: v }))" />
        </a-form-item>
        <a-form-item v-else-if="form.type === 'ERP'" label="ERP 系统">
          <a-select v-model:value="form.erpSystem" :options="ERP_SYSTEMS.map((v) => ({ value: v, label: v }))" />
        </a-form-item>
        <a-form-item v-else label="SSL"><a-switch v-model:checked="form.ssl" /></a-form-item>
      </div>
      <div class="cn-form-row">
        <a-form-item :label="form.type === 'ERP' ? '接口域名' : '主机'" required><a-input v-model:value="form.host" placeholder="10.12.8.21" /></a-form-item>
        <a-form-item label="端口" required><a-input-number v-model:value="form.port" :min="1" :max="65535" class="cn-full" /></a-form-item>
      </div>
      <div class="cn-form-row">
        <a-form-item :label="form.type === 'ERP' ? '账套 / 应用' : '数据库'" required><a-input v-model:value="form.database" /></a-form-item>
        <a-form-item v-if="form.type === 'ERP'" label="应用 ID"><a-input v-model:value="form.appId" /></a-form-item>
        <a-form-item v-else label="负责人"><a-input v-model:value="form.owner" /></a-form-item>
      </div>
      <div class="cn-form-row">
        <a-form-item label="用户名" required><a-input v-model:value="form.user" /></a-form-item>
        <a-form-item label="密码" :required="formMode === 'new'">
          <a-input-password v-model:value="form.password" :placeholder="formMode === 'edit' ? '不修改请留空' : ''" autocomplete="new-password" />
        </a-form-item>
      </div>
      <div class="cn-form-row">
        <a-form-item label="同步方式">
          <a-select v-model:value="form.mode" :options="['定时增量', '定时全量', '实时（CDC）', '手动'].map((v) => ({ value: v, label: v }))" />
        </a-form-item>
        <a-form-item label="频率（cron）"><a-input v-model:value="form.cron" class="num" :disabled="form.mode === '手动' || form.mode === '实时（CDC）'" /></a-form-item>
      </div>
      <div class="t-12 c-3">只需要只读账号。密码加密保存，保存后不再显示。</div>
    </a-form>
    <template #footer>
      <div class="cn-foot">
        <a-button :loading="formTesting" @click="formTest">测试连接</a-button>
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
.cn { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.cn-list { width: 380px; flex: none; }
.cn-filter { display: flex; gap: 8px; padding: 10px 16px; border-bottom: var(--border); }
.cn-w140 { width: 140px; flex: none; }
.cn-w200 { width: 200px; }
.cn-items { flex: 1; min-height: 0; overflow: hidden; }
.cn-item { padding: 9px 16px; border-bottom: var(--border); cursor: pointer; border-left: 3px solid transparent; }
.cn-item:hover { background: var(--g2); }
.cn-item.on { background: var(--brand-bg); border-left-color: var(--brand); }
.cn-item-l1 { display: flex; align-items: center; gap: 8px; min-width: 0; }
.cn-type { flex: none; min-width: 42px; text-align: center; border: var(--border); border-radius: 4px; color: var(--g8); line-height: 18px; }
.cn-ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.cn-badge { margin-left: auto; flex: none; }
.cn-pager { padding: 6px 16px; }
.cn-detail { flex: 1; min-width: 0; }
.cn-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px 6px; }
.cn-title { display: flex; align-items: center; gap: 10px; min-width: 0; }
.cn-meta { display: flex; flex-wrap: wrap; gap: 6px 20px; padding: 0 20px; }
.cn-note { margin: 8px 20px 0; padding: 6px 12px; background: var(--g2); border-radius: var(--radius); }
.cn-tabs { padding: 0 20px; flex: none; }
.cn-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.cn-body { flex: 1; min-height: 0; padding: 16px 20px; }
.cn-col { display: flex; flex-direction: column; padding-bottom: 6px; }
.cn-tbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.cn-tscroll { flex: 1; min-height: 0; }
.cn-tpager { padding-top: 6px; }
.cn-desc :deep(.ant-descriptions-item-label) { width: 140px; }
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
