<script setup lang="ts">
/**
 * 对象类型 · 行为 · 动作：作用在该对象上的动作类型（来自动作类型目录，按 ACTION_OBJ_KIND 归到对象），
 * 下方是动作中心里该对象的动作（待确认 / 执行记录），点编号去动作中心处理。
 * 动作类型本身在“动作类型”页维护，这里只汇总，不提供编辑；草稿视图同样只读。
 * URL：?acpage= 动作中心列表页码，?actype= 打开的动作类型抽屉。
 */
import { computed, ref } from 'vue';
import { useRoute, useRouter, RouterLink } from 'vue-router';
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import ObjRef from '@/components/ObjRef.vue';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { paginate } from '@/mock/paginate';
import { ACTION_TYPES, paramsOf, runStats, type ActionType } from '@/mock/actionEvent';
import { ACTIONS, ACTION_OBJ_KIND, OBJ_KIND_LABEL, hasObjRef, waitingText, type ActionRecord, type ActionStatus, type SysId } from '@/mock/actions';
import { OBJ_TYPE_KIND, OBJ_TYPE_NAME, lifecycleOf, trName } from '@/mock/lifecycle';
import { useDraftView } from '../useDraftView';

const props = defineProps<{ objectId: string; readonly: boolean }>();
const route = useRoute();
const router = useRouter();
const { readonly: histRo, reason } = useDraftView('object', () => props.objectId);

const kind = computed(() => OBJ_TYPE_KIND[props.objectId]);
const objName = computed(() => OBJ_TYPE_NAME[props.objectId] ?? props.objectId);
const types = computed(() => (kind.value ? ACTION_TYPES.filter((t) => ACTION_OBJ_KIND[t.name] === kind.value) : []));
const lc = computed(() => lifecycleOf(props.objectId));
const drives = (t: ActionType) => (lc.value?.transitions ?? []).filter((x) => x.actionId === t.id);
const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

const typeCols = [
  { title: '动作类型', key: 'name', width: 200 },
  { title: '版本', key: 'version', width: 70 },
  { title: '状态', key: 'status', width: 90 },
  { title: '外部系统', key: 'system', width: 190 },
  { title: '审批', key: 'approval', width: 200 },
  { title: '驱动的状态迁移', key: 'drives' },
  { title: '被规则暂存', key: 'usedBy', width: 160 },
  { title: '近 30 天执行', key: 'runs', width: 330 },
];

/* 抽屉：动作类型详情（?actype=） */
const openId = computed(() => (route.query.actype as string) || '');
const openType = computed(() => types.value.find((t) => t.id === openId.value));
const setOpen = (id: string) => router.replace({ query: { ...route.query, actype: id || undefined } });

/* 动作中心里该对象的动作 */
const status = ref('全部');
const actName = ref('全部');
const pq = usePagedQuery({ defaultSize: 10, prefix: 'ac', filters: [status, actName, () => props.objectId] });
const all = computed(() => (kind.value ? ACTIONS.filter((a) => a.objKind === kind.value) : []));
const names = computed(() => ['全部', ...new Set(all.value.map((a) => a.action))]);
const STATUSES: ActionStatus[] = ['待确认', '审批中', '执行中', '成功', '失败', '结果未知', '已撤回', '已对账'];
const rows = computed(() => all.value.filter((a) => (status.value === '全部' || a.status === status.value) && (actName.value === '全部' || a.action === actName.value)));
const paged = computed(() => paginate(rows.value, { page: pq.page.value, size: pq.size.value }));
const pendingN = computed(() => all.value.filter((a) => a.status === '待确认').length);
const unknownN = computed(() => all.value.filter((a) => a.status === '结果未知').length);
const badge = (s: ActionStatus) => (s === '待确认' ? 'warning' : s === '失败' ? 'error' : 'default');
const actLink = (a: ActionRecord) => ({ path: '/use/ops/actions', query: { tab: a.status === '待确认' ? undefined : a.status === '结果未知' ? 'unknown' : 'records', act: a.id } });
const sourceText = (a: ActionRecord) => (a.source.type === '规则' ? `规则 ${a.source.ruleId}` : a.source.type === '告警' ? `告警 ${a.source.alertId}` : a.source.type === '问数' ? `问数 ${a.source.session}` : '手动');
const recCols = [
  { title: '编号', key: 'id', width: 170 },
  { title: '动作', dataIndex: 'action', key: 'action', width: 150 },
  { title: '对象', key: 'obj', width: 280 },
  { title: '参数', key: 'params' },
  { title: '来源', key: 'source', width: 200 },
  { title: '状态', key: 'status', width: 170 },
  { title: '提交时间', key: 'submittedAt', width: 170 },
];
</script>

<template>
  <div class="ot-body ba">
    <template v-if="types.length">
      <div class="ba-bar">
        <span class="t-14 fw-600 c-1">作用在{{ objName }}上的动作类型 {{ types.length }}</span>
        <span class="t-12 c-3">来自动作类型目录；参数、提交条件、写回在“动作类型”页维护，这里只读汇总{{ histRo ? `（${reason}）` : props.readonly ? '' : '，草稿不改动作类型' }}</span>
        <span class="ba-sp" />
        <RouterLink class="t-14" to="/build/ontology/action-types">去动作类型</RouterLink>
      </div>
      <a-table size="small" :columns="typeCols" :data-source="types" row-key="id" :pagination="false" class="ba-types"
        :custom-row="(r: ActionType) => ({ onClick: () => setOpen(r.id) })">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'name'"><a>{{ record.name }}</a><div class="t-12 c-3 num">{{ record.api }}</div></template>
          <template v-else-if="column.key === 'version'"><span class="num">{{ record.version }}</span></template>
          <template v-else-if="column.key === 'status'"><a-badge status="default" :text="record.status" /></template>
          <template v-else-if="column.key === 'system'">
            <template v-if="record.sysId">{{ record.system }}<div class="t-12 c-3">超时 {{ record.timeout }} · {{ record.idempotent ? '幂等' : '非幂等' }}</div></template>
            <span v-else class="c-3">本体内部</span>
          </template>
          <template v-else-if="column.key === 'approval'"><span class="c-2">{{ record.approval }}</span></template>
          <template v-else-if="column.key === 'drives'">
            <template v-if="drives(record).length"><div v-for="t in drives(record)" :key="t.id">{{ t.id }} {{ trName(t) }}</div></template>
            <span v-else class="c-3">不改{{ objName }}状态</span>
          </template>
          <template v-else-if="column.key === 'usedBy'">
            <template v-if="record.usedBy.length">
              <RouterLink v-for="r in record.usedBy" :key="r" class="ba-rule num" :to="{ path: '/build/ontology/rules', query: { rule: r } }" @click.stop>{{ r }}</RouterLink>
            </template>
            <span v-else class="c-3">只手动发起</span>
          </template>
          <template v-else-if="column.key === 'runs'">
            <template v-if="record.status !== '草稿'">
              <span class="num">{{ runStats(record.id).total }}</span> 次 · 成功率 <span class="num">{{ pct(runStats(record.id).rate) }}</span>
              <span v-if="runStats(record.id).failed" class="tl-failed"> · 失败 <span class="num">{{ runStats(record.id).failed }}</span></span>
              <span v-if="runStats(record.id).unknown" class="tl-attention"> · <QuestionCircleOutlined /> 结果未知 <span class="num">{{ runStats(record.id).unknown }}</span></span>
            </template>
            <span v-else class="c-3">草稿，未上线</span>
          </template>
        </template>
      </a-table>

      <div class="ba-bar">
        <span class="t-14 fw-600 c-1">动作中心里的{{ objName }}动作</span>
        <span class="t-12 c-3">待确认 <span :class="pendingN ? 'tl-attention' : ''" class="num">{{ pendingN }}</span> · 结果未知待对账 <span :class="unknownN ? 'tl-attention' : ''" class="num">{{ unknownN }}</span></span>
        <span class="ba-sp" />
        <a-select v-model:value="actName" size="small" class="ba-sel" :options="names.map((n) => ({ value: n, label: n === '全部' ? '全部动作' : n }))" />
        <a-select v-model:value="status" size="small" class="ba-sel" :options="['全部', ...STATUSES].map((s) => ({ value: s, label: s === '全部' ? '全部状态' : s }))" />
      </div>
      <div class="ba-recs">
        <a-table size="small" :columns="recCols" :data-source="paged.items" row-key="id" :pagination="false"
          :locale="{ emptyText: all.length ? '没有符合条件的动作' : `动作中心里还没有${objName}的动作` }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'id'"><RouterLink class="num" :to="actLink(record)">{{ record.id }}</RouterLink></template>
            <template v-else-if="column.key === 'obj'">
              <ObjRef v-if="hasObjRef(record.objKind)" :kind="record.objKind" :id="record.objId">{{ record.objLabel }}</ObjRef>
              <span v-else>{{ record.objLabel }}</span>
            </template>
            <template v-else-if="column.key === 'params'"><span class="c-2">{{ record.params }}</span></template>
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

    <div v-else class="ba-empty">
      <div class="t-14 c-2">还没有作用在{{ objName }}上的动作类型。</div>
      <div class="t-12 c-3">动作类型在“动作类型”页新建，目标对象选“{{ objName }}”后会出现在这里。</div>
      <RouterLink class="t-14" to="/build/ontology/action-types">去动作类型</RouterLink>
    </div>

    <a-drawer :open="!!openType" :width="560" :title="openType ? `${openType.name} · ${openType.version}` : ''" @close="setOpen('')">
      <template v-if="openType">
        <a-descriptions :column="1" size="small" bordered>
          <a-descriptions-item label="说明">{{ openType.desc }}</a-descriptions-item>
          <a-descriptions-item label="作用对象">{{ kind ? OBJ_KIND_LABEL[kind] : objName }}</a-descriptions-item>
          <a-descriptions-item label="外部系统">
            <template v-if="openType.sysId">{{ openType.system }} · {{ openType.endpoint }}</template><span v-else>本体内部</span>
          </a-descriptions-item>
          <a-descriptions-item v-if="openType.sysId" label="超时与重试">
            {{ waitingText(openType.sysId as SysId) }}，不重试写入；{{ openType.retry }}
          </a-descriptions-item>
          <a-descriptions-item label="幂等键"><span class="num">{{ openType.idemKey }}</span></a-descriptions-item>
          <a-descriptions-item label="可发起">{{ openType.runners }}</a-descriptions-item>
          <a-descriptions-item label="审批">{{ openType.approval }}</a-descriptions-item>
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
.ba-types { flex: none; max-height: 40%; overflow: auto; }
.ba-types :deep(.ant-table-row) { cursor: pointer; }
.ba-rule { margin-right: 8px; }
.ba-sel { width: 160px; }
.ba-recs { flex: 1; min-height: 0; overflow: auto; }
.ba-nowrap { white-space: nowrap; }
.ba-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; }
.ba-sec { margin: 16px 0 8px; }
.ba-foot { margin-top: 16px; }
</style>
