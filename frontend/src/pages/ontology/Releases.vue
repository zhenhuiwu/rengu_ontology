<script setup lang="ts">
/**
 * 变更与发布：左侧变更单列表（简洁分页），右侧变更单详情。
 * 页签：改动清单 / 发布校验 / 影响分析 / 评审记录。整版发布：已通过的变更单合并校验后一起发布为下一个版本。
 * 选中变更单写进 ?cr=，页签写进 ?tab=，状态筛选写进 ?st=。查看历史版本时整页只读。
 * 权限：只有该单的审核人能通过 / 驳回，提交人不能审自己的单子；所有状态流转都走确认卡并写进评审记录。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import HoverCard from '@/components/HoverCard.vue';
import StatusTimeline from '@/components/StatusTimeline.vue';
import { useReadonly } from '@/composables/useReadonly';
import { useConfirmStore } from '@/stores/confirm';
import { useAppStore } from '@/stores/app';
import {
  CHANGE_REQUESTS, CURRENT_USER, CURRENT_VERSION, NEXT_VERSION, checkCounts, checkSummary, crDetail, editRoute, impactSummary, mergeCheck, reviewLog,
  type ChangeRequest, type CheckResult, type CRStatus, type ReviewEvent,
} from '@/mock/changes';

const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const app = useAppStore();
const { readonly, reason } = useReadonly();
const me = computed(() => app.user?.name || CURRENT_USER);

const CRS = reactive<ChangeRequest[]>(CHANGE_REQUESTS.map((c) => ({ ...c })));
const extraLog = reactive<Record<string, ReviewEvent[]>>({});
const rolledBack = ref(false);

const STATUSES = ['全部', '草稿', '审核中', '已通过', '已发布', '已驳回'];
const st = computed({
  get: () => (route.query.st as string) || '全部',
  set: (v) => router.replace({ query: { ...route.query, st: v === '全部' ? undefined : v } }),
});
const kw = ref('');
const lpage = ref(1);
const LPAGE = 11;
watch([st, kw], () => { lpage.value = 1; });
const filtered = computed(() => CRS.filter((c) => (st.value === '全部' || c.status === st.value) && (!kw.value || c.title.includes(kw.value) || c.id.includes(kw.value.toUpperCase()))));
const list = computed(() => filtered.value.slice((lpage.value - 1) * LPAGE, lpage.value * LPAGE));

const cur = computed(() => CRS.find((c) => c.id === ((route.query.cr as string) || 'CR-0010')) ?? CRS[0]);
const pick = (id: string) => router.replace({ query: { ...route.query, cr: id } });
// 选中的单子不在当前页时，翻到它所在的页
watch(() => cur.value.id, (id) => { const i = filtered.value.findIndex((c) => c.id === id); if (i >= 0) lpage.value = Math.floor(i / LPAGE) + 1; }, { immediate: true });
const tab = computed({
  get: () => (route.query.tab as string) || 'diff',
  set: (v) => router.replace({ query: { ...route.query, tab: v } }),
});
const detail = computed(() => crDetail(cur.value.id));
const log = computed(() => [...(extraLog[cur.value.id] ?? []), ...reviewLog(cur.value)]);

const statusClass = (s: CRStatus) => (s === '审核中' ? 'c-warning' : s === '已驳回' ? 'c-error' : 'c-3');
const tagColor = (s: CRStatus) => ({ 草稿: 'default', 审核中: 'warning', 已通过: 'default', 已发布: 'default', 已驳回: 'error' })[s];
const resultClass = (r: CheckResult) => (r === '阻断' ? 'c-error' : r === '警告' ? 'c-warning' : r === '通过' ? 'c-2' : 'c-3');

const counts = computed(() => checkCounts(detail.value.checks));
const blocked = computed(() => counts.value['阻断'] > 0);
const impactSum = computed(() => impactSummary(detail.value));

// —— 当前用户能做什么 ——
const isOwner = computed(() => cur.value.owner === me.value);
const isReviewer = computed(() => cur.value.reviewer === me.value && !isOwner.value);
const canManage = computed(() => isOwner.value || cur.value.reviewer === me.value);
const editTo = computed(() => editRoute(detail.value.edit));
const latestRelease = computed(() => CRS.find((c) => c.status === '已发布')?.release);
const isLatest = computed(() => !rolledBack.value && cur.value.release === latestRelease.value && cur.value.release === CURRENT_VERSION);
/** 详情区有没有蓝色主按钮：有就把页头“发布”降为普通按钮，同屏只留一个主按钮 */
const detailPrimary = computed(() => !readonly.value && (cur.value.status === '草稿' || (cur.value.status === '审核中' && isReviewer.value)));

// —— 改动清单（简洁分页）——
const dpage = ref(1);
const DPAGE = 10;
watch(() => cur.value.id, () => { dpage.value = 1; });
const drows = computed(() => detail.value.diffs.slice((dpage.value - 1) * DPAGE, dpage.value * DPAGE));
const diffCols = [
  { title: '类型', dataIndex: 'type', width: 96 },
  { title: '对象', dataIndex: 'target', width: 200 },
  { title: '内容', dataIndex: 'field', width: 220 },
  { title: '改动', key: 'kind', width: 80 },
  { title: '原值', key: 'before' },
  { title: '新值', key: 'after' },
];
const checkCols = [
  { title: '编号', dataIndex: 'code', width: 72 },
  { title: '校验项', dataIndex: 'name', width: 220 },
  { title: '结果', key: 'result', width: 88 },
  { title: '说明', dataIndex: 'note' },
];
const trialCols = [
  { title: '月份', dataIndex: 'period' },
  { title: '旧口径', dataIndex: 'before', align: 'right' as const },
  { title: '新口径', dataIndex: 'after', align: 'right' as const },
  { title: '差异', dataIndex: 'delta', align: 'right' as const },
];

// —— 状态流转：全部走确认卡，写进评审记录，记实际操作人 ——
const pad = (n: number) => String(n).padStart(2, '0');
const now = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const target = () => `${cur.value.id} ${cur.value.title}`;
function move(to: CRStatus, text: string, desc?: string, status?: string) {
  const c = cur.value;
  c.status = to;
  c.updatedAt = now();
  (extraLog[c.id] ??= []).unshift({ time: now(), text: `${me.value} ${text}`, desc, status });
}
async function submit() {
  const ok = await confirm.ask({ title: '提交审核', target: target(), okText: '提交',
    fields: [{ label: '审核人', value: cur.value.reviewer }, { label: '改动', value: `${cur.value.changes} 处` }, { label: '发布校验', value: checkSummary(detail.value.checks) || '暂无' }],
    impact: '提交后草稿锁定；审核人驳回或你撤回提交后才能继续修改。' });
  if (ok) { move('审核中', '提交审核', `审核人 ${cur.value.reviewer}`); message.success(`${cur.value.id} 已提交审核`); }
}
function edit() { if (editTo.value) router.push(editTo.value); }
async function removeDraft() {
  const ok = await confirm.ask({ title: '删除草稿', target: target(), okText: '删除', danger: true,
    fields: [{ label: '改动', value: `${cur.value.changes} 处` }],
    impact: '删除后草稿里的改动全部丢弃，不能恢复；已发布版本不受影响。' });
  if (!ok) return;
  const id = cur.value.id;
  CRS.splice(CRS.findIndex((c) => c.id === id), 1);
  message.success(`${id} 已删除`);
  pick(CRS.find((c) => c.status === '草稿')?.id ?? CRS[0].id);
}
async function recall() {
  const ok = await confirm.ask({ title: '撤回提交', target: target(), okText: '撤回',
    impact: `撤回后回到草稿，${cur.value.reviewer} 不再看到这张审核。` });
  if (ok) move('草稿', '撤回提交');
}
async function approve() {
  const ok = await confirm.ask({ title: '审核通过', target: target(), okText: '通过',
    fields: [{ label: '提交人', value: cur.value.owner }, { label: '发布校验', value: checkSummary(detail.value.checks) }, { label: '目标版本', value: NEXT_VERSION }],
    impact: '通过后进入待发布，和其他已通过的变更单一起整版发布。' });
  if (ok) move('已通过', '审核通过', `等待整版发布 ${NEXT_VERSION}`);
}
async function reject() {
  const ok = await confirm.ask({ title: '驳回', target: target(), okText: '驳回', danger: true,
    fields: [{ label: '提交人', value: cur.value.owner }],
    picker: { label: '驳回原因', required: true, options: ['口径与财务不一致', '影响范围没写清楚', '迁移方式不完整', '与其他变更单冲突', '权限或可见范围未定'].map((v) => ({ value: v, label: v })) } });
  if (ok) { cur.value.rejectReason = confirm.pickValue; move('已驳回', '驳回', confirm.pickValue, 'error'); }
}
async function unapprove() {
  const ok = await confirm.ask({ title: '撤回', target: target(), okText: '撤回', danger: true,
    impact: `撤回后回到草稿，不会进入 ${NEXT_VERSION}；修改后需要重新提交审核。` });
  if (ok) move('草稿', '撤回审核结果', `不进入 ${NEXT_VERSION}`);
}
async function reopen() {
  const ok = await confirm.ask({ title: '改回草稿', target: target(), okText: '改回草稿',
    fields: [{ label: '驳回原因', value: cur.value.rejectReason ?? '暂无' }],
    impact: '改回草稿后可以按驳回意见修改，再重新提交审核。' });
  if (ok) move('草稿', '改回草稿', '按驳回意见修改后重新提交');
}
const approved = computed(() => CRS.filter((c) => c.status === '已通过'));
async function publish() {
  const m = mergeCheck(approved.value);
  const verdict = m.rows.map((r) => ({ label: `${r.code} ${r.result}`, value: `${r.name}：${r.note}` }));
  const ok = await confirm.ask({ title: `整版发布 ${NEXT_VERSION}`, target: `本体（当前 ${CURRENT_VERSION}）`, okText: '发布',
    tag: m.blocked ? '合并校验有阻断' : '合并校验通过',
    fields: [...approved.value.map((c) => ({ label: c.id, value: c.title })), ...verdict],
    impact: m.blocked
      ? '合并校验有阻断，不能发布。请把冲突的变更单撤回或合并后再发布。'
      : '发布后新提问按新版本回答；已有答案、页面和告警保留各自的口径版本。可回滚到上一版本。',
    // 有阻断时用必选项锁住确认按钮（确认卡没有单独的禁用参数）
    picker: m.blocked ? { label: '发布确认', placeholder: '合并校验有阻断，不能发布', options: [], required: true } : undefined });
  if (ok && !m.blocked) {
    approved.value.forEach((c) => { c.status = '已发布'; c.release = NEXT_VERSION; c.updatedAt = now(); (extraLog[c.id] ??= []).unshift({ time: now(), text: `${me.value} 整版发布为 ${NEXT_VERSION}`, desc: `合并校验：${m.rows.filter((r) => r.result !== '通过').map((r) => r.note).join('；') || '全部通过'}` }); });
    message.success(`已发布 ${NEXT_VERSION}`);
  }
}
async function rollback() {
  const prev = CRS.find((c) => c.status === '已发布' && c.release !== cur.value.release)?.release;
  const ok = await confirm.ask({ title: `回滚 ${cur.value.release}`, target: `本体 ${cur.value.release}`, okText: '回滚', danger: true,
    fields: [{ label: '回滚到', value: prev ?? '上一版本' }, { label: '涉及变更单', value: CRS.filter((c) => c.release === cur.value.release).map((c) => c.id).join('、') }],
    impact: '回滚会生成一个新版本，内容等于上一版本；只能回滚最近一次发布。' });
  if (ok) { rolledBack.value = true; (extraLog[cur.value.id] ??= []).unshift({ time: now(), text: `${me.value} 回滚 ${cur.value.release}`, desc: `生成新版本，内容同 ${prev}`, status: 'warn' }); message.success(`已回滚 ${cur.value.release}`); }
}
</script>

<template>
  <Teleport defer to="#page-extra">
    <span class="t-12 c-3">待发布 {{ approved.length }} 张</span>
    <a-tooltip :title="readonly ? reason : approved.length ? '' : '没有已通过的变更单'">
      <a-button :type="detailPrimary ? 'default' : 'primary'" :disabled="readonly || !approved.length" @click="publish">发布 {{ NEXT_VERSION }}</a-button>
    </a-tooltip>
  </Teleport>

  <div class="rl">
    <div class="panel rl-list">
      <div class="rl-filter">
        <a-input-search v-model:value="kw" placeholder="搜索标题或变更单号" class="rl-kw" />
        <a-select v-model:value="st" :options="STATUSES.map((v) => ({ value: v, label: v === '全部' ? '全部状态' : v }))" class="rl-st" />
      </div>
      <ul class="rl-items">
        <li v-for="c in list" :key="c.id" :class="{ active: c.id === cur.id }" @click="pick(c.id)">
          <div class="rl-row">
            <span class="t-14 rl-name">{{ c.title }}</span>
            <span class="t-12" :class="statusClass(c.status)">{{ c.status }}</span>
          </div>
          <div class="rl-row">
            <span class="t-12 c-3 num">{{ c.id }} · {{ c.owner }} · {{ c.updatedAt.slice(5) }}</span>
            <span class="t-12 c-3 num">{{ c.release ?? c.level }}</span>
          </div>
        </li>
      </ul>
      <div class="rl-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="filtered.length" :size="LPAGE" empty-text="没有符合条件的变更单" />
      </div>
    </div>

    <div class="panel rl-detail">
      <div class="rl-head">
        <span class="t-16 fw-600 c-1 rl-title">{{ cur.title }}</span>
        <span class="t-14 c-3 num">{{ cur.id }}</span>
        <a-tag :color="tagColor(cur.status)">{{ cur.status }}</a-tag>
        <a-tag>{{ cur.level }}</a-tag>
        <span class="rl-sp" />
        <template v-if="readonly">
          <span class="t-12 c-3">{{ reason }}</span>
        </template>
        <template v-else-if="cur.status === '草稿'">
          <a-tooltip :title="isOwner ? '' : `只有提交人 ${cur.owner} 能删除`">
            <a-button danger :disabled="!isOwner" @click="removeDraft">删除草稿</a-button>
          </a-tooltip>
          <a-tooltip :title="editTo ? '' : '这张单子没有可跳转的草稿页面'">
            <a-button :disabled="!editTo" @click="edit">编辑</a-button>
          </a-tooltip>
          <a-tooltip :title="blocked ? `有 ${counts['阻断']} 项阻断未通过` : ''">
            <a-button type="primary" :disabled="blocked" @click="submit">提交审核</a-button>
          </a-tooltip>
        </template>
        <template v-else-if="cur.status === '审核中'">
          <template v-if="isReviewer">
            <a-button danger @click="reject">驳回</a-button>
            <a-tooltip :title="blocked ? `有 ${counts['阻断']} 项阻断未通过` : ''">
              <a-button type="primary" :disabled="blocked" @click="approve">审核通过</a-button>
            </a-tooltip>
          </template>
          <template v-else>
            <span class="t-12 c-3">等待 {{ cur.reviewer }} 审核</span>
            <a-button v-if="isOwner" @click="recall">撤回提交</a-button>
          </template>
        </template>
        <template v-else-if="cur.status === '已通过'">
          <span class="t-12 c-3">等待整版发布 {{ NEXT_VERSION }}</span>
          <a-tooltip :title="canManage ? '' : `只有 ${cur.owner} 或 ${cur.reviewer} 能撤回`">
            <a-button :disabled="!canManage" @click="unapprove">撤回</a-button>
          </a-tooltip>
        </template>
        <template v-else-if="cur.status === '已驳回'">
          <span class="t-12 c-error rl-why">驳回：{{ cur.rejectReason ?? '暂无原因' }}</span>
          <a-tooltip :title="canManage ? '' : `只有 ${cur.owner} 或 ${cur.reviewer} 能改回草稿`">
            <a-button :disabled="!canManage" @click="reopen">改回草稿</a-button>
          </a-tooltip>
        </template>
        <template v-else-if="cur.status === '已发布'">
          <span class="t-12 c-3 num">已发布为 {{ cur.release }}</span>
          <a-tooltip :title="isLatest ? '' : rolledBack && cur.release === CURRENT_VERSION ? '已回滚' : '只能回滚最近一次发布'"><a-button danger :disabled="!isLatest" @click="rollback">回滚</a-button></a-tooltip>
        </template>
      </div>
      <div class="rl-meta t-12 c-3 num">
        <span>提交人 {{ cur.owner }}</span><span>审核人 {{ cur.reviewer }}</span><span>改动 {{ cur.changes }} 处</span>
        <span>更新于 {{ cur.updatedAt }}</span><span class="rl-reason">原因：{{ cur.reason }}</span>
      </div>
      <a-tabs v-model:activeKey="tab" class="rl-tabs">
        <a-tab-pane key="diff" tab="改动清单" />
        <a-tab-pane key="checks" :tab="`发布校验${counts['阻断'] ? ' · 阻断 ' + counts['阻断'] : ''}`" />
        <a-tab-pane key="impact" tab="影响分析" />
        <a-tab-pane key="log" tab="评审记录" />
      </a-tabs>

      <!-- 改动清单 -->
      <div v-if="tab === 'diff'" class="rl-body">
        <a-table :columns="diffCols" :data-source="drows" :pagination="false" size="small" row-key="key" :locale="{ emptyText: '暂无改动' }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'kind'"><a-tag>{{ record.kind }}</a-tag></template>
            <template v-else-if="column.key === 'before'"><span v-if="record.before" class="c-3 rl-old">{{ record.before }}</span><span v-else class="c-3">--</span></template>
            <template v-else-if="column.key === 'after'"><span class="c-1">{{ record.after }}</span></template>
          </template>
        </a-table>
        <AppPagination v-model:page="dpage" variant="simple" :total="detail.diffs.length" :size="DPAGE" :empty="false" class="rl-pager" />
        <div class="rl-note t-14">
          <span class="fw-600 c-1 rl-note-k">迁移方式</span>
          <span :class="detail.migration ? 'c-2' : 'c-3'">{{ detail.migration ?? (cur.level === '重大' ? '暂无，重大改动提交前需要写明' : '暂无，本次改动不涉及迁移') }}</span>
        </div>
        <div class="rl-note rl-note-link t-14" @click="tab = 'impact'">
          <span class="fw-600 c-1 rl-note-k">影响摘要</span>
          <span class="c-2 num">影响 {{ impactSum.metrics }} 个指标、{{ impactSum.qa }} 条认证问答、{{ impactSum.evals }} 道评测题</span>
          <span class="t-12 c-3 num">发布校验：{{ checkSummary(detail.checks) || '暂无' }}</span>
          <span class="rl-sp" /><span class="t-12 c-3">查看影响分析 ›</span>
        </div>
      </div>

      <!-- 发布校验：固定 17 项，不分页 -->
      <div v-else-if="tab === 'checks'" class="rl-body">
        <div class="rl-sum t-14">
          <span class="c-error">阻断 {{ counts['阻断'] }}</span><span class="c-warning">警告 {{ counts['警告'] }}</span>
          <span v-if="counts['信息']" class="c-3">信息 {{ counts['信息'] }}</span>
          <span class="c-2">通过 {{ counts['通过'] }}</span><span class="c-3">不适用 {{ counts['不适用'] }}</span>
          <span class="rl-sp" /><span class="t-12 c-3 num">校验于 {{ detail.times.checked ?? '--' }} · 目标库 达梦 DM8、金仓 KES</span>
          <a-button size="small" :disabled="readonly || cur.status === '已发布'">重新校验</a-button>
        </div>
        <a-table :columns="checkCols" :data-source="detail.checks" :pagination="false" size="small" row-key="code" :locale="{ emptyText: '暂无校验结果' }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'result'"><span :class="resultClass(record.result)">{{ record.result }}</span></template>
          </template>
        </a-table>
      </div>

      <!-- 影响分析 -->
      <div v-else-if="tab === 'impact'" class="rl-body rl-impact">
        <div class="rl-cols">
          <div v-for="col in [{ k: 'direct', t: '直接改动' }, { k: 'defs', t: '受影响的定义' }, { k: 'usage', t: '下游使用' }]" :key="col.k" class="rl-col">
            <div class="t-14 fw-600 c-1 rl-col-title">{{ col.t }}</div>
            <div v-for="it in (detail.impact as any)[col.k]" :key="it.name" class="rl-card">
              <div class="t-12 c-3">{{ it.kind }}</div>
              <div class="t-14 c-1"><HoverCard v-if="it.hover" :kind="it.hover.kind" :id="it.hover.id">{{ it.name }}</HoverCard><template v-else>{{ it.name }}</template></div>
              <div class="t-12 c-3">{{ it.note }}</div>
            </div>
            <div v-if="!(detail.impact as any)[col.k].length" class="t-12 c-3">暂无</div>
          </div>
        </div>
        <div class="rl-trial">
          <div class="t-14 fw-600 c-1 rl-col-title">{{ detail.trial?.title ?? '试跑对比' }}</div>
          <a-table v-if="detail.trial" :columns="trialCols" :data-source="detail.trial.rows" :pagination="false" size="small" row-key="period" />
          <div v-else class="t-12 c-3">暂无：本次改动不涉及指标口径，没有试跑。</div>
        </div>
      </div>

      <!-- 评审记录 -->
      <div v-else class="rl-body rl-log">
        <StatusTimeline :items="log" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.rl { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.rl-list { width: 360px; flex: none; }
.rl-filter { padding: 12px 12px 8px; display: flex; gap: 8px; flex: none; }
.rl-kw { flex: 1; }
.rl-st { width: 112px; flex: none; }
.rl-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; overflow: hidden; }
.rl-items li { padding: 8px; border-radius: var(--radius); cursor: pointer; }
.rl-items li:hover { background: var(--g3); }
.rl-items li.active { background: var(--brand-bg); }
.rl-items li.active .rl-name { color: var(--brand); }
.rl-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.rl-name { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.rl-foot { padding: 8px 12px 8px 16px; border-top: var(--border); flex: none; }
.rl-detail { flex: 1; }
.rl-head { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; flex: none; }
.rl-head :deep(.ant-tag) { margin-inline-end: 0; }
.rl-sp { flex: 1; }
.rl-meta { display: flex; gap: 24px; padding: 0 16px 8px; flex: none; }
.rl-reason { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; min-width: 0; }
.rl-tabs { padding: 0 16px; flex: none; }
.rl-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.rl-body { padding: 16px; flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
.rl-old { text-decoration: line-through; }
.rl-pager { margin-top: 0; }
.rl-note { display: flex; align-items: center; gap: 12px; padding: 12px; background: var(--g2); border-radius: var(--radius); flex: none; }
.rl-note-k { width: 64px; flex: none; }
.rl-note-link { cursor: pointer; }
.rl-note-link:hover { background: var(--g3); }
.rl-title { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; min-width: 0; }
.rl-why { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; max-width: 420px; }
.rl-sum { display: flex; align-items: center; gap: 16px; }
.rl-cols { display: flex; gap: 16px; }
.rl-col { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.rl-col-title { margin-bottom: 4px; }
.rl-card { border: var(--border); border-radius: var(--radius); padding: 8px 12px; display: flex; flex-direction: column; gap: 2px; }
.rl-trial { width: 50%; }
.rl-log { max-width: 720px; }
</style>
