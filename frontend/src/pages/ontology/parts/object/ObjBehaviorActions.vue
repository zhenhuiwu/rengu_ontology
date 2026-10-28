<script setup lang="ts">
import InfoTip from '@/components/InfoTip.vue';
/**
 * 对象类型 · 行为 · 动作：作用在该对象上的动作类型（来自动作类型目录，按 ACTION_OBJ_KIND 归到对象），
 * 下方是动作中心里该对象的动作（待确认 / 执行记录），点编号去动作中心处理。
 * 动作类型本身在“动作类型”页维护，这里只汇总，不提供编辑；草稿视图同样只读。
 * URL：?acpage= 动作中心列表页码，?actype= 打开的动作类型抽屉。
 */
import { computed, ref, watch, onScopeDispose } from 'vue';
import { useRoute, useRouter, RouterLink } from 'vue-router';
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import ObjRef from '@/components/ObjRef.vue';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { paginate } from '@/mock/paginate';
import { ACTION_TYPES, paramsOf as mockParams, runStats as mockStats, type ActionParam, type ActionType } from '@/mock/actionEvent';
import { ACTIONS, ACTION_OBJ_KIND, OBJ_KIND_LABEL, hasObjRef, waitingText, type ActionRecord, type ActionStatus, type SysId } from '@/mock/actions';
import { OBJ_TYPE_KIND, OBJ_TYPE_NAME, lifecycleOf, trName } from '@/mock/lifecycle';
import { useDraftView } from '../useDraftView';
import { actionApi } from '@/api/action';
import { dynamicApi, getLifecycle, type ActionRunDto, type LifecycleDto } from '@/api/dynamic';
import { currentApiContext, isApiError, type PageResult } from '@/api/http';
import { OBJECT_TYPE_LIST } from '@/api/static';
import { fetchBehaviorStats } from '@/api/objectAutomation';

const props = defineProps<{ objectId: string; readonly: boolean }>();
const route = useRoute();
const router = useRouter();
const { readonly: histRo, reason } = useDraftView('object', () => props.objectId);

const real = actionApi.isReal();
let mounted = true;
onScopeDispose(() => { mounted = false; });
const remoteTypes = ref<ActionType[]>([]);
type ActionRow = Omit<ActionRecord, 'log'>;
const remoteRecords = ref<ActionRow[]>([]);
const recordTotal = ref(0), recordsError = ref('');
const centerCounts = ref<Record<string, number>>({});
const remoteParams = ref<Record<string, ActionParam[]>>({});
const remoteLc = ref<LifecycleDto | null>(null);
const remoteStats = ref<Record<string, { total: number; rate: number; failed: number; unknown: number }>>({});
const loading = ref(false);
const loadError = ref('');
const object = computed(() => OBJECT_TYPE_LIST.find((o) => o.id === props.objectId));
const kind = computed(() => real ? props.objectId : OBJ_TYPE_KIND[props.objectId]);
const objName = computed(() => real ? object.value?.name ?? props.objectId : OBJ_TYPE_NAME[props.objectId] ?? props.objectId);
const kindLabel = computed(() => OBJ_KIND_LABEL[kind.value as keyof typeof OBJ_KIND_LABEL] ?? objName.value);
const types = computed(() => real ? remoteTypes.value : (kind.value ? ACTION_TYPES.filter((t) => ACTION_OBJ_KIND[t.name] === kind.value) : []));
const lc = computed(() => real ? remoteLc.value ?? undefined : lifecycleOf(props.objectId));
const drives = (t: ActionType) => (lc.value?.transitions ?? []).filter((x) => x.actionId === t.id);
const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
const paramsOf = (t: ActionType) => real ? remoteParams.value[t.id] ?? [] : mockParams(t);
const runStats = (id: string) => real ? remoteStats.value[id] ?? { total: 0, rate: 0, failed: 0, unknown: 0 } : mockStats(id);
async function allPages<T>(client: typeof actionApi, path: string, query: Record<string, string> = {}) {
  const rows: T[] = [], ctx = { ...currentApiContext() };
  for (let page = 1; ; page++) {
    const result = await client.get<PageResult<T>>(path, { headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' }, readonlyParams: false, query: { ...query, page, size: 200, ver: ctx.ver, previewAs: ctx.previewAs } });
    rows.push(...result.items);
    if (!result.items.length || rows.length >= result.total) return rows;
  }
}
let loadSeq = 0;
async function loadActions() {
  const my = ++loadSeq;
  if (!mounted || !real || !props.objectId) return;
  const key = JSON.stringify([props.objectId, currentApiContext()]);
  remoteTypes.value = []; remoteRecords.value = []; remoteParams.value = {}; remoteStats.value = {}; centerCounts.value = {}; remoteLc.value = null; loadError.value = ''; loading.value = true;
  try {
    if (!dynamicApi.isReal()) throw new Error('动作中心数据服务尚未接入');
    const [stats, lifecycle] = await Promise.all([
      fetchBehaviorStats(props.objectId),
      getLifecycle(props.objectId).catch((e) => { if (isApiError(e) && e.status === 404) return null; throw e; }),
    ]);
    if (!mounted || my !== loadSeq || key !== JSON.stringify([props.objectId, currentApiContext()])) return;
    remoteTypes.value = stats.actions;
    remoteStats.value = stats.actions30d;
    centerCounts.value = stats.centerStatusCounts;
    remoteLc.value = lifecycle;
  } catch (e) {
    if (mounted && my === loadSeq && key === JSON.stringify([props.objectId, currentApiContext()])) loadError.value = `动作数据加载失败：${e instanceof Error ? e.message : String(e)}`;
  } finally { if (mounted && my === loadSeq) loading.value = false; }
}
watch(() => JSON.stringify([props.objectId, currentApiContext()]), loadActions, { immediate: true, flush: 'sync' });

const typeCols = [
  { title: '动作类型', key: 'name', width: 170, ellipsis: true },
  { title: '版本', key: 'version', width: 60 },
  { title: '状态', key: 'status', width: 84 },
  { title: '外部系统', key: 'system', width: 100, ellipsis: true },
  { title: '超时 · 幂等', key: 'timeout', width: 100 },
  { title: '审批', key: 'approval', width: 170, ellipsis: true },
  { title: '驱动的状态迁移', key: 'drives', ellipsis: true },
  { title: '被规则暂存', key: 'usedBy', width: 110, ellipsis: true },
  { title: '近 30 天执行', key: 'runs', width: 380 },
];

/* 抽屉：动作类型详情（?actype=） */
const openId = computed(() => (route.query.actype as string) || '');
const openType = computed(() => types.value.find((t) => t.id === openId.value));
const setOpen = (id: string) => router.replace({ query: { ...route.query, actype: id || undefined } });
let paramsSeq = 0;
async function loadParams() {
  const n = ++paramsSeq, id = openType.value?.id, key = JSON.stringify([props.objectId, currentApiContext()]);
  remoteParams.value = {};
  if (!mounted || !real || !id) return;
  try {
    const params = await allPages<ActionParam>(actionApi, `/action-types/${encodeURIComponent(id)}/params`);
    if (mounted && n === paramsSeq && key === JSON.stringify([props.objectId, currentApiContext()]) && id === openType.value?.id) remoteParams.value = { [id]: params };
  } catch (e) { if (mounted && n === paramsSeq && key === JSON.stringify([props.objectId, currentApiContext()])) loadError.value = `动作参数加载失败：${e instanceof Error ? e.message : String(e)}`; }
}
if (real) watch([() => openType.value?.id, () => JSON.stringify([props.objectId, currentApiContext()])], loadParams, { immediate: true, flush: 'sync' });

/* 动作中心里该对象的动作 */
const status = ref('全部');
const actName = ref('全部');
const pq = usePagedQuery({ defaultSize: 10, prefix: 'ac', filters: [status, actName, () => props.objectId] });
const all = computed(() => real ? remoteRecords.value : (kind.value ? ACTIONS.filter((a) => a.objKind === kind.value) : []));
const names = computed(() => real ? ['全部', ...types.value.map(t => t.id)] : ['全部', ...new Set(all.value.map((a) => a.action))]);
const nameLabel = (id: string) => id === '全部' ? '全部动作' : real ? types.value.find(t => t.id === id)?.name ?? id : id;
/** 能把对象状态往回改的动作：在详情里写明回退边界（按生命周期 v4） */
const GUARDS: Record<string, string> = {
  correct_order_status: '只能回退到生命周期 v4 中的上一个合法状态；订单已出库（含已签收）的修正不走免审，需订单运营负责人审批',
};
const STATUSES: ActionStatus[] = ['待确认', '审批中', '执行中', '成功', '失败', '结果未知', '已撤回', '已对账'];
const rows = computed(() => all.value.filter((a) => (status.value === '全部' || a.status === status.value) && (actName.value === '全部' || a.action === actName.value)));
let recordsSeq = 0, recordsScope = '';
async function loadRecords() {
  const n = ++recordsSeq, key = JSON.stringify([props.objectId, currentApiContext()]);
  const scope = JSON.stringify([key, status.value, actName.value]);
  if (scope !== recordsScope) { recordsScope = scope; remoteRecords.value = []; recordTotal.value = 0; }
  recordsError.value = '';
  if (!mounted || !real || !props.objectId) return;
  if (!dynamicApi.isReal()) { recordsError.value = '动作中心数据服务尚未接入'; return; }
  try {
    const r = await dynamicApi.get<PageResult<ActionRunDto>>('/action-runs', { query: { scope: 'center', objKind: props.objectId, status: status.value === '全部' ? undefined : status.value, actionType: actName.value === '全部' ? undefined : actName.value, page: pq.page.value, size: pq.size.value } });
    if (!mounted || n !== recordsSeq || key !== JSON.stringify([props.objectId, currentApiContext()])) return;
    remoteRecords.value = r.items as ActionRow[]; recordTotal.value = r.total;
    if (r.page && r.page !== pq.page.value) pq.page.value = r.page;
  } catch (e) { if (mounted && n === recordsSeq && key === JSON.stringify([props.objectId, currentApiContext()])) { remoteRecords.value = []; recordsError.value = e instanceof Error ? e.message : String(e); } }
}
if (real) watch([() => props.objectId, () => JSON.stringify(currentApiContext()), status, actName, pq.page, pq.size], loadRecords, { immediate: true, flush: 'sync' });
const paged = computed(() => real ? { items: remoteRecords.value, total: recordTotal.value } : paginate(rows.value, { page: pq.page.value, size: pq.size.value }));
const pendingN = computed(() => real ? centerCounts.value['待确认'] ?? 0 : all.value.filter((a) => a.status === '待确认').length);
const unknownN = computed(() => real ? centerCounts.value['结果未知'] ?? 0 : all.value.filter((a) => a.status === '结果未知').length);
const hasRecords = computed(() => real ? recordTotal.value > 0 || Object.values(centerCounts.value).some(n => n > 0) : all.value.length > 0);
const badge = (s: ActionStatus) => (s === '待确认' ? 'warning' : s === '失败' ? 'error' : 'default');
const actLink = (a: ActionRow) => ({ path: '/use/ops/actions', query: { tab: a.status === '待确认' ? undefined : a.status === '结果未知' ? 'unknown' : 'records', act: a.id } });
const sourceText = (a: ActionRow) => (a.source.type === '规则' ? `规则 ${a.source.ruleId}` : a.source.type === '告警' ? `告警 ${a.source.alertId}` : a.source.type === '问数' ? `问数 ${a.source.session}`
  : a.source.type === '推演' ? `情景推演 · ${a.source.scenarioId}` : '手动');
const recCols = [
  { title: '编号', key: 'id', width: 170 },
  { title: '动作', dataIndex: 'action', key: 'action', width: 150 },
  { title: '对象', key: 'obj', width: 280 },
  { title: '参数', key: 'params', ellipsis: true },
  { title: '来源', key: 'source', width: 200 },
  { title: '状态', key: 'status', width: 170 },
  { title: '提交时间', key: 'submittedAt', width: 170 },
];
</script>

<template>
  <div class="ot-body ba">
    <a-alert v-if="loadError" type="error" show-icon :message="loadError"><template #action><a-button size="small" @click="loadActions">重试</a-button></template></a-alert>
    <a-spin v-if="loading" tip="正在读取动作定义与执行记录…" />
    <template v-if="types.length || hasRecords">
      <div class="ba-bar">
        <span class="t-14 fw-600 c-1">作用在{{ objName }}上的动作类型 {{ types.length }}</span>
        <InfoTip :text="`来自动作类型目录；参数、提交条件、写回在“动作类型”页维护，这里只读汇总${histRo ? `（${reason}）` : props.readonly ? '' : '，草稿不改动作类型'}`" />
        <span class="ba-sp" />
        <RouterLink class="t-14" to="/build/ontology/action-types">去动作类型</RouterLink>
      </div>
      <a-table size="small" :columns="typeCols" :data-source="types" row-key="id" :pagination="false" class="ba-types"
        :custom-row="(r: ActionType) => ({ onClick: () => setOpen(r.id) })">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'name'"><a :title="record.api">{{ record.name }}</a></template>
          <template v-else-if="column.key === 'version'"><span class="num">{{ record.version }}</span></template>
          <template v-else-if="column.key === 'status'"><a-badge status="default" :text="record.status" /></template>
          <template v-else-if="column.key === 'system'">
            <template v-if="record.sysId">{{ record.system }}</template>
            <span v-else class="c-3">本体内部</span>
          </template>
          <template v-else-if="column.key === 'timeout'">
            <span v-if="record.sysId" class="num">{{ record.timeout }} · {{ record.idempotent ? '幂等' : '非幂等' }}</span>
            <span v-else class="c-3">--</span>
          </template>
          <template v-else-if="column.key === 'approval'"><span class="c-2" :title="record.approval">{{ record.approval }}</span></template>
          <template v-else-if="column.key === 'drives'">
            <span v-if="real && !lc" class="c-3">未配置生命周期</span>
            <span v-else-if="drives(record).length" :title="drives(record).map((t) => `${t.id} ${trName(t)}`).join('、')">{{ drives(record).map((t) => `${t.id} ${trName(t)}`).join('、') }}</span>
            <span v-else class="c-3">不改{{ objName }}状态</span>
          </template>
          <template v-else-if="column.key === 'usedBy'">
            <template v-if="record.usedBy.length">
              <RouterLink v-for="r in record.usedBy" :key="r" class="ba-rule num" :to="{ path: '/build/ontology/rules', query: { rule: r } }" @click.stop>{{ r }}</RouterLink>
            </template>
            <span v-else class="c-3">只手动发起</span>
          </template>
          <template v-else-if="column.key === 'runs'">
            <span v-if="record.status !== '草稿'" class="ba-nowrap">
              <span class="num">{{ runStats(record.id).total }}</span> 次 · 成功率 <span class="num">{{ pct(runStats(record.id).rate) }}</span>
              <span v-if="runStats(record.id).failed" class="tl-failed"> · 失败 <span class="num">{{ runStats(record.id).failed }}</span></span>
              <span v-if="runStats(record.id).unknown" class="tl-attention"> · <QuestionCircleOutlined /> 结果未知 <span class="num">{{ runStats(record.id).unknown }}</span></span>
            </span>
            <span v-else class="c-3">草稿，未上线</span>
          </template>
        </template>
      </a-table>

      <div class="ba-bar">
        <span class="t-14 fw-600 c-1">动作中心里的{{ objName }}动作</span>
        <span class="t-12 c-2">待确认 <span :class="pendingN ? 'tl-attention' : ''" class="num">{{ pendingN }}</span> · 结果未知待对账 <span :class="unknownN ? 'tl-attention' : ''" class="num">{{ unknownN }}</span></span>
        <span class="ba-sp" />
        <a-select v-model:value="actName" size="small" class="ba-sel" :options="names.map((n) => ({ value: n, label: nameLabel(n) }))" />
        <a-select v-model:value="status" size="small" class="ba-sel" :options="['全部', ...STATUSES].map((s) => ({ value: s, label: s === '全部' ? '全部状态' : s }))" />
      </div>
      <div class="ba-recs">
        <a-alert v-if="recordsError" type="error" :message="recordsError" show-icon><template #action><a-button size="small" @click="loadRecords">重试</a-button></template></a-alert>
        <a-table size="small" :columns="recCols" :data-source="paged.items" row-key="id" :pagination="false"
          :locale="{ emptyText: hasRecords ? '没有符合条件的动作' : `动作中心里还没有${objName}的动作` }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'id'"><RouterLink class="num" :to="actLink(record)">{{ record.id }}</RouterLink></template>
            <template v-else-if="column.key === 'obj'">
              <ObjRef v-if="hasObjRef(record.objKind)" :kind="record.objKind" :id="record.objId">{{ record.objLabel }}</ObjRef>
              <span v-else>{{ record.objLabel }}</span>
            </template>
            <template v-else-if="column.key === 'params'"><span class="c-2" :title="record.params">{{ record.params }}</span></template>
            <template v-else-if="column.key === 'source'"><span class="c-2">{{ sourceText(record) }}</span></template>
            <template v-else-if="column.key === 'status'">
              <span v-if="record.status === '结果未知'" class="tl-attention ba-nowrap"><QuestionCircleOutlined /> 结果未知 · 待对账</span>
              <a-badge v-else :status="badge(record.status)" :text="record.status" />
            </template>
            <template v-else-if="column.key === 'submittedAt'"><span class="num">{{ record.submittedAt }}</span></template>
          </template>
        </a-table>
      </div>
      <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="paged.total" :default-size="pq.defaultSize" :empty="false" />
    </template>

    <div v-else-if="!loading && !loadError" class="ba-empty">
      <div class="t-14 c-2">还没有作用在{{ objName }}上的动作类型。</div>
      <div class="t-12 c-3">已发布动作定义的目标 API 为“{{ object?.api ?? objName }}”时会出现在这里。</div>
      <RouterLink class="t-14" to="/build/ontology/action-types">去动作类型</RouterLink>
    </div>

    <a-drawer :open="!!openType" :width="560" :title="openType ? `${openType.name} · ${openType.version}` : ''" @close="setOpen('')">
      <template v-if="openType">
        <a-descriptions :column="1" size="small" bordered :label-style="{ width: '96px', whiteSpace: 'nowrap' }">
          <a-descriptions-item label="说明">{{ openType.desc }}</a-descriptions-item>
          <a-descriptions-item label="作用对象">{{ real ? openType.target : kindLabel }}</a-descriptions-item>
          <a-descriptions-item label="外部系统">
            <template v-if="openType.sysId">{{ openType.system }} · {{ openType.endpoint }}</template><span v-else>本体内部</span>
          </a-descriptions-item>
          <a-descriptions-item v-if="openType.sysId" label="超时与重试">
            {{ real ? openType.timeout : waitingText(openType.sysId as SysId) }}；{{ openType.retry }}
          </a-descriptions-item>
          <a-descriptions-item label="幂等键"><span class="num">{{ openType.idemKey }}</span></a-descriptions-item>
          <a-descriptions-item label="可发起">{{ openType.runners }}</a-descriptions-item>
          <a-descriptions-item v-if="!real && GUARDS[openType.id]" label="回退限制">{{ GUARDS[openType.id] }}</a-descriptions-item>
          <a-descriptions-item label="负责人">{{ openType.owner }}</a-descriptions-item>
        </a-descriptions>
        <div class="t-14 fw-600 c-1 ba-sec">参数</div>
        <a-table size="small" :pagination="false" row-key="api" :data-source="paramsOf(openType)"
          :columns="[{ title: '参数', dataIndex: 'name' }, { title: '类型', dataIndex: 'type', width: 90 }, { title: '必填', key: 'req', width: 60 }, { title: '规则', dataIndex: 'rule' }]">
          <template #bodyCell="{ column, record }"><template v-if="column.key === 'req'">{{ record.required ? '是' : '否' }}</template></template>
        </a-table>
        <div class="ba-foot">
          <RouterLink :to="{ path: '/build/ontology/action-types', query: { action: openType.id } }">在动作类型页查看提交条件、写回与执行记录</RouterLink>
        </div>
      </template>
    </a-drawer>
  </div>
</template>

<style scoped>
.ba-bar { display: flex; align-items: center; gap: 12px; flex: none; }
.ba-sp { flex: 1; }
.ba-types { flex: none; }
.ba-types :deep(.ant-table-row) { cursor: pointer; }
.ba-rule { margin-right: 8px; }
.ba-sel { width: 160px; }
.ba-recs { flex: 1; min-height: 0; overflow: auto; }
.ba-nowrap { white-space: nowrap; }
.ba-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; }
.ba-sec { margin: 16px 0 8px; }
.ba-foot { margin-top: 16px; }
</style>
