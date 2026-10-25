/**
 * 问数工作台的会话状态（会话列表、对话区、输入框共用）。
 * 原型：发送后用固定的示例回答追加一轮；新建会话得到一个空会话。
 */
import { ref, computed } from 'vue';
import { QA_SESSION_LIST, QA_TODAY } from '@/mock';
import type { QaSession } from '@/mock/qa';

export interface QaTurn { id: number; q: string; time: string; }

const sessions = ref<QaSession[]>([...QA_SESSION_LIST]);
const activeId = ref<string>(QA_SESSION_LIST[0].id);
/** 每个会话里本次新追加的轮次（原有历史轮次由 QaConversation 固定渲染，只属于第一个会话） */
const turns = ref<Record<string, QaTurn[]>>({});
const draft = ref('');
let seq = 0;
let newSeq = 0;

const now = () => { const d = new Date(); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };

export function useQaSession() {
  /** 新建的会话没有历史轮次，对话区显示空状态 */
  const isNew = computed(() => activeId.value.startsWith('NEW-'));
  const activeTurns = computed(() => turns.value[activeId.value] ?? []);
  function send(text?: string) {
    const q = (text ?? draft.value).trim();
    if (!q) return false;
    const list = turns.value[activeId.value] ?? (turns.value[activeId.value] = []);
    list.push({ id: ++seq, q, time: now() });
    const s = sessions.value.find((x) => x.id === activeId.value);
    if (s && s.title === '新会话') s.title = q.slice(0, 20);
    draft.value = '';
    return true;
  }
  function newSession() {
    const id = `NEW-${++newSeq}`;
    sessions.value.unshift({ id, title: '新会话', date: QA_TODAY });
    activeId.value = id;
    draft.value = '';
    return id;
  }
  return { sessions, activeId, turns, draft, isNew, activeTurns, send, newSession };
}
