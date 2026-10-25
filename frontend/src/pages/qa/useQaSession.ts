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
import { isApiError } from '@/api/http';
import { qaLive, qaFallBackIfDown } from './qaFallback';

/** real：开关 qa（写操作按它走真接口）；live：渲染 / 读接口（后台没起来时回退 mock） */
const real = qaApi.qaReal();
const live = qaLive;

export interface QaTurn { id: number; q: string; time: string; }

const sessions = ref<QaSession[]>([...QA_SESSION_LIST]);
const activeId = ref<string>(live.value ? '' : QA_SESSION_LIST[0].id);
/** 真接口：当前会话详情（含全部轮次）；listTick 变了左栏重取当前页 */
const detail = ref<qaApi.QaSessionDetail | null>(null);
const loading = ref(false);
const sending = ref(false);
const listTick = ref(0);
/** favTick 变了左栏底部「常用问题」前 3 条重取（抽屉里移出后） */
const favTick = ref(0);
let loadSeq = 0;

export const errText = (e: unknown) => (isApiError(e) ? `${e.message || e.code}（${e.code}）` : String(e));

async function loadDetail(id = activeId.value) {
  if (!live.value || !id) return;
  const my = ++loadSeq;
  loading.value = true;
  try {
    const d = await qaApi.getSession(id);
    if (my === loadSeq) detail.value = d;
  } catch (e) {
    if (my === loadSeq) { detail.value = null; if (!qaFallBackIfDown(e)) message.error(`会话加载失败：${errText(e)}`); }
  } finally { if (my === loadSeq) loading.value = false; }
}
/** 每个会话里本次新追加的轮次（原有历史轮次由 QaConversation 固定渲染，只属于第一个会话） */
const turns = ref<Record<string, QaTurn[]>>({});
// 后台没起来 → 回退 mock：当前会话切到示例会话
watch(live, (on) => { if (!on) { loadSeq++; loading.value = false; detail.value = null; activeId.value = QA_SESSION_LIST[0].id; } });
const draft = ref('');
let seq = 0;
let newSeq = 0;

const now = () => { const d = new Date(); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };

/** 反问选项 → 自包含追问句（链路无多轮上下文）[推断]：短选项替换原问题里的 term；长句选项去掉开头「是，」直接当问题；否则「原问题（选项）」 */
export function clarifyFollowUp(question: string, term: string, label: string) {
  if (term && question.includes(term) && label.length <= term.length + 6) return question.replace(term, label);
  if (label.length >= 8) return label.replace(/^是[，,、\s]*/, '');
  return `${question}（${label}）`;
}

export function useQaSession() {
  /** 新建的会话没有历史轮次，对话区显示空状态 */
  const isNew = computed(() => (live.value ? !!detail.value && detail.value.id === activeId.value && !detail.value.turns.length : activeId.value.startsWith('NEW-')));
  const activeTurns = computed(() => turns.value[activeId.value] ?? []);
  async function sendReal(q: string) {
    const sid = activeId.value;
    if (!sid || sending.value) return false;
    sending.value = true;
    draft.value = '';
    try {
      const r = await qaApi.askTurn(sid, q);
      if (detail.value?.id === sid) detail.value.turns.push(r.turn);
      if (detail.value?.id === sid && detail.value.title === '新会话') detail.value.title = q.slice(0, 20);
      listTick.value++;
      return true;
    } catch (e) {
      draft.value = q; // 失败时把问题放回输入框
      message.error(`提问失败：${errText(e)}`);
      return false;
    } finally { sending.value = false; }
  }
  function send(text?: string) {
    const q = (text ?? draft.value).trim();
    if (!q) return false;
    if (real) { void sendReal(q); return true; }
    const list = turns.value[activeId.value] ?? (turns.value[activeId.value] = []);
    list.push({ id: ++seq, q, time: now() });
    const s = sessions.value.find((x) => x.id === activeId.value);
    if (s && s.title === '新会话') s.title = q.slice(0, 20);
    draft.value = '';
    return true;
  }
  async function newSessionReal(scope: string, caliber: string) {
    try {
      const d = await qaApi.createSession({ scope, caliber });
      detail.value = d; activeId.value = d.id; draft.value = '';
      listTick.value++;
      return d.id;
    } catch (e) { message.error(`新建会话失败：${errText(e)}`); return ''; }
  }
  function newSession() {
    const id = `NEW-${++newSeq}`;
    sessions.value.unshift({ id, title: '新会话', date: QA_TODAY });
    activeId.value = id;
    draft.value = '';
    return id;
  }
  return { sessions, activeId, turns, draft, isNew, activeTurns, send, newSession, real, live, detail, loading, sending, listTick, favTick, loadDetail, newSessionReal };
}
