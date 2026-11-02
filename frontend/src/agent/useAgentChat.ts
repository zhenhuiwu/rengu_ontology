import { ref, shallowRef } from 'vue';
import { publicApi, PublicApiError, type PublicConfig, type PublicSession, type PublicTurn } from './api';

const POINTER_KEY = 'rengu-agent:public-session';
const publicId = (id: unknown): id is string => typeof id === 'string' && /^P_[A-Za-z0-9_-]{43}$/.test(id);
export const validOntology = (id: unknown): id is string => typeof id === 'string' && /^[1-9]\d*$/.test(id) && Number.isSafeInteger(Number(id));

/** Each mounted public app owns its state; no platform store, cached identity, or mock fallback. */
export function createAgentChat(api = publicApi, storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>) {
  const config = shallowRef<PublicConfig | null>(null);
  const ontologyId = ref(''), sessionId = ref(''), turns = ref<PublicTurn[]>([]);
  const draft = ref(''), pendingQuestion = ref(''), loading = ref(false), sending = ref(false), error = ref('');
  let epoch = 0, controller = new AbortController();
  function invalidate() { epoch++; controller.abort(); controller = new AbortController(); return epoch; }
  function forget() { try { storage?.removeItem(POINTER_KEY); } catch { /* Storage may be unavailable. */ } }
  function remember(id: string) {
    try { storage?.setItem(POINTER_KEY, JSON.stringify({ ontologyId: ontologyId.value, sessionId: id })); } catch { /* Refresh recovery is optional. */ }
  }
  function savedId(id: string) {
    try {
      const saved = JSON.parse(storage?.getItem(POINTER_KEY) ?? 'null');
      return saved?.ontologyId === id && publicId(saved.sessionId) ? saved.sessionId as string : '';
    } catch { return ''; }
  }
  function acceptSession(session: PublicSession, expected: string) {
    if (String(session.ontologyId) !== expected || !publicId(session.id) || !Array.isArray(session.turns)) throw new Error('会话范围不匹配，请新建会话');
    sessionId.value = session.id; turns.value = session.turns; remember(session.id);
  }
  async function load(raw: unknown) {
    const ticket = invalidate();
    config.value = null; ontologyId.value = ''; sessionId.value = ''; turns.value = [];
    draft.value = ''; pendingQuestion.value = ''; sending.value = false; error.value = ''; loading.value = true;
    if (raw !== undefined && !validOntology(raw)) { error.value = '本体编号无效，请重新选择本体'; loading.value = false; return; }
    const id = typeof raw === 'string' ? raw : '';
    try {
      const result = await api.config(id, controller.signal);
      if (ticket !== epoch) return;
      if (id && (!result.selected || String(result.selected.id) !== id)) throw new Error('此本体尚未开放，请选择其他本体');
      config.value = result; ontologyId.value = id;
      const saved = id ? savedId(id) : '';
      if (saved) {
        try {
          const session = await api.session(id, saved, controller.signal);
          if (ticket === epoch) acceptSession(session, id);
        } catch (e) {
          if (ticket !== epoch) return;
          forget();
          if (!(e instanceof PublicApiError && e.status === 404)) throw e;
          error.value = '上次会话已失效，可直接开始新会话';
        }
      }
    } catch (e) {
      if (ticket === epoch) { config.value = null; ontologyId.value = ''; error.value = e instanceof Error ? e.message : '暂时无法连接问数服务'; }
    }
    finally { if (ticket === epoch) loading.value = false; }
  }
  function newSession() {
    invalidate(); forget(); sessionId.value = ''; turns.value = []; draft.value = ''; pendingQuestion.value = '';
    sending.value = false; error.value = '';
  }
  async function send(text = draft.value) {
    const question = text.trim();
    if (!question || sending.value || loading.value || !ontologyId.value || !config.value?.selected) return;
    if (question.length > 500) { draft.value = question; error.value = '问题最多支持 500 个字符，请精简后发送'; return; }
    const ticket = epoch, id = ontologyId.value;
    sending.value = true; pendingQuestion.value = question; error.value = ''; draft.value = '';
    try {
      if (!sessionId.value) {
        const session = await api.create(id, controller.signal);
        if (ticket !== epoch) return;
        acceptSession(session, id);
      }
      const result = await api.ask(id, sessionId.value, question, controller.signal);
      if (ticket !== epoch) return;
      turns.value = [...turns.value, result.turn];
    } catch (e) {
      if (ticket === epoch) { error.value = e instanceof Error ? e.message : '提问失败，请稍后重试'; draft.value = question; }
    } finally { if (ticket === epoch) { sending.value = false; pendingQuestion.value = ''; } }
  }
  return { config, ontologyId, sessionId, turns, draft, pendingQuestion, loading, sending, error, load, newSession, send, dispose: invalidate };
}
