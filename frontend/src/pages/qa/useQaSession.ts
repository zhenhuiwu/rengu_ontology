/**
 * 问数工作台的会话状态（会话列表、对话区、输入框共用）。
 * 原型（mock）：发送后用固定的示例回答追加一轮；新建会话得到一个空会话。
 * 真接口（开关 qa）：列表由左栏按页取（W1），当前会话详情 W3，发送 W6，新建 W2；见 src/api/qa.ts。
 */
import { ref, computed, watch } from 'vue';
import { message } from 'ant-design-vue';
import { QA_SESSION_LIST, QA_TODAY } from '@/mock';
import type { QaSession } from '@/mock/qa';
import * as qaApi from '@/api/qa';
import { currentApiContext, isApiError } from '@/api/http';
import { qaLive, qaFallBackIfDown } from './qaFallback';

/** real：开关 qa（写操作按它走真接口）；live：渲染 / 读接口（后台没起来时回退 mock） */
const real = qaApi.qaReal();
const live = qaLive;

export interface QaTurn { id: number; q: string; time: string; }

const sessions = ref<QaSession[]>(real ? [] : [...QA_SESSION_LIST]);
const activeId = ref<string>(live.value ? '' : QA_SESSION_LIST[0].id);
/** 真接口：当前会话详情（含全部轮次）；listTick 变了左栏重取当前页 */
const storedDetail = ref<qaApi.QaSessionDetail | null>(null);
const detailContext = ref('');
const contextKey = () => JSON.stringify(currentApiContext());
const detail = computed({
  get: () => !real || (detailContext.value === contextKey() && storedDetail.value?.id === activeId.value) ? storedDetail.value : null,
  set: (value: qaApi.QaSessionDetail | null) => { detailContext.value = contextKey(); storedDetail.value = value; },
});
const loading = ref(false);
const sending = ref(false);
/** W7 实时阶段提示（SSE status 事件的文案），发送结束清空 */
const streamStatus = ref('');
const listTick = ref(0);
/** favTick 变了左栏底部「常用问题」前 3 条重取（抽屉里移出后） */
const favTick = ref(0);
let loadSeq = 0, sendSeq = 0, createSeq = 0;
let detailRequest: AbortController | undefined;
const creating = ref(false);

export const errText = (e: unknown) => (isApiError(e) ? `${e.message || e.code}（${e.code}）` : String(e));

async function loadDetail(id = activeId.value) {
  if (!live.value || !id || id !== activeId.value) return;
  detailRequest?.abort();
  const my = ++loadSeq, key = contextKey();
  detailRequest = new AbortController();
  const valid = () => my === loadSeq && key === contextKey() && id === activeId.value && live.value;
  loading.value = true;
  try {
    const d = await qaApi.getSession(id, qaApi.qaRequestOptions(detailRequest.signal));
    if (valid()) detail.value = d;
  } catch (e) {
    if (valid() && (e as Error)?.name !== 'AbortError') { detail.value = null; if (!qaFallBackIfDown(e)) message.error(`会话加载失败：${errText(e)}`); }
  } finally { if (valid()) loading.value = false; }
}
/** 每个会话里本次新追加的轮次（原有历史轮次由 QaConversation 固定渲染，只属于第一个会话） */
const turns = ref<Record<string, QaTurn[]>>({});
const draft = ref('');
export const chartRequests = ref<Record<string, { type: 'bar' | 'line' }>>({});
if (real) {
  watch(contextKey, () => {
    ++loadSeq; ++sendSeq; ++createSeq; detailRequest?.abort();
    detail.value = null; activeId.value = ''; draft.value = ''; sessions.value = []; turns.value = {}; chartRequests.value = {}; listTick.value++; favTick.value++;
    loading.value = false; sending.value = false; creating.value = false;
  }, { flush: 'sync' });
  watch(activeId, () => { ++loadSeq; detailRequest?.abort(); loading.value = false; ++sendSeq; sending.value = false; draft.value = ''; }, { flush: 'sync' });
  watch([activeId, live], ([id, on]) => {
    if (!on || !id || detail.value?.id !== id) void loadDetail(id);
  }, { immediate: true });
}
let seq = 0;
let newSeq = 0;

const now = () => { const d = new Date(); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };

/** 反问选项 → 自包含追问句（链路无多轮上下文）[推断]：短选项替换原问题里的 term；长句选项去掉开头「是，」直接当问题；否则「原问题（选项）」 */
export function clarifyFollowUp(question: string, term: string, label: string) {
  const next = term && question.includes(term) && label.length <= term.length + 6
    ? question.replace(term, label)
    : label.length >= 8 ? label.replace(/^是[，,、\s]*/, '') : `${question}（${label}）`;
  const chart = question.match(/柱状图|柱形图|条形图|折线图/);
  return chart && !/柱状图|柱形图|条形图|折线图/.test(next) ? `${next}（${chart[0]}展示）` : next;
}

export function useQaSession() {
  /** 新建的会话没有历史轮次，对话区显示空状态 */
  const isNew = computed(() => (live.value ? !!detail.value && detail.value.id === activeId.value && !detail.value.turns.length : activeId.value.startsWith('NEW-')));
  const activeTurns = computed(() => turns.value[activeId.value] ?? []);
  async function sendReal(q: string) {
    const sid = activeId.value, key = contextKey();
    if (!sid || sending.value) return false;
    const my = ++sendSeq;
    const valid = () => my === sendSeq && sid === activeId.value && key === contextKey();
    sending.value = true;
    draft.value = '';
    try {
      // W7：先走 SSE（阶段提示实时更新），后台没有流 / 流断了没拿到 done → 回退 W6 同步接口
      let r: qaApi.QaAskOut;
      try {
        r = await qaApi.askTurnStream(sid, q, (e) => { if (valid() && e.event === 'status') streamStatus.value = String(e.data?.text ?? ''); });
      } catch (e) {
        if (!(e instanceof qaApi.QaStreamUnsupported)) throw e;
        r = await qaApi.askTurn(sid, q, qaApi.qaRequestOptions());
      }
      if (!valid()) return false;
      if (detail.value?.id === sid) {
        if (!detail.value.turns.some((t) => t.id === r.turn.id)) detail.value.turns.push(r.turn);
        if (detail.value.title === '新会话') detail.value.title = q.slice(0, 20);
      }
      listTick.value++;
      return true;
    } catch (e) {
      if (valid()) { draft.value = q; message.error(`提问失败：${errText(e)}`); }
      return false;
    } finally { if (valid()) { sending.value = false; streamStatus.value = ''; } }
  }
  function send(text?: string) {
    const q = (text ?? draft.value).trim();
    if (!q) return false;
    // shortcut: 只识别单纯切换图表的追问；涉及新筛选条件时继续交给问数。
    const chart = q.replace(/[\s，,。！？!?]/g, '').match(/^(?:我)?(?:需要|想|请)?(?:用|以|改为|换成|切换为)?(柱状图|柱形图|条形图|折线图)(?:展示|显示|查看|呈现)?$/);
    const history = detail.value?.turns ?? [];
    const last = history[history.length - 1];
    if (real && !sending.value && chart && last?.kind === 'answer' && last.answer.card === 'table') {
      const requests = { ...chartRequests.value };
      delete requests[last.id];
      chartRequests.value = { ...requests, [last.id]: { type: chart[1] === '折线图' ? 'line' : 'bar' } };
      draft.value = '';
      return true;
    }
    if (real) { void sendReal(q); return true; }
    const list = turns.value[activeId.value] ?? (turns.value[activeId.value] = []);
    list.push({ id: ++seq, q, time: now() });
    const s = sessions.value.find((x) => x.id === activeId.value);
    if (s && s.title === '新会话') s.title = q.slice(0, 20);
    draft.value = '';
    return true;
  }
  async function newSessionReal(scope: string, caliber: string) {
    if (creating.value) return '';
    const my = ++createSeq, key = contextKey(), selected = activeId.value;
    const valid = () => my === createSeq && key === contextKey() && selected === activeId.value;
    creating.value = true;
    try {
      const d = await qaApi.createSession({ scope, caliber }, qaApi.qaRequestOptions());
      if (!valid()) return '';
      detail.value = d; activeId.value = d.id; draft.value = '';
      listTick.value++;
      return d.id;
    } catch (e) { if (valid()) message.error(`新建会话失败：${errText(e)}`); return ''; }
    finally { if (my === createSeq) creating.value = false; }
  }
  function newSession() {
    const id = `NEW-${++newSeq}`;
    sessions.value.unshift({ id, title: '新会话', date: QA_TODAY });
    activeId.value = id;
    draft.value = '';
    return id;
  }
  return { streamStatus, sessions, activeId, turns, draft, isNew, activeTurns, send, newSession, real, live, detail, loading, sending, creating, listTick, favTick, loadDetail, newSessionReal };
}
