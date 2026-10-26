<script setup lang="ts">
/**
 * 反馈与纠错：点踩反馈队列（用户、问题、回答、用户说明、建议修正），可转为同义词或认证问答。
 * 转为同义词走公共“发起变更”（StartChangeModal + useDraftView.start）：弹窗里先是“新建变更单”，确认后才分配 CR 编号，
 * 取消不消耗编号；新单经 changes.ts 的 registerChange 写进公共变更单列表，“变更与发布”里能看到同一张单。
 * “发货及时率”召回不到的反馈已由 CR-0008（同义词“发货及时率”→出库及时率 v1，已通过）解决。
 * 行点击 → 反馈抽屉（?fb=FB-248）。URL：?fkw=&fst=&fk=&fpage=
 * 真接口（开关 qa）：T13 列表 / T14 抽屉 / T15–T17 写，写完 tuningTick+1 重拉。
 * T15 只支持“词已在变更单里”的关联；要新建 / 加入变更单的，后台回 409 CR_API_PENDING（静态变更单写接口 static-r3 未合入），这里提示，不走 mock 弹窗。
 */
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { useConfirmStore } from '@/stores/confirm';
import { paginate } from '@/mock';
import { useAppStore } from '@/stores/app';
import { EVAL_QS, T_METRICS, crById, metricLabel, planText, type Feedback, type FbStatus, type SemPlan } from '@/mock/qa-tuning';
import { CHANGE_REQUESTS, CR_DETAILS, changeRow, registerChange } from '@/mock/changes';
import { tuning, tuningTick, addCert, ME } from './state';
import type { PageResult } from '@/api/http';
import { ApiError } from '@/api/http';
import { qaReal, listFeedbacks, getFeedback, getTuningSummary, feedbackToSynonym, feedbackToCert, ignoreFeedback, reopenFeedback, type QaFeedback } from '@/api/qa';
import { qaLive as live, qaFallBackIfDown } from '@/pages/qa/qaFallback';
import CrTag from './CrTag.vue';
import InfoTip from '../../qa/InfoTip.vue';
import StartChangeModal from '../parts/StartChangeModal.vue';
import { useDraftView, allDrafts } from '../parts/useDraftView';

/** mock paginate 和真接口分页都有 items / total */
type Rows<T> = { items: T[]; total: number };

defineProps<{ readonly: boolean; reason?: string }>();
const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
// 当前 / 下一版本号取 app store（mock 模式是 mock 值，versioning 走真接口时是 /releases/current 的 current / next）
const app = useAppStore();
const CUR_VERSION = computed(() => app.ontologyVersion);
const NEXT_VERSION = computed(() => app.ontologyNextVersion);
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });

const STATUSES: FbStatus[] = ['待处理', '已转同义词', '已转认证问答', '待发布生效', '已忽略'];
const kw = computed({ get: () => q('fkw'), set: (v: string) => setQuery({ fkw: v || undefined }) });
const stF = computed({ get: () => q('fst') || undefined, set: (v?: string) => setQuery({ fst: v || undefined }) });
const kF = computed({ get: () => q('fk') || undefined, set: (v?: string) => setQuery({ fk: v || undefined }) });
const rows = computed(() => tuning.feedbacks.filter((f) => (!kw.value || f.question.includes(kw.value) || f.user.includes(kw.value))
  && (!stF.value || f.status === stF.value) && (!kF.value || f.fix.kind === kF.value))
  .slice().sort((a, b) => (a.status === '待处理' ? 0 : 1) - (b.status === '待处理' ? 0 : 1)));
const pq = usePagedQuery({ defaultSize: 15, filters: [kw, stF, kF], prefix: 'f' });
/* 真接口：列表 / 抽屉 / 待处理计数（T1 openFeedback） */
const real = qaReal();
const remote = ref<PageResult<QaFeedback>>({ items: [], total: 0, page: 1, size: 15 });
const remotePending = ref(0);
const remoteCur = ref<QaFeedback>();
const loadErr = (e: unknown) => { if (!qaFallBackIfDown(e)) message.error(`加载反馈失败：${(e as Error).message}`); };
const fail = (e: unknown) => { if (!qaFallBackIfDown(e)) message.error((e as Error).message); };
if (real) {
  watch([kw, stF, kF, pq.page, pq.size, tuningTick], async () => {
    if (!live.value) return;
    try {
      remote.value = await listFeedbacks({ kw: kw.value || undefined, status: stF.value, fixKind: kF.value, page: pq.page.value, size: pq.size.value });
      remotePending.value = (await getTuningSummary()).openFeedback;
    } catch (e) { loadErr(e); }
  }, { immediate: true });
  watch([() => q('fb'), tuningTick], async ([id]) => {
    if (!id || !live.value) { remoteCur.value = undefined; return; }
    try { remoteCur.value = await getFeedback(id as string); } catch (e) { remoteCur.value = undefined; loadErr(e); }
  }, { immediate: true });
}
type Fb = Feedback | QaFeedback;
const isRemote = (f: Fb): f is QaFeedback => 'certBlock' in f;
const paged = computed<Rows<Fb>>(() => (live.value ? remote.value : paginate(rows.value, { page: pq.page.value, size: pq.size.value })));
const pending = computed(() => (live.value ? remotePending.value : tuning.feedbacks.filter((f) => f.status === '待处理').length));

const cols = [
  { title: '编号', key: 'id', width: 76 },
  { title: '时间', key: 'time', width: 110 },
  { title: '用户', key: 'user', width: 72 },
  { title: '部门', key: 'dept', width: 110, ellipsis: true },
  { title: '问题', key: 'question', width: 200, ellipsis: true },
  { title: '回答', key: 'answer', ellipsis: true },
  { title: '用户说明', key: 'reason', width: 190, ellipsis: true },
  { title: '建议修正', key: 'fix', width: 260, ellipsis: true },
  { title: '状态', key: 'status', width: 190 },
  { title: '操作', key: 'op', width: 150 },
];
const stCls = (s: FbStatus) => (s === '待处理' ? 'tl-normal' : s === '已忽略' || s === '待发布生效' ? 'c-3' : 'c-2');
/** 修正已在“已通过、待整版发布”的变更单里 */
const awaitingRelease = (cr?: string) => !!cr && crById(cr)?.status === '已通过';

/** 转认证问答需要语义计划：按建议的问题在评测题库 / 高频问题里找到已确认的理解 */
const planFor = (f: Fb): SemPlan | undefined => isRemote(f) ? f.plan :
  tuning.freq.find((x) => x.question === f.fix.text)?.plan ?? EVAL_QS.find((x) => x.question === f.fix.text)?.expect ?? tuning.certs.find((c) => c.question === f.fix.text)?.plan;
const existing = (f: Fb): { id: string; question: string; status: string; variants?: string[] } | undefined => (isRemote(f)
  ? (f.existingCert ? { id: f.existingCert, question: f.existingCertQuestion ?? f.fix.text, status: f.existingCertStatus ?? '' } : undefined)
  : tuning.certs.find((c) => c.question === f.fix.text));
const planTextOf = (f: Fb) => (isRemote(f) ? f.planText ?? '' : planFor(f) ? planText(planFor(f)!) : '');
const certBlock = (f: Fb) => isRemote(f) ? f.certBlock : (existing(f) || planFor(f) ? '' : f.fix.target?.includes('新建') ? '本体里还没有对应指标，需先在指标口径中新建' : '还没有可用的语义计划，请先在评测题库里补一道题');
const relEvals = computed(() => (cur.value && isRemote(cur.value) ? cur.value.relatedEvalCount : cur.value?.resolvedBy ? EVAL_QS.filter((x) => x.cr === cur.value!.resolvedBy).length : 0));
const synCovered = (f: Fb) => (isRemote(f) ? (f.coveredBy ? { cr: f.coveredBy } : undefined) : tuning.synonyms.find((s) => s.word === f.fix.text));

/**
 * 同义词指向的对象：指向指标的登记为 kind 'metric'（id 取公共指标 id）；
 * 指向维度取值的（如“双十一备货期”→时间范围）登记为 kind 'dimension'（id 为词本身），改动范围写 dimension:<词>。
 */
function synTarget(f: Feedback): { kind: 'metric' | 'dimension'; id: string; name: string } {
  const norm = (x: string) => x.replace(/\s/g, '');
  const m = Object.values(T_METRICS).find((x) => norm(`${x.name}${x.version}`) === norm(f.fix.target ?? '') || norm(x.name) === norm(f.fix.target ?? ''));
  return m ? { kind: 'metric', id: m.drawerId ?? m.key, name: `${m.name} ${m.version}` } : { kind: 'dimension', id: f.fix.text, name: f.fix.target ?? '--' };
}
const dv = { metric: useDraftView('metric', () => ''), dimension: useDraftView('dimension', () => '') };
const changeOpen = ref(false);
const changeFb = ref<Feedback>();
const changeTargetName = computed(() => (changeFb.value ? `同义词“${changeFb.value.fix.text}”→ ${synTarget(changeFb.value).name}` : ''));

async function toSynonym(f: Fb) {
  const covered = synCovered(f);
  if (isRemote(f)) {
    if (!covered) { message.warning(`「${f.fix.text}」还不在任何变更单里；新建 / 加入变更单的接口（静态模块）尚未接入，请先在“变更与发布”中发起`); return; }
    try {
      const r = await feedbackToSynonym(f.id);
      tuningTick.value++;
      message.success(`「${f.fix.text}」已在 ${r.crId} 中，已关联${r.affected.length > 1 ? `（同词反馈 ${r.affected.length} 条）` : ''}`);
    } catch (e) {
      if (e instanceof ApiError && e.code === 'CR_API_PENDING') message.warning(e.message); else fail(e);
    }
    return;
  }
  if (covered) { f.status = awaitingRelease(covered.cr) ? '待发布生效' : '已转同义词'; f.resolvedBy = covered.cr; f.handler = ME; message.success(`「${f.fix.text}」已在 ${covered.cr} 中，已关联`); return; }
  changeFb.value = f; changeOpen.value = true;
}
/** 发起变更弹窗确认后才分配编号（新建）或加入已有草稿；取消不分配 */
function onStartChange(opts: { join?: string; title?: string }) {
  const f = changeFb.value; if (!f) return;
  const t = synTarget(f);
  const cr = dv[t.kind].start({ ...opts, target: t.id });
  const diff = (key: number) => changeRow(key, '同义词', t.name, '同义词', '新增', '', f.fix.text);
  if (opts.join) {
    const d = CR_DETAILS[cr]; if (d) { d.diffs.push(diff(d.diffs.length + 1)); (d.synonyms ??= []).push({ word: f.fix.text, to: t.name }); }
    const c = CHANGE_REQUESTS.find((x) => x.id === cr); if (c) c.changes += 1;
  } else {
    const at = allDrafts().find((d) => d.id === cr)?.updatedAt ?? '';
    registerChange(
      { id: cr, title: opts.title || '未命名变更', owner: ME, reviewer: '刘婷', status: '草稿', level: '修订', changes: 1, updatedAt: at, reason: `问数反馈 ${f.id}：用户说“${f.fix.text}”，问数召回不到` },
      {
        diffs: [diff(1)],
        impact: { direct: [{ kind: '同义词', name: f.fix.text, note: `指向${t.name}` }], defs: [{ kind: t.kind === 'metric' ? '指标' : '维度', name: t.name, note: t.kind === 'metric' ? '口径不变，版本不变' : '取值不变' }], usage: [{ kind: '问数反馈', name: f.id, note: f.question }] },
        touches: [`${t.kind}:${t.id}`],
        synonyms: [{ word: f.fix.text, to: t.name }],
        times: { created: at },
      },
    );
  }
  tuning.synonyms.push({ word: f.fix.text, target: f.fix.target ?? '', cr });
  tuning.feedbacks.filter((x) => x.status === '待处理' && x.fix.kind === '同义词' && x.fix.text === f.fix.text)
    .forEach((x) => { x.status = '已转同义词'; x.resolvedBy = cr; x.handler = ME; });
  message.success(opts.join ? `已加入变更单 ${cr}：同义词「${f.fix.text}」→ ${t.name}` : `已生成变更单草稿 ${cr}，可在“变更与发布”中查看`);
  changeFb.value = undefined;
}
async function toCert(f: Fb) {
  const ex = existing(f);
  const plan = planFor(f);
  if (!ex && !plan) return;
  const m = plan ? (T_METRICS[plan.metric] as (typeof T_METRICS)[string] | undefined) : undefined;
  const cr = m?.cr ? crById(m.cr) : undefined;
  const ok = await confirm.ask({
    title: '转为认证问答', target: `${f.id} ${f.question}`,
    fields: ex
      ? [{ label: '已有认证问答', value: `${ex.id} ${ex.question}` }, { label: '状态', value: ex.status }]
      : [{ label: '问题', value: f.fix.text }, { label: '绑定指标', value: m ? metricLabel(plan!.metric) : plan!.metric }, { label: '语义计划', value: planTextOf(f) }, { label: '认证人', value: ME }],
    impact: ex ? `已有相同问题的认证问答，把用户原话“${f.question}”加为它的同问法。`
      : `认证后同类问题直接按此语义计划回答。${cr && cr.status !== '已发布' ? `注意：${cr.id}「${cr.title}」${cr.status}，发布后需重新认证。` : ''}`,
    okText: ex ? '加为同问法' : '认证',
  });
  if (!ok) return;
  if (isRemote(f)) {
    try { const r = await feedbackToCert(f.id); tuningTick.value++; message.success(r.mode === 'variant' ? `已加为 ${r.certId} 的同问法` : `已生成认证问答 ${r.certId}`); } catch (e) { fail(e); }
    return;
  }
  if (ex) { if (ex.variants && !ex.variants.includes(f.question) && f.question !== ex.question) ex.variants.push(f.question); f.resolvedBy = ex.id; }
  else {
    const c = addCert(f.fix.text, plan!, '反馈纠错', cr && cr.status !== '已发布' ? { status: '待重新认证', cr: cr.id, note: `已按 ${m!.version} 认证；${cr.id} 发布后需再次认证` } : {});
    if (f.question !== c.question) c.variants.push(f.question);
    f.resolvedBy = c.id;
  }
  f.status = '已转认证问答'; f.handler = ME;
  message.success(ex ? `已加为 ${ex.id} 的同问法` : `已生成认证问答 ${f.resolvedBy}`);
}
async function ignore(f: Fb) {
  if (isRemote(f)) { try { await ignoreFeedback(f.id); tuningTick.value++; message.success(`已忽略 ${f.id}`); } catch (e) { fail(e); } return; }
  f.status = '已忽略'; f.handler = ME; message.success(`已忽略 ${f.id}`);
}
async function reopen(f: Fb) {
  if (isRemote(f)) { try { await reopenFeedback(f.id); tuningTick.value++; message.success(`已重新打开 ${f.id}`); } catch (e) { fail(e); } return; }
  f.status = '待处理'; f.handler = undefined; f.resolvedBy = undefined; message.success(`已重新打开 ${f.id}`);
}

const cur = computed<Fb | undefined>(() => (live.value ? remoteCur.value : tuning.feedbacks.find((f) => f.id === q('fb'))));
const rowProps = (f: Fb) => ({ class: f.id === q('fb') ? 'qt-row-on' : '', onClick: () => setQuery({ fb: f.id }) });
const isCr = (s?: string) => !!s && s.startsWith('CR-');
</script>

<template>
  <div class="qt-filters">
    <a-input-search v-model:value="kw" allow-clear placeholder="搜索问题或用户" class="qt-kw" />
    <a-select v-model:value="stF" allow-clear placeholder="状态" class="qt-sel" :options="STATUSES.map((s) => ({ value: s, label: s }))" />
    <a-select v-model:value="kF" allow-clear placeholder="建议修正" class="qt-sel" :options="['同义词', '认证问答'].map((s) => ({ value: s, label: s }))" />
    <span class="t-12 c-3 qt-hint">{{ pending }} 条待处理；同义词改动生成变更单，随整版发布生效</span>
  </div>
  <div class="qt-table" :class="{ 'paged-scroll': pq.overflow.value }">
    <a-table :locale="{ emptyText: '没有符合条件的反馈' }" size="small" row-key="id" :columns="cols" :data-source="paged.items" :pagination="false" :custom-row="rowProps">
      <template #bodyCell="{ column, record }">
        <span v-if="column.key === 'id'" class="num c-3">{{ record.id }}</span>
        <span v-else-if="column.key === 'time'" class="num c-2">{{ record.time.slice(5) }}</span>
        <span v-else-if="column.key === 'user'" class="c-1">{{ record.user }}</span>
        <span v-else-if="column.key === 'dept'" class="c-1" :title="record.dept">{{ record.dept }}</span>
        <span v-else-if="column.key === 'question'" class="c-1" :title="record.question">{{ record.question }}</span>
        <span v-else-if="column.key === 'answer'" class="c-2" :title="record.answer">{{ record.answer }}</span>
        <span v-else-if="column.key === 'reason'" class="c-2" :title="record.reason">{{ record.reason }}</span>
        <span v-else-if="column.key === 'fix'" class="c-2" :title="`${record.fix.text} → ${record.fix.target}`"><span class="c-3">{{ record.fix.kind }}</span> {{ record.fix.text }} → {{ record.fix.target }}</span>
        <span v-else-if="column.key === 'status'" class="fb-st">
          <span :class="stCls(record.status)" style="white-space: nowrap">{{ record.status }}</span>
          <CrTag v-if="isCr(record.resolvedBy)" :id="record.resolvedBy" :text="record.resolvedBy" />
          <span v-else-if="record.resolvedBy" class="num c-3 t-12">{{ record.resolvedBy }}</span>
        </span>
        <span v-else-if="column.key === 'op'" class="qt-ops" @click.stop>
          <template v-if="record.status === '待处理'">
            <a-tooltip v-if="record.fix.kind === '同义词'" :title="reason"><a-button type="link" size="small" :disabled="readonly" @click="toSynonym(record)">转为同义词</a-button></a-tooltip>
            <a-tooltip v-else :title="reason || certBlock(record)"><a-button type="link" size="small" :disabled="readonly || !!certBlock(record)" @click="toCert(record)">转为认证问答</a-button></a-tooltip>
            <a-tooltip :title="reason"><a-button type="link" size="small" :disabled="readonly" @click="ignore(record)">忽略</a-button></a-tooltip>
          </template>
          <a-tooltip v-else-if="record.status === '已忽略'" :title="reason"><a-button type="link" size="small" :disabled="readonly" @click="reopen(record)">重新打开</a-button></a-tooltip>
          <span v-else class="t-12 c-3">{{ record.handler }} 处理</span>
        </span>
      </template>
    </a-table>
  </div>
  <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="paged.total" :default-size="pq.defaultSize" unit="条反馈" :empty="false" class="qt-pager" />

  <a-drawer :open="!!cur" :width="600" :title="cur ? `${cur.id} 用户反馈` : ''" @close="setQuery({ fb: undefined })">
    <template #extra>
      <a-space v-if="cur && cur.status === '待处理'">
        <a-tooltip :title="reason"><a-button :disabled="readonly" @click="ignore(cur)">忽略</a-button></a-tooltip>
        <a-tooltip :title="reason || certBlock(cur)"><a-button :type="cur.fix.kind === '认证问答' ? 'primary' : 'default'" :disabled="readonly || !!certBlock(cur)" @click="toCert(cur)">转为认证问答</a-button></a-tooltip>
        <a-tooltip :title="reason"><a-button :type="cur.fix.kind === '同义词' ? 'primary' : 'default'" :disabled="readonly" @click="toSynonym(cur)">转为同义词</a-button></a-tooltip>
      </a-space>
    </template>
    <div v-if="cur" class="qd">
      <div class="qd-meta">
        <span class="t-12 c-3">用户</span><span class="t-14 c-1">{{ cur.user }} · {{ cur.dept }}</span>
        <span class="t-12 c-3">时间</span><span class="t-14 c-1 num">{{ cur.time }}</span>
        <span class="t-12 c-3">状态</span><span class="t-14 qd-head" :class="stCls(cur.status)">{{ cur.status }}<span v-if="cur.handler" class="c-3"> · {{ cur.handler }}</span><InfoTip v-if="cur.status === '待发布生效' && cur.resolvedBy" :title="`${cur.resolvedBy} 已为${cur.fix.target}补充同义词“${cur.fix.text}”，变更单已通过，随 ${NEXT_VERSION} 发布后生效；生效前问数仍按当前版本 ${CUR_VERSION} 回答，发布前自动回放 ${relEvals} 道相关评测题。`" /></span>
        <span class="t-12 c-3">关联</span><span class="t-14"><CrTag v-if="isCr(cur.resolvedBy)" :id="cur.resolvedBy!" /><span v-else class="c-2 num">{{ cur.resolvedBy ?? '--' }}</span></span>
      </div>
      <div class="qd-sec">
        <div class="t-14 fw-600 c-1">问题与回答</div>
        <div class="qd-row"><span class="t-12 c-3 qd-k">问题</span><span class="t-14 c-1">{{ cur.question }}</span></div>
        <div class="qd-row"><span class="t-12 c-3 qd-k">回答</span><span class="t-14 c-2">{{ cur.answer }}</span></div>
        <div class="qd-row"><span class="t-12 c-3 qd-k">说明</span><span class="t-14 c-2">{{ cur.reason }}</span></div>
      </div>
      <div class="qd-sec">
        <div class="qd-head"><span class="t-14 fw-600 c-1">建议修正 · {{ cur.fix.kind }}</span>
          <InfoTip v-if="certBlock(cur) || existing(cur)" :title="[certBlock(cur) ? `转认证问答：${certBlock(cur)}` : '', existing(cur) ? `已有认证问答 ${existing(cur)!.id}，转为认证问答时会把用户原话加为同问法` : ''].filter(Boolean).join('；')" /></div>
        <div class="qd-row"><span class="t-12 c-3 qd-k">修正</span><span class="t-14 c-1">{{ cur.fix.text }} → {{ cur.fix.target }}</span></div>
        <div v-if="cur.fix.kind === '认证问答' && planFor(cur)" class="qd-row"><span class="t-12 c-3 qd-k">计划</span><span class="t-14 c-1 qd-ell" :title="planTextOf(cur)">{{ planTextOf(cur) }}</span></div>
      </div>
    </div>
  </a-drawer>
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTargetName" title-placeholder="例如：销售额补充同义词“GMV”" @ok="onStartChange" />
</template>

<style scoped src="./qt.css"></style>
<style scoped>
.fb-st { display: inline-flex; gap: 6px; align-items: center; }
</style>
