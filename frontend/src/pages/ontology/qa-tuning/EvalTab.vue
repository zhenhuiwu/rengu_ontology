<script setup lang="ts">
/**
 * 评测题库：运行记录（通过率）+ 题目两个视图（?ev=questions）。
 * 运行记录行点击 → 失败用例抽屉（?run=ER-0023，simple 分页）。
 * 待运行：ER-0025 v2026.10.4 发布前回放 5 题（CR-0008 + CR-0015）、ER-0024 CR-0010 退款相关 4 题；“CR-0008 · 履约相关 3 题”试跑全部通过（召回率预计提升）。
 */
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { paginate } from '@/mock';
import { EVAL_QS, EVAL_GROUPS, planText, passRate, type EvalRun, type EvalQ, type FailKind } from '@/mock/qa-tuning';
import { tuning } from './state';
import CrTag from './CrTag.vue';

defineProps<{ readonly: boolean; reason?: string }>();
const route = useRoute();
const router = useRouter();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });

const view = computed({ get: () => (q('ev') === 'questions' ? 'questions' : 'runs'), set: (v: string) => setQuery({ ev: v === 'questions' ? 'questions' : undefined }) });

/* 运行记录 */
const trigF = computed({ get: () => q('etr') || undefined, set: (v?: string) => setQuery({ etr: v || undefined }) });
const TRIGGERS = ['每日定时', '手动运行', '发布前回放', '发布后回放', '审核通过后试跑'];
const runs = computed(() => tuning.runs.filter((r) => !trigF.value || r.trigger.includes(trigF.value)));
const rq = usePagedQuery({ defaultSize: 15, filters: [trigF], prefix: 'e' });
const rpaged = computed(() => paginate(runs.value, { page: rq.page.value, size: rq.size.value }));
const rcols = [
  { title: '编号', key: 'id', width: 90 },
  { title: '时间', key: 'time', width: 140 },
  { title: '触发', key: 'trigger', width: 300 },
  { title: '本体版本', key: 'version', width: 150 },
  { title: '范围', dataIndex: 'scope', key: 'scope', width: 150 },
  { title: '通过 / 题数', key: 'passed', width: 110, align: 'right' },
  { title: '通过率', key: 'rate', width: 90, align: 'right' },
  { title: '失败', key: 'fails', width: 70, align: 'right' },
  { title: '状态', key: 'status', width: 110 },
  { title: '执行人', dataIndex: 'operator', key: 'operator', width: 80 },
  { title: '说明', key: 'note', ellipsis: true },
];
const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
const latest = computed(() => tuning.runs.find((r) => r.status === '已完成' && r.scope.startsWith('全部')));

/* 题目 */
const gF = computed({ get: () => q('eg') || undefined, set: (v?: string) => setQuery({ eg: v || undefined }) });
const resF = computed({ get: () => q('er') || undefined, set: (v?: string) => setQuery({ er: v || undefined }) });
const ekw = computed({ get: () => q('ekw'), set: (v: string) => setQuery({ ekw: v || undefined }) });
const qs = computed(() => EVAL_QS.filter((x) => (!gF.value || x.group === gF.value) && (!resF.value || x.last === resF.value) && (!ekw.value || x.question.includes(ekw.value))));
const qq = usePagedQuery({ defaultSize: 15, filters: [gF, resF, ekw], prefix: 'q' });
const qpaged = computed(() => paginate(qs.value, { page: qq.page.value, size: qq.size.value }));
const qcols = [
  { title: '编号', key: 'id', width: 80 },
  { title: '题目', key: 'question', width: 260, ellipsis: true },
  { title: '分组', key: 'group', width: 90 },
  { title: '期望语义计划', key: 'expect', ellipsis: true },
  { title: '关联变更', key: 'cr', width: 150 },
  { title: '最近结果', key: 'last', width: 90 },
];
const groupCount = (g: string) => EVAL_QS.filter((x) => x.group === g).length;

/* 抽屉 */
const cur = computed(() => tuning.runs.find((r) => r.id === q('run')));
const rowProps = (r: EvalRun) => ({ class: r.id === q('run') ? 'qt-row-on' : '', onClick: () => setQuery({ run: r.id }) });
const FSIZE = 6;
const fpage = ref(1);
watch(() => cur.value?.id, () => { fpage.value = 1; });
const fpaged = computed(() => paginate(cur.value?.fails ?? [], { page: fpage.value, size: FSIZE }));
const kinds = computed(() => {
  const m = new Map<FailKind, number>();
  (cur.value?.fails ?? []).forEach((f) => m.set(f.kind, (m.get(f.kind) ?? 0) + 1));
  return [...m.entries()];
});
/** 待运行的发布前回放：列出将要回放的题 */
const crCount = (id: string) => EVAL_QS.filter((x) => x.cr === id).length;
const crsOf = (r: EvalRun) => r.crs ?? (r.cr ? [r.cr] : []);
const replayQs = computed<EvalQ[]>(() => {
  const ids = cur.value?.status === '待运行' ? crsOf(cur.value) : [];
  return EVAL_QS.filter((x) => x.cr && ids.includes(x.cr));
});
const REPLAY_HINT: Record<string, string> = {
  'CR-0010': '按 退款率 v3（退款完成时间）重新比对',
  'CR-0008': '同义词“发货及时率”生效后应召回 出库及时率 v1',
  'CR-0015': '同义词“成交单量”生效后应召回 有效订单数 v1',
};
const trigText = (r: EvalRun) => crsOf(r).reduce((t, id) => t.replace(id + ' ', ''), r.trigger);
</script>

<template>
  <div class="qt-filters">
    <a-radio-group v-model:value="view" button-style="solid">
      <a-radio-button value="runs">运行记录（{{ tuning.runs.length }}）</a-radio-button>
      <a-radio-button value="questions">题目（{{ EVAL_QS.length }}）</a-radio-button>
    </a-radio-group>
    <template v-if="view === 'runs'">
      <a-select v-model:value="trigF" allow-clear placeholder="触发方式" class="qt-sel" :options="TRIGGERS.map((s) => ({ value: s, label: s }))" />
      <span v-if="latest" class="t-12 c-3 qt-hint">最近整库运行 {{ latest.id }} · 通过率 <span class="num c-1">{{ pct(passRate(latest)) }}</span>（{{ latest.passed }}/{{ latest.total }}）</span>
    </template>
    <template v-else>
      <a-input-search v-model:value="ekw" allow-clear placeholder="搜索题目" class="qt-kw" />
      <a-select v-model:value="gF" allow-clear placeholder="分组" class="qt-sel" :options="EVAL_GROUPS.map((g) => ({ value: g, label: `${g}（${groupCount(g)}）` }))" />
      <a-select v-model:value="resF" allow-clear placeholder="最近结果" class="qt-sel" :options="['通过', '失败'].map((s) => ({ value: s, label: s }))" />
      <span class="t-12 c-3 qt-hint">v2026.10.4 发布前回放 5 题（CR-0008 履约 {{ crCount('CR-0008') }} + CR-0015 订单量 {{ crCount('CR-0015') }}）；退款相关 {{ crCount('CR-0010') }} 题待 CR-0010</span>
    </template>
  </div>

  <template v-if="view === 'runs'">
    <div class="qt-table" :class="{ 'paged-scroll': rq.overflow.value }">
      <a-table :locale="{ emptyText: '没有符合条件的运行记录' }" size="small" row-key="id" :columns="rcols" :data-source="rpaged.items" :pagination="false" :custom-row="rowProps">
        <template #bodyCell="{ column, record }">
          <span v-if="column.key === 'id'" class="num c-3">{{ record.id }}</span>
          <span v-else-if="column.key === 'time'" class="num c-2">{{ record.time }}</span>
          <span v-else-if="column.key === 'trigger'" class="ev-trig">
            <span class="c-1">{{ trigText(record as EvalRun) }}</span>
            <CrTag v-for="id in crsOf(record as EvalRun)" :key="id" :id="id" :text="id" />
          </span>
          <span v-else-if="column.key === 'version'" class="num c-2">{{ record.version }}</span>
          <span v-else-if="column.key === 'passed'" class="num">{{ record.status === '已完成' ? `${record.passed} / ${record.total}` : `-- / ${record.total}` }}</span>
          <span v-else-if="column.key === 'rate'" class="num c-1">{{ record.status === '已完成' ? pct(passRate(record)) : '--' }}</span>
          <span v-else-if="column.key === 'fails'" class="num" :class="record.fails.length ? 'tl-failed' : 'c-3'">{{ record.status === '已完成' ? record.fails.length : '--' }}</span>
          <span v-else-if="column.key === 'status'" :class="record.status === '已完成' ? 'c-2' : 'tl-attention'">{{ record.status }}</span>
          <span v-else-if="column.key === 'note'" class="c-3" :title="record.note">{{ record.note ?? '' }}</span>
        </template>
      </a-table>
    </div>
    <AppPagination v-model:page="rq.page.value" v-model:size="rq.size.value" variant="full" :total="rpaged.total" :default-size="rq.defaultSize" unit="次运行" :empty="false" class="qt-pager" />
  </template>
  <template v-else>
    <div class="qt-table" :class="{ 'paged-scroll': qq.overflow.value }">
      <a-table :locale="{ emptyText: '没有符合条件的题目' }" size="small" row-key="id" :columns="qcols" :data-source="qpaged.items" :pagination="false">
        <template #bodyCell="{ column, record }">
          <span v-if="column.key === 'id'" class="num c-3">{{ record.id }}</span>
          <span v-else-if="column.key === 'question'" class="c-1">{{ record.question }}</span>
          <span v-else-if="column.key === 'group'" class="c-2">{{ record.group }}</span>
          <span v-else-if="column.key === 'expect'" class="c-2" :title="planText(record.expect)">{{ planText(record.expect) }}</span>
          <span v-else-if="column.key === 'cr'"><CrTag v-if="record.cr" :id="record.cr" /><span v-else class="c-3">--</span></span>
          <span v-else-if="column.key === 'last'" :class="record.last === '失败' ? 'tl-failed' : 'c-2'">{{ record.last }}</span>
        </template>
      </a-table>
    </div>
    <AppPagination v-model:page="qq.page.value" v-model:size="qq.size.value" variant="full" :total="qpaged.total" :default-size="qq.defaultSize" unit="题" :empty="false" class="qt-pager" />
  </template>

  <a-drawer :open="!!cur" :width="680" :title="cur ? `${cur.id} · ${cur.trigger}` : ''" @close="setQuery({ run: undefined })">
    <div v-if="cur" class="qd">
      <div class="qd-meta">
        <span class="t-12 c-3">时间</span><span class="t-14 c-1 num">{{ cur.time }}</span>
        <span class="t-12 c-3">本体版本</span><span class="t-14 c-1 num">{{ cur.version }}</span>
        <span class="t-12 c-3">范围</span><span class="t-14 c-1">{{ cur.scope }}</span>
        <span class="t-12 c-3">执行人</span><span class="t-14 c-1">{{ cur.operator }}</span>
        <span class="t-12 c-3">通过率</span><span class="t-14 c-1 num">{{ cur.status === '已完成' ? `${pct(passRate(cur))}（${cur.passed}/${cur.total}）` : cur.status }}</span>
        <span class="t-12 c-3">失败</span><span class="t-14 num" :class="cur.fails.length ? 'tl-failed' : 'c-1'">{{ cur.status === '已完成' ? `${cur.fails.length} 题` : '--' }}</span>
      </div>
      <div v-if="crsOf(cur).length || cur.note" class="qd-note"><CrTag v-for="id in crsOf(cur)" :key="id" :id="id" /><span class="t-12 c-2">{{ cur.note }}</span></div>

      <div v-if="replayQs.length" class="qd-sec">
        <div class="t-14 fw-600 c-1">将回放的题目（{{ replayQs.length }}）</div>
        <div class="qd-list">
          <div v-for="x in replayQs" :key="x.id" class="qd-item">
            <div class="qd-head"><span class="num c-3 t-12">{{ x.id }}</span><span class="t-14 c-1">{{ x.question }}</span></div>
            <div class="t-12 c-2">当前期望：{{ planText(x.expect) }}</div>
            <div class="t-12 c-3">{{ x.last === '失败' ? '当前版本未通过；' : '' }}{{ REPLAY_HINT[x.cr!] }}</div>
          </div>
        </div>
      </div>
      <div v-else-if="cur.status === '运行中'" class="qd-sec"><span class="t-14 c-2">正在运行，预计 3 分钟完成。</span></div>
      <div v-else class="qd-sec">
        <div class="qd-head">
          <span class="t-14 fw-600 c-1">失败用例（{{ cur.fails.length }}）</span>
          <span class="t-12 c-3">{{ kinds.map(([k, n]) => `${k} ${n}`).join(' · ') }}</span>
        </div>
        <div v-if="!cur.fails.length" class="t-14 c-3">本次全部通过</div>
        <div class="qd-list">
          <div v-for="f in fpaged.items" :key="f.qid" class="qd-item">
            <div class="qd-head"><span class="num c-3 t-12">{{ f.qid }}</span><span class="t-14 c-1 qd-grow">{{ f.question }}</span><span class="t-12 tl-failed">{{ f.kind }}</span></div>
            <div class="qd-row"><span class="t-12 c-3 qd-k">期望</span><span class="t-12 c-2">{{ f.expect }}</span></div>
            <div class="qd-row"><span class="t-12 c-3 qd-k">实际</span><span class="t-12 c-1">{{ f.actual }}</span></div>
          </div>
        </div>
        <AppPagination v-if="cur.fails.length > FSIZE" v-model:page="fpage" variant="simple" :total="cur.fails.length" :size="FSIZE" unit="个失败用例" />
      </div>
    </div>
  </a-drawer>
</template>

<style scoped src="./qt.css"></style>
<style scoped>
.ev-trig { display: inline-flex; gap: 6px; align-items: center; }
</style>
