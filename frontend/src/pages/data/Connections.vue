<script setup lang="ts">
/**
 * 数据连接：左侧数据源列表（simple 分页），右侧详情（连接参数 / 库表 / 同步计划）。
 * URL：?ds=DS-001&tab=params|tables|sync&form=new|edit，刷新可还原。
 * 新建 / 编辑在抽屉表单里（达梦显示兼容模式，金仓显示 database_mode，ERP 显示系统和应用 ID）；删除走 ConfirmActionModal。
 * 历史版本只读（useReadonly）；表单有未保存修改时，关闭抽屉或离开页面要确认（useUnsavedGuard）。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { useUnsavedGuard, confirmDiscard } from '@/composables/useUnsavedGuard';
import { paginate, fmtInt, EMPTY } from '@/mock';
import {
  DATASOURCES, DS_TYPES, DS_DEFAULT_PORT, DM_COMPAT, KB_MODES, ERP_SYSTEMS, dsTables,
  type Datasource, type DsType, type DsStatus,
} from '@/mock/datasources';

const route = useRoute();
const router = useRouter();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) =>
  router.replace({ query: { ...route.query, ...patch } });

const { readonly, reason } = useReadonly();
const list = ref<Datasource[]>(DATASOURCES.map((d) => ({ ...d })));

// ---------- 左侧列表 ----------
const LIST_SIZE = 12;
const kw = ref('');
const type = ref<'全部' | DsType>('全部');
const listPage = ref(1);
watch([kw, type], () => { listPage.value = 1; });
const filtered = computed(() => list.value.filter((d) =>
  (type.value === '全部' || d.type === type.value) &&
  (!kw.value || d.name.includes(kw.value) || d.host.includes(kw.value) || d.database.toLowerCase().includes(kw.value.toLowerCase()))));
const listPaged = computed(() => paginate(filtered.value, { page: listPage.value, size: LIST_SIZE }));

const selected = computed(() => list.value.find((d) => d.id === q('ds')) ?? filtered.value[0] ?? null);
// 选中项不在当前页时，翻到它所在的页
watch(() => q('ds'), (id) => {
  const i = filtered.value.findIndex((d) => d.id === id);
  if (i >= 0) listPage.value = Math.floor(i / LIST_SIZE) + 1;
}, { immediate: true });
const tab = computed({ get: () => q('tab') || 'params', set: (v: string) => setQuery({ tab: v === 'params' ? undefined : v }) });
const select = (id: string) => setQuery({ ds: id });

const statusBadge = (s: DsStatus) => (s === '正常' ? 'success' : s === '异常' ? 'error' : 'default');
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
const TABLE_SIZE = 12;
const tablePage = ref(1);
const tableKw = ref('');
watch([() => selected.value?.id, tableKw], () => { tablePage.value = 1; });
const tables = computed(() => (selected.value ? dsTables(selected.value) : []).filter((t) =>
  !tableKw.value || t.name.toLowerCase().includes(tableKw.value.toLowerCase()) || t.comment.includes(tableKw.value)));
const tablesPaged = computed(() => paginate(tables.value, { page: tablePage.value, size: TABLE_SIZE }));
const tableCols = [
  { title: 'Schema', dataIndex: 'schema', key: 'schema', width: 110 },
  { title: '表名', dataIndex: 'name', key: 'name', width: 200, ellipsis: true },
  { title: '说明', dataIndex: 'comment', key: 'comment', width: 140, ellipsis: true },
  { title: '行数', key: 'rows', width: 120, align: 'right' as const },
  { title: '字段数', dataIndex: 'cols', key: 'cols', width: 80, align: 'right' as const },
  { title: '映射对象类型', key: 'objectType', width: 130 },
  { title: '最近更新', key: 'updatedAt', width: 150 },
];

// ---------- 测试连接 ----------
const testing = ref(false);
async function testConn(d: Datasource) {
  testing.value = true;
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
  const ok = await confirm.ask({
    title: '删除数据连接', target: `${d.name}（${d.id}）`, danger: true, okText: '删除', irreversible: true,
    fields: [
      { label: '类型', value: d.type },
      { label: '地址', value: `${d.host}:${d.port} / ${d.database}` },
      { label: '映射的对象类型', value: d.objectTypes.length ? d.objectTypes.join('、') : '无' },
    ],
    impact: d.objectTypes.length
      ? `删除后同步计划停止，${d.objectTypes.join('、')} 的数据不再更新（已同步的数据保留）。`
      : '没有对象类型使用这个连接，删除后同步计划一起删除。',
  });
  if (!ok) return;
  list.value = list.value.filter((x) => x.id !== d.id);
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
watch([formMode, () => selected.value?.id], ([m]) => {
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
  if (!form.host) { message.warning('请先填写主机'); return; }
  formTesting.value = true;
  await new Promise((r) => setTimeout(r, 600));
  formTesting.value = false;
  message.success(`连接成功：${form.host}:${form.port}，耗时 37 ms`);
}
function save() {
  if (!form.name || !form.host || !form.database || !form.user) { message.warning('请填写名称、主机、数据库和用户名'); return; }
  if (formMode.value === 'new' && !form.password) { message.warning('请填写密码'); return; }
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
const runColor = (r: string) => (r === '成功' ? 'tl-normal' : r === '延迟' ? 'tl-attention' : 'tl-failed');
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
          <div class="cn-item-l1">
            <span class="cn-type t-12">{{ typeShort[d.type] }}</span>
            <span class="t-14 fw-600 c-1 cn-ellip">{{ d.name }}</span>
            <a-badge :status="statusBadge(d.status)" :text="d.status" class="cn-badge" />
          </div>
          <div class="t-12 c-3 cn-item-l2">
            <span class="cn-ellip">{{ d.owner }} · 最近测试 <span class="num">{{ d.lastTest ? d.lastTest.time.slice(5) : '未测试' }}</span></span>
            <span v-if="modeText(d)" class="cn-ellip cn-mode">{{ modeText(d) }}</span>
          </div>
        </div>
      </div>
      <AppPagination v-model:page="listPage" class="cn-pager" variant="simple" :total="filtered.length" :size="LIST_SIZE" unit="个" />
    </div>

    <!-- 右：详情 -->
    <div v-if="selected" class="panel cn-detail">
      <div class="cn-head">
        <div class="cn-title">
          <span class="t-20 fw-600 c-1">{{ selected.name }}</span>
          <a-tag>{{ selected.type }}</a-tag>
          <a-tag v-if="modeText(selected)" color="blue">{{ modeText(selected) }}</a-tag>
          <a-badge :status="statusBadge(selected.status)" :text="selected.status" />
        </div>
        <a-space>
          <a-button :loading="testing" @click="testConn(selected)">测试连接</a-button>
          <a-tooltip :title="reason"><a-button :disabled="readonly" @click="openForm('edit')">编辑</a-button></a-tooltip>
          <a-tooltip :title="reason"><a-button danger :disabled="readonly" @click="remove(selected)">删除</a-button></a-tooltip>
        </a-space>
      </div>
      <div class="cn-meta t-12 c-3">
        <span class="num">{{ selected.id }}</span>
        <span>负责人 {{ selected.owner }}</span>
        <span>创建于 <span class="num">{{ selected.createdAt }}</span></span>
        <span v-if="selected.lastTest" :class="selected.lastTest.ok ? '' : 'tl-failed'">
          最近测试 <span class="num">{{ selected.lastTest.time }}</span> · {{ selected.lastTest.ok ? `成功，${selected.lastTest.ms} ms` : selected.lastTest.msg }}
        </span>
        <span v-else>尚未测试连接</span>
        <span>映射对象类型 {{ selected.objectTypes.length ? selected.objectTypes.join('、') : EMPTY }}</span>
      </div>
      <a-tabs v-model:activeKey="tab" class="cn-tabs">
        <a-tab-pane key="params" tab="连接参数" />
        <a-tab-pane key="tables" :tab="`库表（${dsTables(selected).length}）`" />
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
          <a-descriptions-item v-if="selected.type === '达梦 DM8'" label="兼容模式">{{ selected.compatMode }}</a-descriptions-item>
          <a-descriptions-item v-if="selected.type === '人大金仓 KingbaseES'" label="database_mode">{{ selected.databaseMode }}</a-descriptions-item>
          <a-descriptions-item v-if="selected.type === 'ERP'" label="ERP 系统">{{ selected.erpSystem }}</a-descriptions-item>
          <a-descriptions-item v-if="selected.type === 'ERP'" label="应用 ID"><span class="num">{{ selected.appId }}</span></a-descriptions-item>
          <a-descriptions-item label="连接串" :span="2"><span class="num">{{ jdbc(selected) }}</span></a-descriptions-item>
          <a-descriptions-item label="连接池">最小 2 · 最大 16 · 空闲 10 分钟回收</a-descriptions-item>
          <a-descriptions-item label="超时">连接 10 秒 · 查询 300 秒</a-descriptions-item>
          <a-descriptions-item label="读写">只读（数据接入只查询，不写回源库）</a-descriptions-item>
          <a-descriptions-item label="网络">内网直连 · 白名单 10.12.40.0/24</a-descriptions-item>
        </a-descriptions>
      </div>

      <div v-else-if="tab === 'tables'" class="cn-body cn-col">
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
                <a-tag v-if="record.objectType" color="blue">{{ record.objectType }}</a-tag><span v-else class="c-3">未映射</span>
              </span>
              <span v-else-if="column.key === 'updatedAt'" class="num">{{ record.updatedAt }}</span>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="tablePage" variant="simple" :total="tables.length" :size="TABLE_SIZE" unit="张表" class="cn-tpager" />
      </div>

      <div v-else class="cn-body cn-sync">
        <a-descriptions :column="2" bordered size="small" class="cn-desc">
          <a-descriptions-item label="同步方式">{{ selected.sync.mode }}</a-descriptions-item>
          <a-descriptions-item label="频率">{{ selected.sync.cronText }}<span v-if="selected.sync.cron !== '-'" class="num c-3 cn-gap">{{ selected.sync.cron }}</span></a-descriptions-item>
          <a-descriptions-item label="运行时间窗">{{ selected.sync.window }}</a-descriptions-item>
          <a-descriptions-item label="增量字段"><span class="num">{{ selected.sync.cursorField }}</span></a-descriptions-item>
          <a-descriptions-item label="下次运行"><span class="num">{{ selected.sync.nextRun ?? EMPTY }}</span></a-descriptions-item>
          <a-descriptions-item label="失败重试">3 次，间隔 5 分钟；仍失败通知负责人</a-descriptions-item>
        </a-descriptions>
        <div class="t-14 fw-600 c-1 cn-runs-title">最近同步</div>
        <div v-if="!selected.runs.length" class="c-3 t-12">还没有同步记录</div>
        <div v-for="(r, k) in selected.runs" :key="k" class="cn-run">
          <span class="cn-dot" :class="runColor(r.result)" />
          <span class="num c-2 cn-run-time">{{ r.time }}</span>
          <span class="cn-run-res" :class="runColor(r.result)">{{ r.result }}</span>
          <span class="num c-2 cn-run-n">{{ r.result === '失败' ? EMPTY : `${fmtInt(r.rows)} 行` }}</span>
          <span class="num c-3 cn-run-n">{{ r.secs }} 秒</span>
          <span class="c-3">{{ r.msg }}</span>
        </div>
      </div>
    </div>
    <div v-else class="panel cn-detail"><a-empty description="没有数据源" class="cn-empty" /></div>
  </div>

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
          <span v-if="formDirty" class="t-12 tl-attention">有未保存的修改</span>
          <a-button @click="closeForm">取消</a-button>
          <a-button type="primary" :disabled="readonly" @click="save">保存</a-button>
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
.cn-item-l2 { display: flex; justify-content: space-between; gap: 8px; margin-top: 2px; padding-left: 2px; }
.cn-type { flex: none; min-width: 42px; text-align: center; border: var(--border); border-radius: 4px; color: var(--g8); line-height: 18px; }
.cn-ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.cn-badge { margin-left: auto; flex: none; }
.cn-mode { flex: none; max-width: 150px; }
.cn-pager { padding: 6px 16px; }
.cn-detail { flex: 1; min-width: 0; }
.cn-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px 6px; }
.cn-title { display: flex; align-items: center; gap: 10px; }
.cn-meta { display: flex; flex-wrap: wrap; gap: 6px 20px; padding: 0 20px; }
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
