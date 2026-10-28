<script setup lang="ts">
/** 对话区：页面里唯一的滚动区（.scroll-area）。内容不足时贴底显示 */
import { ref, computed, onMounted, nextTick, watch, type ComponentPublicInstance } from 'vue';
import { useRoute } from 'vue-router';
import { message } from 'ant-design-vue';
import { ruleEvents, runRule } from '@/api/qa';
import { LockOutlined, InboxOutlined, ClockCircleOutlined, ApartmentOutlined, MessageOutlined } from '@ant-design/icons-vue';
import { QA_DEMO_TURNS, QA_DEMO_CANNED } from '@/mock';
import { useReadonly } from '@/composables/useReadonly';
import { useQaSession, clarifyFollowUp, chartRequests } from '../useQaSession';
import { useAppStore } from '@/stores/app';
import QaAnswer from './QaAnswer.vue';
import QaStoreAnswer from './QaStoreAnswer.vue';
import QaDynamicSummary from './QaDynamicSummary.vue';
import QaResultView from './QaResultView.vue';
import ActionReviewPanel from '@/pages/ops/ActionReview.vue';
import type { QaDynamicAnswer } from '@/api/qa';
import type { QaRealTurn, QaChainAnswer, QaClarifyAnswer, QaActionAnswer, QaAlertRef, QaActionSuggest, QaActionRun } from '@/api/qa';
import { pinTitle } from '@/api/qaPages';
/** applied：越权那一轮已提交申请；tips：本会话中途修改范围/口径后插入的系统提示（追加在最新一轮之后） */
const props = defineProps<{ applied: boolean; tips: string[]; actionRuns: Record<string, QaActionRun> }>();
/** pin：真接口时带这一轮的 id 和问题（固定到页面 P7 用） */
const emit = defineEmits<{ (e: 'suggest', action?: QaActionSuggest, turnId?: string): void; (e: 'refresh-action', turnId: string): void; (e: 'pin', turn?: { id: string; title: string }): void; (e: 'apply', turnId?: string): void }>();
const { isNew, activeTurns, activeId, draft, live, detail, sending, loading, send, streamStatus } = useQaSession();
const { readonly, reason } = useReadonly();
/** 示例会话的轮次（mock/qa.ts）；新追加的轮次统一用 QA_DEMO_CANNED */
const TURNS = computed<QaRealTurn[]>(() => (live.value ? detail.value?.turns ?? [] : activeId.value ? QA_DEMO_TURNS : []));
/** 真接口：链路新生成的回答（card = table）——沿用新轮次的回答气泡，多行结果在气泡下给出结果表 */
type ChainTurn = Extract<QaRealTurn, { answer: QaChainAnswer }>;
type ActionTurn = Extract<QaRealTurn, { answer: QaActionAnswer }>;
const ruleDialog = ref(false), ruleBusy = ref(false), ruleTurn = ref(''), selectedEvent = ref<string>();
const ruleChoices = ref<{ value: string; label: string }[]>([]);
const scheduledRule = ref(false);
const ruleSearch = ref(''), ruleNext = ref<number | null>();
async function loadRuleEvents(reset = false) {
  ruleBusy.value = true;
  try {
    const result = await ruleEvents(ruleTurn.value, reset ? undefined : ruleNext.value ?? undefined, ruleSearch.value);
    const choices = result.items.map(e => ({ value: e.id, label: `${e.label} · ${e.id} · ${e.at}` }));
    ruleChoices.value = reset ? choices : [...ruleChoices.value, ...choices];
    ruleNext.value = result.nextBefore ?? null;
  } catch (e) { message.error(e instanceof Error ? e.message : '无法读取事件'); }
  finally { ruleBusy.value = false; }
}
async function openRule(tid: string, kind?: string) {
  scheduledRule.value = kind === '定时';
  ruleTurn.value = tid; selectedEvent.value = undefined; ruleChoices.value = []; ruleSearch.value = ''; ruleNext.value = null; ruleDialog.value = true;
  if (!scheduledRule.value) await loadRuleEvents(true);
}
async function confirmRule() {
  if (!scheduledRule.value && !selectedEvent.value) { message.warning('请选择真实事件'); return; }
  ruleBusy.value = true;
  try {
    const result = await runRule(ruleTurn.value, selectedEvent.value);
    const turn = TURNS.value.find(t => t.id === ruleTurn.value);
    if (turn?.kind === 'answer' && turn.answer.card === 'rule') { turn.answer.rule.eventId = selectedEvent.value; turn.answer.rule.result = result; }
    ruleDialog.value = false; message.success('规则已运行');
  } catch (e) { message.error(e instanceof Error ? e.message : '规则运行失败'); }
  finally { ruleBusy.value = false; }
}
const isAction = (t: QaRealTurn): t is ActionTurn => t.kind === 'answer' && (t.answer as { card: string }).card === 'action';
/** 回答带的相关告警（只读引用，动态模块的告警） */
const alertsOf = (t: QaRealTurn): QaAlertRef[] => (t.kind === 'answer' ? ((t.answer as { alerts?: QaAlertRef[] }).alerts ?? []) : []);
const summariesOf = (turn: QaRealTurn) => {
  if (turn.kind !== 'answer') return [];
  const answer = turn.answer as QaDynamicAnswer;
  return answer.dynamicSummaries?.length ? answer.dynamicSummaries : answer.dynamicSummary ? [answer.dynamicSummary] : [];
};
const isChain = (t: QaRealTurn): t is ChainTurn => t.kind === 'answer' && (t.answer as { card: string }).card === 'table';
const chainMeta = computed(() => {
  const scope = detail.value?.scope?.trim();
  const caliber = detail.value?.caliber?.trim();
  const parts = [scope ? `范围 = ${scope}` : '', caliber ? `口径 = ${caliber}` : ''].filter(Boolean);
  return parts.length ? `按 ${parts.join('、')} 回答` : '';
});
function retry(text: string) { draft.value = text; }
/**
 * 链路反问（card='clarify'）：点选项 = 再问一轮。链路不带上下文，追问要自带完整意思 [推断]：
 * - 短选项、原问题里有那个说法 → 换掉说法（「哪个门店表现最好？」+「销售额最高」→「哪个门店销售额最高？」）；
 * - 长选项（≥ 8 字，模型给的是整句，如「是，查询华东大区上个月的有效订单数」）→ 去掉开头的「是，」直接问；
 * - 其他 → 原问题后补「（选项）」。
 */
type ClarifyTurn = Extract<QaRealTurn, { answer: QaClarifyAnswer }>;
const followUp = (t: ClarifyTurn, label: string) => clarifyFollowUp(t.question, t.answer.clarify.term, label);
function onClarify(t: QaRealTurn, label: string) { if (t.kind === 'answer' && t.answer.card === 'clarify') send(followUp(t as ClarifyTurn, label)); }
/** 已按哪个选项追问过：后面有一轮的问题正好是那句追问（刷新后也能还原；chosen 不回写后台）[推断] */
function chosenOf(t: QaRealTurn): string | null {
  if (t.kind !== 'answer' || t.answer.card !== 'clarify') return null;
  const ct = t as ClarifyTurn;
  const asked = new Set(TURNS.value.map((x) => ('question' in x ? x.question : '')));
  return ct.answer.clarify.options.find((o) => asked.has(followUp(ct, o.label)))?.label ?? ct.answer.clarify.chosen ?? null;
}
/** 拒答：只有权限拦截（NO_PERMISSION，或演示数据没有 code）带「申请权限」；超出范围 / 没有数据 / 非查询只显示原因 [推断] */
const canApply = (t: QaRealTurn) => !('code' in t) || !t.code || t.code === 'NO_PERMISSION' || t.code === 'OUT_OF_SCOPE';
const me = useAppStore().user.name.slice(0, 1);
const route = useRoute();
const conv = ref<HTMLElement>();
const storeTurn = ref<HTMLElement>();
/** 门店表那一轮的提问行，用于 ?tblPage 定位 */
function setStoreTurn(el: Element | ComponentPublicInstance | null) { if (el instanceof HTMLElement) storeTurn.value = el; }
// 默认停在最新一轮（底部）；URL 带 ?tblPage 时定位到门店表那一轮，便于复现翻页状态
onMounted(async () => {
  await nextTick();
  const el = conv.value; if (!el) return;
  if (route.query.tblPage && storeTurn.value) el.scrollTop += storeTurn.value.getBoundingClientRect().top - el.getBoundingClientRect().top - 12;
  else el.scrollTop = el.scrollHeight;
});
// 新插入系统提示时滚到底部
watch(() => [props.tips.length, activeTurns.value.length, activeId.value, TURNS.value.length, sending.value], async () => { await nextTick(); if (conv.value) conv.value.scrollTop = conv.value.scrollHeight; });
watch(chartRequests, async (requests) => { const ids = Object.keys(requests); const id = ids[ids.length - 1]; await nextTick(); if (id) document.getElementById(`qa-result-${id}`)?.scrollIntoView({ block: 'nearest' }); });
</script>

<template>
  <div ref="conv" class="qa-conv scroll-area">
    <a-modal v-model:open="ruleDialog" title="确认运行规则" ok-text="确认运行" :confirm-loading="ruleBusy" :ok-button-props="{ disabled: !scheduledRule && !selectedEvent }" @ok="confirmRule">
      <p v-if="scheduledRule">将按当前对象状态运行已发布的定时规则，可能生成告警或自动执行动作并触发外部写入。仅规则配置的运行身份可以确认。</p>
      <template v-else>
      <p>选择符合当前发布版本的真实事件。规则可能生成告警，并按规则配置及动作执行策略自动执行或生成待确认动作；自动执行可能触发外部写入。</p>
      <a-input-search v-model:value="ruleSearch" placeholder="按事件编号或对象筛选" :maxlength="100" @search="loadRuleEvents(true)" />
      <a-select v-model:value="selectedEvent" :options="ruleChoices" :loading="ruleBusy" placeholder="选择事件" style="width: 100%" />
      <a-button v-if="ruleNext" :loading="ruleBusy" @click="loadRuleEvents()">加载更多</a-button>
      <p v-if="!ruleBusy && !ruleChoices.length">没有可用的真实事件，请先接入事件。</p>
      </template>
    </a-modal>
    <div v-if="live && loading && !detail" class="qa-empty"><a-spin /></div>
    <a-empty v-else-if="!activeId" class="qa-empty" description="还没有会话：点左侧「新建会话」开始提问" />
    <a-empty v-if="isNew && !activeTurns.length" class="qa-empty" description="新会话：在下方输入问题，Enter 发送">
      <template #image><MessageOutlined class="qa-empty-ic c-3" /></template>
    </a-empty>
    <template v-if="!isNew">
    <template v-for="t in TURNS" :key="t.id">
    <div v-if="t.kind === 'system'" class="qa-sys t-12 c-3"><span>{{ t.time }} · {{ t.text }}</span></div>
    <template v-else>
    <div :ref="t.kind === 'answer' && t.answer.card === 'store' ? setStoreTurn : undefined" class="qa-u"><span v-if="t.time" class="t-12 c-3">{{ t.time }}</span><span class="qa-bubble">{{ t.question }}</span><a-avatar size="small" class="qa-me">{{ me }}</a-avatar></div>
    <template v-if="t.kind === 'answer' && t.answer.card === 'rule'">
      <div class="qa-b qa-b-ans"><ApartmentOutlined class="qa-b-av" /><span class="qa-b-txt">{{ t.answer.text }}</span>
        <span v-if="t.answer.rule.result">已运行 <template v-if="t.answer.rule.eventId">· 事件 {{ t.answer.rule.eventId }}</template> · {{ t.answer.rule.result.status }}</span>
        <a-button v-else size="small" :disabled="readonly || t.answer.rule.cardVersion !== 1" @click="openRule(t.id, t.answer.rule.triggerKind)">{{ t.answer.rule.triggerKind === '定时' ? '确认运行' : '选择事件并确认' }}</a-button>
      </div>
    </template>
    <template v-else-if="isAction(t)">
      <div class="qa-b qa-b-ans">
        <ApartmentOutlined class="qa-b-av" />
        <span class="qa-b-txt">{{ t.answer.text }}</span>
        <a-tag :color="t.answer.action.dryRun.passed ? 'green' : 'orange'">{{ t.answer.action.dryRun.passed ? '校验通过' : '校验未通过' }}</a-tag>
        <a-tag v-if="actionRuns[t.id]" :color="actionRuns[t.id].status === '成功' ? 'green' : undefined">{{ actionRuns[t.id].status }} · {{ actionRuns[t.id].id }}</a-tag>
        <a-tag v-else-if="t.answer.action.runId">记录 {{ t.answer.action.runId }} · 状态待查询</a-tag>
        <span v-if="actionRuns[t.id]?.status === '失败' && actionRuns[t.id]?.failReason" class="t-12 c-3">{{ actionRuns[t.id]?.failReason }}</span>
        <span v-if="actionRuns[t.id]?.status === '结果未知' && actionRuns[t.id]?.unknownReason" class="t-12 c-3">{{ actionRuns[t.id]?.unknownReason }}</span>
        <span v-if="actionRuns[t.id]?.status === '已对账'" class="t-12 c-3">{{ actionRuns[t.id]?.reconcile }}<template v-if="actionRuns[t.id]?.extRef"> · 外部单号 {{ actionRuns[t.id]?.extRef }}</template></span>
        <a-button v-if="actionRuns[t.id] && ['执行中', '待审批', '结果未知'].includes(actionRuns[t.id].status)" size="small" @click="emit('refresh-action', t.id)">刷新状态</a-button>
        <span v-if="live && !t.answer.action.runId && t.answer.action.stageVersion !== 1" class="t-12 c-3">历史动作卡无法确认是否已执行，请重新提问后发起。</span>
        <a-tooltip v-else-if="t.answer.action.canStage !== false && (!actionRuns[t.id] || actionRuns[t.id].status === '待确认')" :title="reason || '在当前页面确认目标和参数后执行'"><a-button size="small" :disabled="readonly" @click="emit('suggest', t.answer.action, t.id)">{{ t.answer.action.runId && !actionRuns[t.id] ? '查看状态' : '确认并执行' }}</a-button></a-tooltip>
        <a-tag v-else-if="t.answer.action.canStage === false">仅建议</a-tag>
      </div>
      <ActionReviewPanel v-bind="t.answer.action.dryRun" />
    </template>
    <template v-else-if="isChain(t)">
      <div class="qa-b qa-b-ans">
        <ApartmentOutlined class="qa-b-av" />
        <span class="qa-b-txt">{{ t.answer.text }}</span>
        <span v-if="chainMeta" class="t-12 c-3 qa-b-meta">{{ chainMeta }}</span>
      </div>
      <QaResultView :id="`qa-result-${t.id}`" :table="t.answer.table" :question="t.question" :request="chartRequests[t.id]" />
    </template>
    <div v-if="alertsOf(t).length" class="qa-alerts t-12 c-2">
      相关告警：<a-tag v-for="a in alertsOf(t)" :key="a.id" :color="a.level === '高' ? 'red' : a.level === '中' ? 'orange' : 'default'">{{ a.objLabel || a.objId }} · {{ a.rule }}（{{ a.status }}）</a-tag>
    </div>
    <QaDynamicSummary :items="summariesOf(t)" />
    <template v-if="isAction(t) || isChain(t) || (t.kind === 'answer' && t.answer.card === 'rule')" />
    <template v-else-if="t.kind === 'answer'">
    <QaStoreAnswer v-if="t.answer.card === 'store'" :card="t.answer" :turn-id="live ? t.id : undefined" />
    <QaAnswer v-else-if="t.answer.card !== 'rule'" :card="t.answer" :turn-id="live ? t.id : undefined" :vote="'vote' in t ? t.vote : undefined" :chosen="chosenOf(t)" @suggest="emit('suggest')" @pin="emit('pin', live ? { id: t.id, title: pinTitle(t.question) } : undefined)" @clarify="(l: string) => onClarify(t, l)" />
    </template>
    <div v-else-if="t.kind === 'denied'" class="qa-b">
      <LockOutlined class="c-3" />
      <span class="qa-b-txt">{{ t.lead }}<span class="fw-600">{{ t.bold }}</span>{{ t.tail }}</span>
      <span v-if="canApply(t) && (live ? t.applied : applied)" class="qa-b-state c-3"><ClockCircleOutlined />已申请 · 待审批</span>
      <template v-else><a-tag>{{ t.tag }}</a-tag><a-button v-if="canApply(t)" size="small" @click="emit('apply', t.id)">申请权限</a-button></template>
    </div>
    <div v-else class="qa-b">
      <InboxOutlined class="c-3" />
      <span class="qa-b-txt">{{ t.lead }}<span class="fw-600">{{ t.bold }}</span>{{ t.tail }}</span>
      <a-tag>{{ t.tag }}</a-tag><a-tooltip v-if="t.retry" :title="reason || t.retry.tip"><a-button size="small" :disabled="readonly" @click="retry(t.retry.draft)">{{ t.retry.label }}</a-button></a-tooltip>
    </div>
    </template>
    </template>
    <div v-for="(t, i) in tips" :key="i" class="qa-sys t-12 c-3"><span>{{ t }}</span></div>
    </template>
    <div v-if="live && sending" class="qa-b qa-b-ans"><ApartmentOutlined class="qa-b-av" /><span class="qa-b-txt c-3">{{ streamStatus || '正在查询…' }}</span><a-spin size="small" /></div>
    <template v-for="t in activeTurns" :key="t.id">
      <div class="qa-u"><span class="t-12 c-3">{{ t.time }}</span><span class="qa-bubble">{{ t.q }}</span><a-avatar size="small" class="qa-me">{{ me }}</a-avatar></div>
      <div class="qa-b qa-b-ans">
        <ApartmentOutlined class="qa-b-av" />
        <span class="qa-b-txt">{{ QA_DEMO_CANNED.text }}</span>
        <span class="t-12 c-3 qa-b-meta">{{ QA_DEMO_CANNED.meta }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.qa-conv { flex: 1; padding: 12px 20px; display: flex; flex-direction: column; }
.qa-conv > :first-child { margin-top: auto; }
.qa-conv > * { flex: none; }
.qa-u { display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-bottom: 8px; }
.qa-bubble { background: var(--brand-bg); border-radius: var(--radius); padding: 4px 12px; }
.qa-me { background: var(--brand); flex: none; }
.qa-b { display: flex; align-items: center; gap: 8px; background: var(--g1); border: var(--border); border-radius: var(--radius); padding: 6px 16px; margin: 0 40px 10px 0; }
.qa-b-txt { flex: 1; }
.qa-b :deep(.ant-tag) { margin-inline-end: 0; }
.qa-b-av { color: var(--brand); }
.qa-b-meta { flex: none; white-space: nowrap; }
.qa-empty { margin: auto; }
.qa-empty-ic { font-size: 40px; }
.qa-b-state { display: inline-flex; align-items: center; gap: 4px; }
/* 系统提示：灰色细线居中，标出范围/口径从哪一问开始变化；之前的答案保留各自依据 */
.qa-sys { display: flex; align-items: center; gap: 12px; margin: 0 40px 10px 0; }
.qa-sys::before, .qa-sys::after { content: ''; flex: 1; border-top: var(--border); }
.qa-ans + .qa-sys, :deep(.qa-ans) + .qa-sys { margin-top: 10px; }
.qa-alerts { margin: 4px 0 8px 32px; }
</style>
