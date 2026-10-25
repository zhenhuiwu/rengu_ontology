<script setup lang="ts">
/**
 * 告警处理（动态本体）：左侧告警列表（分页），右侧当前告警详情：触发依据、处理记录、可执行动作。
 * 当前选中写进 ?alert=，状态页签写进 ?status=。对象点开走公共抽屉，不跳转。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import ObjRef from '@/components/ObjRef.vue';
import HoverCard from '@/components/HoverCard.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { ALERTS, RULES, METRICS, alertTimeline, type Alert, type AlertStatus } from '@/mock';
import { ACTIONS } from '@/mock/actions';

type LogItem = { time: string; text: string; who: string; abnormal?: boolean };
type Row = Alert & { log: LogItem[]; actionRef?: string; actionStatus?: string };
/** 页面内的告警状态（模拟后台：认领、转派、关闭、发起动作都会改它并写处理记录） */
/** 关联动作取自动作中心同一份数据（mock/actions.ts，按来源告警匹配最近一条），两页的幂等键和状态一致 */
const LIST = reactive<Row[]>(ALERTS.map((a) => {
  const act = ACTIONS.find((x) => x.source.alertId === a.id);
  const log = alertTimeline(a);
  if (act && !(a.status === '已解决' || a.status === '已忽略')) log.push({ time: act.submittedAt, text: `发起动作「${act.action}」（${act.idemKey}），现为${act.status}`, who: act.initiator });
  log.sort((x, y) => (x.time < y.time ? -1 : 1));
  return { ...a, log, actionRef: act?.idemKey, actionStatus: act?.status };
}));
/** 关联动作还没有结果时锁定转派和关闭 */
const IN_FLIGHT = ['待确认', '审批中', '执行中', '结果未知'];
const ME = '王敏';
const PEOPLE = ['王敏', '陈立军', '刘婷', '孙浩然', '何静', '赵蕾', '吴佳'];
let tick = 0;
const now = () => { tick += 1; return `2026-10-25 16:${String(30 + tick).padStart(2, '0')}:00`; };
const isClosed = (s: AlertStatus) => s === '已解决' || s === '已忽略';

const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const { readonly, reason: roReason } = useReadonly();

const TABS: { key: string; title: string; match: (s: AlertStatus) => boolean }[] = [
  { key: 'open', title: '待处理', match: (s) => s === '待处理' },
  { key: 'doing', title: '处理中', match: (s) => s === '处理中' },
  { key: 'closed', title: '已关闭', match: (s) => s === '已解决' || s === '已忽略' },
  { key: 'all', title: '全部', match: () => true },
];
const tab = computed({
  get: () => (route.query.status as string) || 'open',
  set: (v) => router.replace({ query: { ...route.query, status: v, alert: undefined, page: undefined } }),
});
const level = ref('全部');
const ruleId = ref('全部');
const kw = ref('');
const page = computed({
  get: () => Number(route.query.page) || 1,
  set: (v: number) => router.replace({ query: { ...route.query, page: v > 1 ? String(v) : undefined } }),
});
const pageSize = computed({
  get: () => Number(route.query.size) || 12,
  set: (v: number) => router.replace({ query: { ...route.query, size: v === 12 ? undefined : String(v), page: undefined } }),
});
watch([level, ruleId, kw], () => { if (page.value !== 1) page.value = 1; });

const rows = computed(() => {
  const t = TABS.find((x) => x.key === tab.value) ?? TABS[3];
  return LIST.filter((a) =>
    t.match(a.status) &&
    (level.value === '全部' || a.level === level.value) &&
    (ruleId.value === '全部' || a.ruleId === ruleId.value) &&
    (!kw.value || a.id.includes(kw.value) || a.objLabel.includes(kw.value)),
  );
});
const count = (key: string) => LIST.filter((a) => TABS.find((t) => t.key === key)!.match(a.status)).length;

const pageRows = computed(() => rows.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value));
/** 刚处理过的告警即使离开当前页签也继续显示在右侧，避免详情突然换成别的告警 */
const pinned = ref<string>();
const selectedId = computed(() => (route.query.alert as string) || pinned.value || pageRows.value[0]?.id);
const current = computed<Row | undefined>(() => LIST.find((a) => a.id === selectedId.value) ?? pageRows.value[0]);
const rule = computed(() => (current.value ? RULES[current.value.ruleId] : null));
const locked = computed(() => !!current.value?.actionStatus && IN_FLIGHT.includes(current.value.actionStatus));
const metric = computed(() => (rule.value?.metric ? METRICS.find((m) => m.id === rule.value!.metric) : undefined));
/** 同一规则、同一对象近 7 天的触发次数（含本条） */
const recent = computed(() => {
  const a = current.value; if (!a) return 0;
  const end = new Date(a.triggeredAt.replace(' ', 'T')).getTime();
  return ALERTS.filter((x) => x.ruleId === a.ruleId && x.objId === a.objId &&
    end - new Date(x.triggeredAt.replace(' ', 'T')).getTime() < 7 * 864e5 &&
    new Date(x.triggeredAt.replace(' ', 'T')).getTime() <= end).length + (a.ruleId === 'R-012' ? 2 : 0);
});
const pick = (a: Alert) => (pinned.value = a.id, router.replace({ query: { ...route.query, alert: a.id } }));

const statusBadge = (s: AlertStatus) =>
  s === '待处理' ? 'warning' : 'default';
const levelClass = (l: Alert['level']) => (l === '高' ? 'c-error fw-600' : 'c-2');

const columns = [
  { title: '级别', key: 'level', width: 64 },
  { title: '告警编号', dataIndex: 'id', key: 'id', width: 176 },
  { title: '规则', key: 'rule', width: 172 },
  { title: '对象', key: 'obj', ellipsis: true },
  { title: '触发值 / 阈值', key: 'value', width: 200 },
  { title: '触发时间', dataIndex: 'triggeredAt', key: 'triggeredAt', width: 172 },
  { title: '状态', key: 'status', width: 92 },
  { title: '处理人', key: 'assignee', width: 88 },
];

function claimRow(a: Row, who = ME) {
  if (a.status !== '待处理') return;
  a.status = '处理中'; a.assignee = who;
  a.log.push({ time: now(), text: '认领，状态 待处理 → 处理中', who });
}
function claim() { if (current.value) { pinned.value = current.value.id; claimRow(current.value); message.success(`已认领 ${current.value.id}`); } }

// 转派
const transferTo = ref<string>();
const transferOpen = ref(false);
function doTransfer() {
  const a = current.value; if (!a || !transferTo.value) return;
  pinned.value = a.id;
  const from = a.assignee ?? '未认领';
  if (a.status === '待处理') a.status = '处理中';
  a.assignee = transferTo.value;
  a.log.push({ time: now(), text: `转派：${from} → ${transferTo.value}`, who: ME });
  transferOpen.value = false; transferTo.value = undefined;
  message.success('已转派');
}

// 关闭：必须选结果并写原因
const closeOpen = ref(false);
const batchCloseOpen = ref(false);
const closeAs = ref<'已解决' | '已忽略'>('已解决');
const closeReason = ref('');
function closeRows(rowsToClose: Row[]) {
  for (const a of rowsToClose) {
    const from = a.status;
    a.status = closeAs.value; a.assignee = a.assignee ?? ME;
    a.log.push({ time: now(), text: `关闭为${closeAs.value}：${closeReason.value}（${from} → ${closeAs.value}）`, who: ME });
  }
  closeOpen.value = false; batchCloseOpen.value = false; closeReason.value = '';
}
function doClose() { if (current.value && closeReason.value.trim()) { pinned.value = current.value.id; closeRows([current.value]); message.success('已关闭'); } }

// 批量
const selectedKeys = ref<string[]>([]);
/** 只有本人处理中的告警才能关闭、发起动作 */
const mine = computed(() => current.value?.status === '处理中' && current.value.assignee === ME);
const inList = computed(() => !!current.value && rows.value.some((a) => a.id === current.value!.id));
const selectedRows = computed(() => LIST.filter((a) => selectedKeys.value.includes(a.id)));
const closable = computed(() => selectedRows.value.filter((a) => a.status === '处理中' && a.assignee === ME));
function batchClaim() {
  const n = selectedRows.value.filter((a) => a.status === '待处理').length;
  selectedRows.value.forEach((a) => claimRow(a));
  selectedKeys.value = [];
  message.success(`已认领 ${n} 条`);
}
function doBatchClose() {
  if (!closeReason.value.trim()) return;
  const n = closable.value.length;
  closeRows(closable.value);
  selectedKeys.value = [];
  message.success(`已关闭 ${n} 条`);
}
watch(tab, () => { selectedKeys.value = []; pinned.value = undefined; });
async function runAction() {
  const a = current.value; const r = rule.value;
  if (!a || !r?.action) return;
  const ok = await confirm.ask({
    title: r.action,
    tag: '待确认',
    target: `${a.objKind === 'order' ? '订单' : a.objKind === 'product' ? '商品' : '门店'} ${a.objLabel}`,
    fields: [
      { label: '来源告警', value: `${a.id}（${r.id} ${r.version}）` },
      { label: '触发值', value: `${a.value}，阈值 ${a.threshold}` },
      { label: '审批人', value: a.objKind === 'product' ? '品类经理 王敏' : '区域运营 陈立军' },
      { label: '幂等键', value: `act-${a.id.toLowerCase()}` },
    ],
    impact: '提交后写回外部系统；外部系统超时会标记为“结果未知”，系统每 15 分钟按幂等键自动查询，仍无法确认时在动作中心人工对账，不自动重试写入。',
  });
  if (!ok) return;
  pinned.value = a.id;
  a.actionRef = `act-${a.id.toLowerCase()}`;
  a.actionStatus = '审批中';
  a.log.push({ time: now(), text: `发起动作「${r.action}」，待${a.objKind === 'product' ? '品类经理 王敏' : '区域运营 陈立军'}审批；动作成功后告警自动转为已解决`, who: ME });
  message.success(`已提交：${r.action}`);
}
</script>

<template>
  <div class="al">
    <div class="panel al-list">
      <div class="panel-head">
        <a-radio-group v-model:value="tab" size="small" button-style="solid">
          <a-radio-button v-for="t in TABS" :key="t.key" :value="t.key">{{ t.title }} {{ count(t.key) }}</a-radio-button>
        </a-radio-group>
        <div v-if="selectedKeys.length" class="al-filters">
          <span class="t-14 c-2">已选 {{ selectedKeys.length }} 条</span>
          <a-button size="small" @click="batchClaim">批量认领</a-button>
          <a-popover v-model:open="batchCloseOpen" trigger="click" placement="bottomRight">
            <template #content>
              <div class="al-pop">
                <a-radio-group v-model:value="closeAs"><a-radio value="已解决">已解决</a-radio><a-radio value="已忽略">已忽略</a-radio></a-radio-group>
                <a-textarea v-model:value="closeReason" :rows="3" placeholder="关闭原因（必填）" />
                <div class="al-pop-foot"><a-button size="small" type="primary" :disabled="!closeReason.trim()" @click="doBatchClose">关闭 {{ closable.length }} 条</a-button></div>
              </div>
            </template>
            <a-tooltip :title="closable.length ? undefined : '只能关闭本人处理中的告警，请先认领'"><a-button size="small" :disabled="!closable.length">批量关闭{{ closable.length && closable.length < selectedKeys.length ? `（${closable.length}）` : '' }}</a-button></a-tooltip>
          </a-popover>
          <a-button size="small" type="text" @click="selectedKeys = []">取消选择</a-button>
        </div>
        <div v-else class="al-filters">
          <a-select v-model:value="level" size="small" style="width: 104px"
            :options="['全部', '高', '中', '低'].map((v) => ({ value: v, label: v === '全部' ? '全部级别' : `${v}级` }))" />
          <a-select v-model:value="ruleId" size="small" style="width: 180px"
            :options="[{ value: '全部', label: '全部规则' }, ...Object.values(RULES).map((r) => ({ value: r.id, label: `${r.id} ${r.name}` }))]" />
          <a-input-search v-model:value="kw" size="small" placeholder="告警编号 / 对象" style="width: 200px" />
        </div>
      </div>
      <div class="al-table">
        <a-table :columns="columns" :data-source="rows" row-key="id" size="middle"
          :row-selection="{ selectedRowKeys: selectedKeys, onChange: (k: (string | number)[]) => (selectedKeys = k as string[]), getCheckboxProps: (r: Row) => ({ disabled: readonly || isClosed(r.status) }) }" :scroll="pageSize > 12 ? { y: 572 } : undefined"
          :pagination="{ current: page, pageSize, total: rows.length, showSizeChanger: true, pageSizeOptions: ['12', '20', '50'], showQuickJumper: rows.length > pageSize * 10, showTotal: (t: number) => `共 ${t.toLocaleString('zh-CN')} 条`, onChange: (p: number, sz: number) => { if (sz !== pageSize) pageSize = sz; else page = p; } }"
          :row-class-name="(r: Row) => (r.id === selectedId ? 'al-row-active' : '')"
          :custom-row="(r: Row) => ({ onClick: () => pick(r) })">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'level'"><span :class="levelClass(record.level)" >{{ record.level }}</span></template>
            <template v-else-if="column.key === 'id'"><span class="num">{{ record.id }}</span></template>
            <template v-else-if="column.key === 'rule'">{{ RULES[record.ruleId].name }}</template>
            <template v-else-if="column.key === 'obj'">
              <ObjRef :kind="record.objKind" :id="record.objId" @click.stop>{{ record.objLabel }}</ObjRef>
            </template>
            <template v-else-if="column.key === 'value'"><span class="num">{{ record.value }}</span><span class="c-3"> / {{ record.threshold }}</span></template>
            <template v-else-if="column.key === 'triggeredAt'"><span class="num">{{ record.triggeredAt }}</span></template>
            <template v-else-if="column.key === 'status'"><a-badge :status="statusBadge(record.status)" :text="record.status" /></template>
            <template v-else-if="column.key === 'assignee'">{{ record.assignee ?? '--' }}</template>
          </template>
        </a-table>
      </div>
    </div>

    <div v-if="current && rule" class="panel al-detail">
      <div class="panel-head">
        <span class="t-16 fw-600">{{ rule.name }}</span>
        <a-badge :status="statusBadge(current.status)" :text="current.status" />
      </div>
      <div class="al-body">
        <div v-if="!inList" class="t-12 c-3 al-out">此告警已移出当前筛选（现为{{ current.status }}）</div>
        <a-descriptions :column="1" size="small" bordered :label-style="{ width: '96px' }">
          <a-descriptions-item label="告警编号"><span class="num">{{ current.id }}</span></a-descriptions-item>
          <a-descriptions-item label="对象">
            <ObjRef :kind="current.objKind" :id="current.objId">{{ current.objLabel }}</ObjRef>
          </a-descriptions-item>
          <a-descriptions-item label="触发值"><span class="num">{{ current.value }}</span><span class="c-3">，阈值 {{ current.threshold }}</span></a-descriptions-item>
          <a-descriptions-item label="规则">{{ rule.id }} {{ rule.version }} · {{ rule.trigger }}</a-descriptions-item>
          <a-descriptions-item v-if="metric" label="指标">
            <HoverCard kind="metric" :id="metric.id">{{ metric.name }}</HoverCard><span class="c-3">（口径 {{ metric.version }}）</span>
          </a-descriptions-item>
          <a-descriptions-item label="条件">{{ rule.condition }}</a-descriptions-item>
          <a-descriptions-item label="近 7 天">同一规则、同一对象第 {{ recent }} 次触发</a-descriptions-item>
          <a-descriptions-item label="去重">{{ rule.dedupe }}</a-descriptions-item>
          <a-descriptions-item label="数据截至"><span class="num">{{ current.cutoff }}</span></a-descriptions-item>
          <a-descriptions-item label="处理人">{{ current.assignee ?? '未认领' }}</a-descriptions-item>
          <a-descriptions-item v-if="current.actionRef" label="关联动作"><span class="num">{{ current.actionRef }}</span><span v-if="current.actionStatus === '结果未知'" class="tl-attention"> · <QuestionCircleOutlined /> 结果未知 · 待对账</span><span v-else class="c-3"> · {{ current.actionStatus }}</span></a-descriptions-item>
        </a-descriptions>

        <div class="t-14 fw-600 al-sub">处理记录</div>
        <a-timeline class="al-tl">
          <a-timeline-item v-for="(t, i) in current.log" :key="i" :color="t.abnormal ? 'red' : 'gray'">
            <div class="t-14">{{ t.text }}</div>
            <div class="t-12 c-3 num">{{ t.time }} · {{ t.who }}</div>
          </a-timeline-item>
        </a-timeline>
      </div>
      <div class="al-foot">
        <span v-if="readonly" class="t-14 c-3">{{ roReason }}，不能处理告警</span>
        <template v-else-if="!isClosed(current.status)">
          <span v-if="current.status === '处理中' && current.assignee !== ME" class="t-14 c-3">{{ current.assignee }} 处理中</span>
          <a-popover v-model:open="transferOpen" trigger="click" placement="topRight">
            <template #content>
              <div class="al-pop">
                <a-select v-model:value="transferTo" placeholder="转派给" :options="PEOPLE.filter((p) => p !== current!.assignee).map((p) => ({ value: p, label: p }))" />
                <div class="al-pop-foot"><a-button size="small" type="primary" :disabled="!transferTo" @click="doTransfer">转派</a-button></div>
              </div>
            </template>
            <a-tooltip :title="locked ? `关联动作${current.actionStatus}，有结果前不能转派` : undefined"><a-button :disabled="locked">转派</a-button></a-tooltip>
          </a-popover>
          <a-button v-if="current.status === '待处理'" type="primary" @click="claim">认领</a-button>
          <template v-if="mine">
          <a-popover v-model:open="closeOpen" trigger="click" placement="topRight">
            <template #content>
              <div class="al-pop">
                <a-radio-group v-model:value="closeAs"><a-radio value="已解决">已解决</a-radio><a-radio value="已忽略">已忽略</a-radio></a-radio-group>
                <a-textarea v-model:value="closeReason" :rows="3" placeholder="关闭原因（必填）" />
                <div class="al-pop-foot"><a-button size="small" type="primary" :disabled="!closeReason.trim()" @click="doClose">关闭告警</a-button></div>
              </div>
            </template>
            <a-tooltip :title="locked ? `关联动作${current.actionStatus}，有结果前不能关闭` : undefined"><a-button :disabled="locked">关闭</a-button></a-tooltip>
          </a-popover>
          <span v-if="current.actionRef" class="t-14 c-3">{{ rule.action }} · {{ current.actionStatus === '结果未知' ? '结果未知 · 待对账' : current.actionStatus }}</span>
          <a-button v-else-if="rule.action" type="primary" @click="runAction">{{ rule.action }}</a-button>
          </template>
        </template>
        <span v-else class="t-14 c-3">已关闭，处理人 {{ current.assignee }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.al { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.al-list { flex: 1; }
.al-filters { display: flex; gap: 8px; }
.al-table { flex: 1; min-height: 0; padding: 0 16px; }
.al-table :deep(.ant-table-body) { overflow-y: auto !important; }
.al-table :deep(.ant-table-row) { cursor: pointer; }
.al-table :deep(.al-row-active > td) { background: var(--brand-bg) !important; }
.al-detail { width: 480px; flex: none; }
.al-body { flex: 1; min-height: 0; padding: 12px 16px; }
.al-sub { margin: 16px 0 12px; }
.al-tl :deep(.ant-timeline-item) { padding-bottom: 12px; }
.al-out { margin-bottom: 8px; }
.al-pop { width: 280px; display: flex; flex-direction: column; gap: 8px; }
.al-pop-foot { display: flex; justify-content: flex-end; }
.al-act-state { align-self: center; }
.al-foot { flex: none; align-items: center; display: flex; justify-content: flex-end; gap: 8px; padding: 12px 16px; border-top: var(--border); }
</style>
