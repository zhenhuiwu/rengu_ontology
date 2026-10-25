<script setup lang="ts">
/**
 * 认证问答：问题 → 绑定指标与版本 → 语义计划摘要（不含 SQL）→ 状态 → 认证人。
 * 受变更单影响的条目显示“待重新认证 · 受 CR-xxxx 影响”。行点击打开抽屉（?cq=CQ-001）。
 * URL：?ckw=&cst=&cm=&cpage=
 */
import { computed, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { useConfirmStore } from '@/stores/confirm';
import { paginate } from '@/mock';
import { T_METRICS, certStatusText, metricLabel, planText, crById, type CertQa, type CertStatus } from '@/mock/qa-tuning';
import { tuning, NOW, ME } from './state';
import CrTag from './CrTag.vue';
import MetricRef from './MetricRef.vue';
import PlanView from './PlanView.vue';

defineProps<{ readonly: boolean; reason?: string }>();
const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });

const STATUSES: CertStatus[] = ['已认证', '待重新认证', '待认证', '已停用'];
const kw = computed({ get: () => q('ckw'), set: (v: string) => setQuery({ ckw: v || undefined }) });
const stF = computed({ get: () => q('cst') || undefined, set: (v?: string) => setQuery({ cst: v || undefined }) });
const mF = computed({ get: () => q('cm') || undefined, set: (v?: string) => setQuery({ cm: v || undefined }) });
const rank = (s: CertStatus) => (s === '待重新认证' ? 0 : s === '待认证' ? 1 : s === '已认证' ? 2 : 3);
const rows = computed(() => tuning.certs
  .filter((c) => (!kw.value || c.question.includes(kw.value) || c.variants.some((v) => v.includes(kw.value)))
    && (!stF.value || c.status === stF.value) && (!mF.value || c.plan.metric === mF.value))
  .slice().sort((a, b) => rank(a.status) - rank(b.status)));
const pq = usePagedQuery({ defaultSize: 15, filters: [kw, stF, mF], prefix: 'c' });
const paged = computed(() => paginate(rows.value, { page: pq.page.value, size: pq.size.value }));
const reCount = computed(() => tuning.certs.filter((c) => c.status === '待重新认证').length);

const cols = [
  { title: '编号', dataIndex: 'id', key: 'id', width: 80 },
  { title: '问题', key: 'question', width: 240, ellipsis: true },
  { title: '绑定指标', key: 'metric', width: 230 },
  { title: '语义计划摘要', key: 'plan', ellipsis: true },
  { title: '状态', key: 'status', width: 210 },
  { title: '认证人', dataIndex: 'certifier', key: 'certifier', width: 80 },
  { title: '认证时间', key: 'at', width: 120 },
  { title: '近 30 天命中', key: 'hits', width: 100, align: 'right' },
  { title: '操作', key: 'op', width: 130 },
];
const stCls = (s: CertStatus) => (s === '待重新认证' ? 'tl-attention' : s === '已停用' ? 'c-3' : s === '待认证' ? 'c-2' : 'c-1');
const planShort = (c: CertQa) => planText(c.plan).replace(`${metricLabel(c.plan.metric)} · `, '');

/* 抽屉 */
const cur = computed(() => tuning.certs.find((c) => c.id === q('cq')));
const open = (c: CertQa) => setQuery({ cq: c.id });
const rowProps = (c: CertQa) => ({ class: c.id === q('cq') ? 'qt-row-on' : '', onClick: () => open(c) });

async function certify(c: CertQa) {
  const cr = c.cr ? crById(c.cr) : undefined;
  const pending = cr && cr.status !== '已发布';
  const ok = await confirm.ask({
    title: c.status === '待认证' ? '认证问答' : '重新认证',
    target: `${c.id} ${c.question}`,
    fields: [{ label: '绑定指标', value: metricLabel(c.plan.metric) }, { label: '语义计划', value: planText(c.plan) }, { label: '认证人', value: ME }],
    impact: pending
      ? `${cr!.id}「${cr!.title}」仍是“${cr!.status}”。现在认证会按当前口径 ${T_METRICS[c.plan.metric].version} 锁定回答；变更发布后仍需再次认证。`
      : '认证后，相同或相近的问题直接按此语义计划回答，并在回答上标注“已认证”。',
    okText: '认证',
  });
  if (!ok) return;
  c.status = '已认证'; c.certifier = ME; c.certifiedAt = NOW;
  if (!pending) { c.cr = undefined; c.note = undefined; } else c.note = `已按 ${T_METRICS[c.plan.metric].version} 认证；${cr!.id} 发布后需再次认证`;
  if (pending) c.status = '待重新认证';
  message.success(pending ? `已按当前口径认证「${c.question}」，${cr!.id} 发布后会再次提醒` : `已认证「${c.question}」`);
}
async function toggle(c: CertQa) {
  const stop = c.status !== '已停用';
  const ok = await confirm.ask({
    title: stop ? '停用认证问答' : '启用认证问答', target: `${c.id} ${c.question}`, danger: stop, okText: stop ? '停用' : '启用',
    fields: [{ label: '近 30 天命中', value: `${c.hits} 次` }],
    impact: stop ? '停用后此问题回到常规理解流程，回答不再标注“已认证”。' : '启用后恢复为已认证。',
  });
  if (!ok) return;
  c.status = stop ? '已停用' : '已认证';
  message.success(`${stop ? '已停用' : '已启用'}「${c.question}」`);
}
/* 抽屉里加同问法 */
const newVar = ref('');
function addVariant(c: CertQa) {
  const v = newVar.value.trim(); if (!v) return;
  if (c.variants.includes(v)) { message.warning('已有相同问法'); return; }
  c.variants.push(v); newVar.value = ''; message.success(`已添加同问法「${v}」`);
}
const removeVariant = (c: CertQa, v: string) => { c.variants = c.variants.filter((x) => x !== v); };
</script>

<template>
  <div class="qt-filters">
    <a-input-search v-model:value="kw" allow-clear placeholder="搜索问题或同问法" class="qt-kw" />
    <a-select v-model:value="stF" allow-clear placeholder="状态" class="qt-sel" :options="STATUSES.map((s) => ({ value: s, label: s }))" />
    <a-select v-model:value="mF" allow-clear placeholder="绑定指标" class="qt-sel2" :options="Object.values(T_METRICS).map((m) => ({ value: m.key, label: `${m.name} ${m.version}` }))" />
    <span class="t-12 c-3 qt-hint">{{ reCount }} 条待重新认证；回答由本体查询模块按语义计划生成，问数不写 SQL</span>
  </div>
  <div class="qt-table" :class="{ 'paged-scroll': pq.overflow.value }">
    <a-table :locale="{ emptyText: '没有符合条件的认证问答' }" size="small" row-key="id" :columns="cols" :data-source="paged.items" :pagination="false" :custom-row="rowProps">
      <template #bodyCell="{ column, record }">
        <span v-if="column.key === 'id'" class="num c-3">{{ record.id }}</span>
        <span v-else-if="column.key === 'question'" class="c-1" :title="record.question">{{ record.question }}</span>
        <MetricRef v-else-if="column.key === 'metric'" :k="record.plan.metric" />
        <span v-else-if="column.key === 'plan'" class="c-2" :title="planText(record.plan)">{{ planShort(record) }}</span>
        <span v-else-if="column.key === 'status'" :class="stCls(record.status)">{{ certStatusText(record) }}</span>
        <span v-else-if="column.key === 'at'" class="num c-2">{{ record.certifiedAt.slice(5) }}</span>
        <span v-else-if="column.key === 'hits'" class="num">{{ record.hits }}</span>
        <span v-else-if="column.key === 'op'" class="qt-ops" @click.stop>
          <a-tooltip :title="reason"><a-button v-if="record.status === '待重新认证' || record.status === '待认证'" type="link" size="small" :disabled="readonly" @click="certify(record)">{{ record.status === '待认证' ? '认证' : '重新认证' }}</a-button></a-tooltip>
          <a-tooltip :title="reason"><a-button type="link" size="small" :disabled="readonly" @click="toggle(record)">{{ record.status === '已停用' ? '启用' : '停用' }}</a-button></a-tooltip>
        </span>
      </template>
    </a-table>
  </div>
  <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="paged.total" :default-size="pq.defaultSize" unit="条认证问答" :empty="false" class="qt-pager" />

  <a-drawer :open="!!cur" :width="640" :title="cur ? `${cur.id} ${cur.question}` : ''" @close="setQuery({ cq: undefined })">
    <template #extra>
      <a-space v-if="cur">
        <a-tooltip :title="reason"><a-button v-if="cur.status === '待重新认证' || cur.status === '待认证'" type="primary" :disabled="readonly" @click="certify(cur)">{{ cur.status === '待认证' ? '认证' : '重新认证' }}</a-button></a-tooltip>
        <a-tooltip :title="reason"><a-button :disabled="readonly" @click="toggle(cur)">{{ cur.status === '已停用' ? '启用' : '停用' }}</a-button></a-tooltip>
      </a-space>
    </template>
    <div v-if="cur" class="qd">
      <div class="qd-meta">
        <span class="t-12 c-3">状态</span><span class="t-14" :class="stCls(cur.status)">{{ certStatusText(cur) }}</span>
        <span class="t-12 c-3">认证人</span><span class="t-14 c-1">{{ cur.certifier }} · <span class="num">{{ cur.certifiedAt }}</span></span>
        <span class="t-12 c-3">来源</span><span class="t-14 c-1">{{ cur.source }}</span>
        <span class="t-12 c-3">命中</span><span class="t-14 c-1">近 30 天 <span class="num">{{ cur.hits }}</span> 次</span>
      </div>
      <div v-if="cur.cr || cur.note" class="qd-note">
        <CrTag v-if="cur.cr" :id="cur.cr" />
        <span class="t-12 c-warning">{{ cur.note }}</span>
      </div>
      <div class="qd-sec">
        <div class="t-14 fw-600 c-1">语义计划</div>
        <PlanView :plan="cur.plan" />
        <div class="t-12 c-3">本体查询模块按此计划生成达梦 DM8、金仓 KES 的查询；问数不写 SQL。</div>
      </div>
      <div class="qd-sec">
        <div class="t-14 fw-600 c-1">同问法（{{ cur.variants.length }}）</div>
        <div class="qd-vars">
          <a-tag v-for="v in cur.variants" :key="v" :closable="!readonly" @close.prevent="removeVariant(cur, v)">{{ v }}</a-tag>
          <span v-if="!cur.variants.length" class="t-12 c-3">还没有同问法</span>
        </div>
        <div class="qd-add">
          <a-input v-model:value="newVar" size="small" :disabled="readonly" placeholder="输入同问法，例如：上月各大区退款率" @press-enter="addVariant(cur)" />
          <a-tooltip :title="reason"><a-button size="small" :disabled="readonly || !newVar.trim()" @click="addVariant(cur)">添加</a-button></a-tooltip>
        </div>
      </div>
    </div>
  </a-drawer>
</template>

<style scoped src="./qt.css"></style>
