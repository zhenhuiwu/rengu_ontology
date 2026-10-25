<script setup lang="ts">
/**
 * 外部系统：动作写回用到的系统（CRM、WMS、短信、顺丰……）。
 * 左侧系统列表，右侧配置（地址、认证、幂等、超时 / 重试）+ 最近调用（simple 分页，按结果筛选）。
 * 调用结果颜色按时间线规则：成功 灰、结果未知 橙、失败 红。点“详情”打开调用抽屉（?call=），结果未知可按幂等键重试或人工核对。
 * URL：?sys=EXT-WMS&result=失败&call=WMS-00012
 */
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { paginate, EMPTY } from '@/mock';
import { EXT_SYSTEMS, extCalls, type CallResult, type ExtCall, type ExtSystem } from '@/mock/extsys';

const route = useRoute();
const router = useRouter();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const { readonly, reason } = useReadonly();

const selected = computed<ExtSystem>(() => EXT_SYSTEMS.find((s) => s.id === q('sys')) ?? EXT_SYSTEMS[0]);
// 状态点：正常 / 停用 灰，降级 橙（不用绿、蓝）
const statusBadge = (s: ExtSystem['status']) => (s === '降级' ? 'warning' : 'default');
const resultText = (r: CallResult) => (r === '结果未知' ? '结果未知 · 待对账' : r);

// 调用记录：本页内可改（重试 / 核对后结果会变）
const overrides = ref<Record<string, Partial<ExtCall>>>({});
const calls = computed(() => extCalls(selected.value).map((c) => ({ ...c, ...overrides.value[c.id] })));
const counts = computed(() => {
  const m: Record<string, number> = { 全部: calls.value.length, 成功: 0, 失败: 0, 结果未知: 0 };
  calls.value.forEach((c) => { m[c.result] += 1; });
  return m;
});
const result = computed({ get: () => q('result') || '全部', set: (v: string) => setQuery({ result: v === '全部' ? undefined : v }) });
const filtered = computed(() => calls.value.filter((c) => result.value === '全部' || c.result === result.value));
const SIZE = 10;
const page = ref(1);
watch([() => selected.value.id, result], () => { page.value = 1; });
const paged = computed(() => paginate(filtered.value, { page: page.value, size: SIZE }));
const tl = (r: CallResult) => (r === '成功' ? 'tl-normal' : r === '结果未知' ? 'tl-attention' : 'tl-failed');
const cols = [
  { title: '时间', key: 'time', width: 160 },
  { title: '动作', dataIndex: 'action', key: 'action', width: 130, ellipsis: true },
  { title: '对象', key: 'target', width: 200, ellipsis: true },
  { title: '发起人', dataIndex: 'actor', key: 'actor', width: 130, ellipsis: true },
  { title: '幂等键', key: 'idemKey', width: 130, ellipsis: true },
  { title: '耗时', key: 'ms', width: 80, align: 'right' as const },
  { title: '尝试', key: 'attempt', width: 50, align: 'right' as const },
  { title: 'HTTP', key: 'http', width: 60, align: 'right' as const },
  { title: '结果', key: 'result', width: 150 },
  { title: '操作', key: 'op', width: 60 },
];

// 调用详情抽屉
const call = computed(() => calls.value.find((c) => c.id === q('call')) ?? null);
const openCall = (id: string) => setQuery({ call: id });
const closeCall = () => setQuery({ call: undefined });
const reqBody = (c: ExtCall) => JSON.stringify({ action: c.action, target: c.target, operator: c.actor, ...(c.idemKey ? { [selected.value.idemKey.split('（')[0]]: c.idemKey } : {}) }, null, 2);
const respBody = (c: ExtCall) => (c.http == null ? `（${selected.value.timeoutMs / 1000} 秒内没有收到响应）` : JSON.stringify(c.http === 200 ? { code: 0, msg: 'success' } : { code: c.http, msg: c.http === 429 ? '请求过于频繁' : c.http === 401 ? '签名校验失败' : '服务暂时不可用' }, null, 2));

const confirm = useConfirmStore();
async function retry(c: ExtCall) {
  const ok = await confirm.ask({
    title: '用同一幂等键重试', target: `${c.action} · ${c.target}`, okText: '重试',
    fields: [{ label: '系统', value: `${selected.value.name}（${selected.value.vendor}）` }, { label: '幂等键', value: c.idemKey ?? EMPTY }, { label: '原调用', value: `${c.id} · ${c.time}` }],
    impact: `这次调用超时，系统没有自动重试。请先对账；确认要重试时，对方按幂等键 ${c.idemKey} 去重：上次已处理则只返回原结果，不会重复执行。`,
  });
  if (!ok) return;
  overrides.value[c.id] = { result: '成功', http: 200, ms: 236, attempt: c.attempt + 1, msg: '人工对账后重试成功：对方返回原处理结果（幂等命中）' };
  message.success('重试成功：对方已处理过，返回原结果');
}
async function markChecked(c: ExtCall) {
  const ok = await confirm.ask({
    title: '人工核对结果', target: `${c.action} · ${c.target}`, okText: '确认对方已处理', tag: '结果未知 · 待对账',
    fields: [{ label: '系统', value: `${selected.value.name}（${selected.value.vendor}）` }, { label: '原调用', value: `${c.id} · ${c.time}` }],
    impact: `${selected.value.name} 不支持幂等，从不重试。请先在对方系统确认这条请求已处理，再标记为成功；没处理的话请重新发起动作。`,
  });
  if (!ok) return;
  overrides.value[c.id] = { result: '成功', msg: '人工核对：对方已处理（王敏 16:30 标记）' };
  message.success('已标记为成功');
}
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly">新建外部系统</a-button></a-tooltip>
  </Teleport>
  <div class="ex">
    <div class="panel ex-list">
      <div class="panel-head">
        <span class="t-16 fw-600">外部系统</span>
        <span class="t-12 c-3">共 {{ EXT_SYSTEMS.length }} 个</span>
      </div>
      <div class="ex-items">
        <div v-for="s in EXT_SYSTEMS" :key="s.id" class="ex-item" :class="{ on: s.id === selected.id }" @click="setQuery({ sys: s.id, result: undefined, call: undefined })">
          <div class="ex-l1">
            <span class="ex-cat t-12">{{ s.category }}</span>
            <span class="t-14 fw-600 c-1 ex-ellip">{{ s.name }}</span>
            <a-badge :status="statusBadge(s.status)" :text="s.status" class="ex-badge" />
          </div>
          <div class="t-12 c-3 ex-l2">
            <span class="ex-ellip">{{ s.vendor }} · {{ s.env }}</span>
            <span>{{ s.idempotent ? '支持幂等' : '不支持幂等' }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="panel ex-detail">
      <div class="ex-head">
        <div class="ex-title">
          <span class="t-20 fw-600 c-1">{{ selected.name }}</span>
          <a-tag>{{ selected.category }}</a-tag>
          <a-tag>{{ selected.env }}</a-tag>
          <a-badge :status="statusBadge(selected.status)" :text="selected.status" />
          <span v-if="selected.status === '降级'" class="t-12 tl-attention">最近 6 次调用有失败 / 结果未知；结果未知不自动重试，需对账</span>
        </div>
        <a-space>
          <a-button>测试调用</a-button>
          <a-tooltip :title="reason"><a-button :disabled="readonly">编辑</a-button></a-tooltip>
        </a-space>
      </div>
      <div class="ex-cfg">
        <a-descriptions :column="3" bordered size="small" class="ex-desc">
          <a-descriptions-item label="接口地址" :span="2"><span class="num">{{ selected.endpoint }}</span></a-descriptions-item>
          <a-descriptions-item label="供应商">{{ selected.vendor }}</a-descriptions-item>
          <a-descriptions-item label="认证方式">{{ selected.auth }}</a-descriptions-item>
          <a-descriptions-item label="凭证" :span="2"><span class="num">{{ selected.authDetail }}</span></a-descriptions-item>
          <a-descriptions-item label="幂等">
            <span v-if="selected.idempotent">支持 · <span class="num">{{ selected.idemKey }}</span></span>
            <span v-else class="tl-attention">不支持（从不重试；结果未知需人工对账）</span>
          </a-descriptions-item>
          <a-descriptions-item label="超时"><span class="num">{{ selected.timeoutMs / 1000 }} 秒</span></a-descriptions-item>
          <a-descriptions-item label="限流">{{ selected.rateLimit }}</a-descriptions-item>
          <a-descriptions-item label="重试策略" :span="2">{{ selected.retry }}</a-descriptions-item>
          <a-descriptions-item label="负责人">{{ selected.owner }}</a-descriptions-item>
          <a-descriptions-item label="使用它的动作" :span="3">
            <a-tag v-for="a in selected.actions" :key="a">{{ a }}</a-tag>
            <span v-if="!selected.actions.length" class="c-3">没有动作使用</span>
          </a-descriptions-item>
        </a-descriptions>
      </div>
      <div class="ex-calls-head">
        <span class="t-16 fw-600">最近调用</span>
        <a-radio-group v-model:value="result" size="small" button-style="solid">
          <a-radio-button v-for="k in ['全部', '成功', '失败', '结果未知']" :key="k" :value="k">{{ k }}（{{ counts[k] }}）</a-radio-button>
        </a-radio-group>
      </div>
      <div class="ex-table paged-scroll">
        <a-table size="small" row-key="id" :columns="cols" :data-source="paged.items" :pagination="false">
          <template #bodyCell="{ column, record }">
            <span v-if="column.key === 'time'" class="num">{{ record.time }}</span>
            <span v-else-if="column.key === 'target'" class="num">{{ record.target }}</span>
            <span v-else-if="column.key === 'idemKey'" class="num c-3">{{ record.idemKey ?? EMPTY }}</span>
            <span v-else-if="column.key === 'ms'" class="num">{{ record.ms }} ms</span>
            <span v-else-if="column.key === 'attempt'" class="num c-2">{{ record.attempt }}</span>
            <span v-else-if="column.key === 'http'" class="num" :class="record.http && record.http !== 200 ? 'tl-failed' : ''">{{ record.http ?? EMPTY }}</span>
            <span v-else-if="column.key === 'result'" class="ex-res" :class="tl(record.result)"><QuestionCircleOutlined v-if="record.result === '结果未知'" /><span v-else class="ex-dot" />{{ resultText(record.result) }}</span>
            <a v-else-if="column.key === 'op'" @click="openCall(record.id)">详情</a>
          </template>
        </a-table>
      </div>
      <AppPagination v-model:page="page" variant="simple" :total="filtered.length" :size="SIZE" unit="次调用" class="ex-pager" />
    </div>
  </div>

  <a-drawer :open="!!call" :width="560" :title="call ? `调用详情 · ${call.id}` : ''" @close="closeCall">
    <template v-if="call">
      <div class="ex-dr-status" :class="tl(call.result)"><QuestionCircleOutlined v-if="call.result === '结果未知'" /><span v-else class="ex-dot" /><span class="t-16 fw-600">{{ resultText(call.result) }}</span></div>
      <div class="t-12 c-2 ex-dr-msg">{{ call.msg }}</div>
      <a-descriptions :column="1" bordered size="small" class="ex-desc">
        <a-descriptions-item label="系统">{{ selected.name }}（{{ selected.vendor }}）</a-descriptions-item>
        <a-descriptions-item label="动作">{{ call.action }}</a-descriptions-item>
        <a-descriptions-item label="对象"><span class="num">{{ call.target }}</span></a-descriptions-item>
        <a-descriptions-item label="发起人">{{ call.actor }}</a-descriptions-item>
        <a-descriptions-item label="时间"><span class="num">{{ call.time }}</span></a-descriptions-item>
        <a-descriptions-item label="请求"><span class="num">{{ call.method }} {{ call.path }}</span></a-descriptions-item>
        <a-descriptions-item label="幂等键"><span class="num">{{ call.idemKey ?? '不支持' }}</span></a-descriptions-item>
        <a-descriptions-item label="耗时 / 尝试"><span class="num">{{ call.ms }} ms · 第 {{ call.attempt }} 次</span></a-descriptions-item>
      </a-descriptions>
      <div class="t-14 fw-600 c-1 ex-dr-sub">请求体</div>
      <pre class="ex-pre num">{{ reqBody(call) }}</pre>
      <div class="t-14 fw-600 c-1 ex-dr-sub">响应</div>
      <pre class="ex-pre num">{{ respBody(call) }}</pre>
    </template>
    <template v-if="call && call.result === '结果未知'" #footer>
      <div class="ex-foot">
        <a-tooltip :title="reason">
          <a-button v-if="call.idemKey" type="primary" :disabled="readonly" @click="retry(call)">用同一幂等键重试</a-button>
          <a-button v-else type="primary" :disabled="readonly" @click="markChecked(call)">人工核对</a-button>
        </a-tooltip>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped>
.ex { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.ex-list { width: 320px; flex: none; }
.ex-items { flex: 1; min-height: 0; overflow: hidden; }
.ex-item { padding: 10px 16px; border-bottom: var(--border); border-left: 3px solid transparent; cursor: pointer; }
.ex-item:hover { background: var(--g2); }
.ex-item.on { background: var(--brand-bg); border-left-color: var(--brand); }
.ex-l1 { display: flex; align-items: center; gap: 8px; min-width: 0; }
.ex-l2 { display: flex; justify-content: space-between; margin-top: 2px; }
.ex-cat { flex: none; min-width: 36px; text-align: center; border: var(--border); border-radius: 4px; color: var(--g8); line-height: 18px; padding: 0 4px; }
.ex-ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.ex-badge { margin-left: auto; flex: none; }
.ex-detail { flex: 1; min-width: 0; }
.ex-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px 10px; }
.ex-title { display: flex; align-items: center; gap: 10px; }
.ex-cfg { padding: 0 20px; flex: none; }
.ex-desc :deep(.ant-descriptions-item-label) { width: 120px; }
.ex-calls-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px 8px; flex: none; }
.ex-table { flex: 1; min-height: 0; padding: 0 20px; }
.ex-pager { padding: 6px 20px 8px; }
.ex-res { display: inline-flex; align-items: center; gap: 6px; }
.ex-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.ex-dr-status { display: flex; align-items: center; gap: 8px; }
.ex-dr-msg { margin: 4px 0 12px 16px; }
.ex-dr-sub { margin: 16px 0 6px; }
.ex-pre { background: var(--g2); border: var(--border); border-radius: var(--radius); padding: 8px 12px; margin: 0; color: var(--g9); white-space: pre-wrap; }
.ex-foot { display: flex; justify-content: flex-end; }
</style>
