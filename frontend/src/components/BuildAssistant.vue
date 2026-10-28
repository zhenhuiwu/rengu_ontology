<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { CloseOutlined, MessageOutlined, SendOutlined, FullscreenOutlined, FullscreenExitOutlined } from '@ant-design/icons-vue';
import { useAppStore } from '@/stores/app';
import { clearAssistantPrefill, generateAssistantDraft, putAssistantPrefill, type AssistantDraft, type AssistantTarget, type AssistantEdit } from '@/api/assistant';
import { currentApiContext } from '@/api/http';
import { getFunction } from '@/api/functions';

const app = useAppStore();
const route = useRoute();
const router = useRouter();
const choices: { value: AssistantTarget; label: string; route: string }[] = [
  { value: 'function', label: '函数', route: 'functions' },
  { value: 'object', label: '对象类型', route: 'object-types' },
  { value: 'relation', label: '关系类型', route: 'link-types' },
  { value: 'action', label: '动作类型', route: 'action-types' },
  { value: 'metric', label: '指标口径', route: 'metrics' },
  { value: 'dimension', label: '维度', route: 'dimensions' },
  { value: 'dictionary', label: '字典', route: 'dimensions' },
  { value: 'knowledge', label: '业务知识', route: 'glossary' },
  { value: 'event', label: '事件类型', route: 'event-types' },
  { value: 'rule', label: '规则', route: 'rules' },
];
const open = ref(false);
const maximized = ref(false);
const target = ref<AssistantTarget | 'auto'>('auto');
const prompt = ref('');
const result = ref<AssistantDraft | null>(null);
const acceptedFields = ref<string[]>([]);
const history = ref<{ role: 'user' | 'assistant'; content: string }[]>([]);
const error = ref('');
const loading = ref(false);
const messages = ref<HTMLElement | null>(null);
const resultScope = ref<{ path: string; version: string } | null>(null);
const editing = ref<(AssistantEdit & { name: string; base: Record<string, unknown> }) | null>(null);
const storageNotice = ref('');
const sessionKey = computed(() => `ontology:assistant:v1:${encodeURIComponent(currentApiContext().demoAs || app.persona)}:${encodeURIComponent(app.ontologyId)}:${encodeURIComponent(String(route.name || 'other'))}${editing.value ? ':edit:' + encodeURIComponent(editing.value.targetId) + ':' + encodeURIComponent(editing.value.version) + ':' + editing.value.lockVersion : ''}`);
let activeKey = '';
let loadingSession = false;
let requestId = 0;
function editFunction(event: Event) {
  const value = (event as CustomEvent).detail;
  if (!value || route.name !== 'functions' || value.targetId !== route.query.fn || currentApiContext().ver || currentApiContext().previewAs) return;
  editing.value = value; open.value = true;
}
onMounted(() => window.addEventListener('assistant-edit-function', editFunction));
onBeforeUnmount(() => window.removeEventListener('assistant-edit-function', editFunction));
function saveSession() {
  if (!activeKey || loadingSession) return;
  const content = JSON.stringify({ v: 1, prompt: prompt.value, history: history.value, result: result.value, resultScope: resultScope.value });
  if (content.length > 120000 || /(?:api[_ -]?key|bearer\s+|password|密码|密钥)/i.test(content)) {
    try { sessionStorage.removeItem(activeKey); } catch { /* storage unavailable */ }
    storageNotice.value = '当前内容未保存在本标签页，请勿输入密钥或密码。';
    return;
  }
  try {
    const keys = Object.keys(sessionStorage).filter(key => key.startsWith('ontology:assistant:v1:'));
    if (!keys.includes(activeKey) && keys.length >= 20) { storageNotice.value = '本标签页已保存 20 个对话，请先清理不需要的对话。'; return; }
    sessionStorage.setItem(activeKey, content); storageNotice.value = '';
  }
  catch { storageNotice.value = '浏览器未能保存本次对话，刷新后可能丢失。'; }
}
function loadSession(key: string) {
  loadingSession = true;
  prompt.value = ''; history.value = []; result.value = null; resultScope.value = null; error.value = ''; storageNotice.value = '';
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw || raw.length > 120000) return;
    const saved = JSON.parse(raw);
    if (saved.v !== 1 || typeof saved.prompt !== 'string' || saved.prompt.length > 2000 || !Array.isArray(saved.history) || saved.history.length > 20 || !saved.history.every((item: any) => item && ['user', 'assistant'].includes(item.role) && typeof item.content === 'string' && item.content.length <= 24000)) return;
    prompt.value = saved.prompt; history.value = saved.history;
    if (saved.result && typeof saved.result === 'object' && typeof saved.result.target === 'string' && typeof saved.result.message === 'string' && saved.result.draft && typeof saved.result.draft === 'object' && Array.isArray(saved.result.warnings) && saved.resultScope && typeof saved.resultScope.path === 'string' && typeof saved.resultScope.version === 'string') {
      result.value = saved.result; resultScope.value = saved.resultScope;
    }
  } catch { /* invalid or unavailable storage */ }
  finally { loadingSession = false; }
}
function clearConversation() {
  requestId++; loading.value = false; prompt.value = ''; history.value = []; result.value = null; resultScope.value = null; error.value = '';
  clearAssistantPrefill();
  try { sessionStorage.removeItem(activeKey); storageNotice.value = ''; } catch { /* storage unavailable */ }
}
watch(sessionKey, (key) => {
  if (activeKey) saveSession();
  requestId++; loading.value = false; activeKey = key; loadSession(key); clearAssistantPrefill();
}, { immediate: true, flush: 'sync' });
watch([prompt, history, result, resultScope], saveSession, { deep: true, flush: 'sync' });
watch(result, () => { acceptedFields.value = []; });
watch([history, result, error, loading], async () => {
  await nextTick();
  if (messages.value) messages.value.scrollTop = messages.value.scrollHeight;
}, { deep: true });
function onPromptKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  void generate();
}
watch(() => route.name, name => {
  const choice = choices.find(c => c.route === name);
  if (choice) target.value = choice.value; else target.value = 'auto';
}, { immediate: true });
watch(() => route.fullPath, (path, previous) => {
  if (path !== previous) { requestId++; loading.value = false; }
  if (editing.value && (route.name !== 'functions' || route.query.fn !== editing.value.targetId)) editing.value = null;
}, { flush: 'sync' });
function modelHistory() {
  const older = history.value.slice(0, -6).filter(item => item.role === 'user').map(item => item.content);
  const recent = history.value.slice(-6);
  const groups: { role: 'user'; content: string }[] = [];
  for (const [index, text] of older.entries()) {
    const line = `${index + 1}. ${text}\n`;
    if (!groups.length || groups[groups.length - 1].content.length + line.length > 4000) groups.push({ role: 'user', content: '此前按时间顺序提出的需求与修改（后提出的修改优先）：\n' });
    groups[groups.length - 1].content += line;
  }
  const combined = [...groups, ...recent];
  if (combined.length > 8 || combined.some(item => item.content.length > (item.role === 'user' ? 4000 : 24000)) || combined.reduce((sum, item) => sum + item.content.length, 0) > 48000) throw new Error('本次对话内容过长，请新建对话并概括仍需保留的要求。');
  return combined;
}
async function generate() {
  if (!prompt.value.trim() || loading.value) return;
  if (!editing.value && route.name === 'functions' && typeof route.query.fn === 'string' && /(?:修改|编辑|调整|修复|优化).{0,12}(?:当前|这个|此|该|已有)?函数|(?:当前|这个|此|该|已有)函数.{0,12}(?:修改|编辑|调整|修复|优化)/.test(prompt.value)) {
    const id = route.query.fn, question = prompt.value, context = JSON.stringify(currentApiContext());
    loading.value = true;
    try {
      const f = await getFunction(id, {});
      if (JSON.stringify(currentApiContext()) !== context || route.query.fn !== id || route.name !== 'functions') return;
      if (f.status !== '已发布') throw new Error('请选择已发布的函数后再修改');
      editing.value = { targetId: f.id, lockVersion: f.lockVersion, version: f.version, name: f.name, base: { title: f.title, desc: f.desc, timeoutS: f.timeoutS, code: f.code } };
      prompt.value = question;
    } catch (e) { error.value = (e as Error).message || '无法读取当前函数'; return; }
    finally { loading.value = false; }
  }
  if (history.value.length >= 20) { error.value = '本次对话已达到 10 轮，请新建对话并概括仍需保留的要求。'; return; }
  let prior: ReturnType<typeof modelHistory>;
  try { prior = modelHistory(); } catch (e) { error.value = (e as Error).message; return; }
  const id = ++requestId;
  const path = route.fullPath, version = app.ontologyVersion, key = activeKey;
  const question = prompt.value.trim();
  const requested = /(?:新建|创建|生成|构建).{0,20}(?:规则|自动化)/.test(question) ? 'rule'
    : /(?:新建|创建|生成|构建).{0,20}事件/.test(question) ? 'event' : undefined;
  const explicit = choices.find(c => c.value === requested) ?? choices.find(c => ({ function: /函数/, object: /对象类型|对象建模/, relation: /关系类型|关联关系/, action: /动作类型|创建动作/, metric: /指标/, dimension: /维度/, dictionary: /字典|码表/, knowledge: /业务知识|知识术语/, event: /(?:新建|创建|生成|构建).{0,20}事件/, rule: /(?:新建|创建|生成|构建).{0,20}(?:规则|自动化)/ }[c.value].test(question)));
  const selected = editing.value ? 'function' : explicit?.value ?? (result.value?.target !== 'auto' ? result.value?.target : undefined) ?? target.value;
  if (result.value && result.value.target !== selected && result.value.target !== 'auto') { error.value = '当前对话属于其他模块，请新建对话再切换。'; return; }
  error.value = ''; loading.value = true;
  try {
    const edit = editing.value ? { targetId: editing.value.targetId, lockVersion: editing.value.lockVersion, version: editing.value.version } : undefined;
    const response = await generateAssistantDraft(selected, question, prior, edit);
    if (id === requestId && key === activeKey && path === route.fullPath && version === app.ontologyVersion) {
      history.value.push({ role: 'user', content: question }, { role: 'assistant', content: JSON.stringify(response) });
      prompt.value = ''; result.value = response; resultScope.value = { path, version };
    }
  } catch (e) {
    if (id === requestId && (e as Error).name !== 'AbortError') error.value = (e as Error).message || '生成失败，请重试';
  } finally { if (id === requestId) loading.value = false; }
}
async function apply() {
  if (!result.value) return;
  if (!resultScope.value || resultScope.value.path !== route.fullPath || resultScope.value.version !== app.ontologyVersion || app.ontologyVersion === '—' || currentApiContext().ver || currentApiContext().previewAs) { error.value = '页面或本体版本已变化，请重新生成建议。'; return; }
  const choice = choices.find(c => c.value === result.value!.target);
  if (!choice) return;
  if (result.value.target === 'auto') return;
  const draft = result.value.edit ? Object.fromEntries(Object.entries(result.value.draft).filter(([key]) => acceptedFields.value.includes(key))) : result.value.draft;
  if (result.value.edit && !Object.keys(draft).length) { error.value = '请选择要采用的修改。'; return; }
  const prefill = { target: result.value.target, draft, ontologyId: app.ontologyId, version: app.ontologyVersion, identity: currentApiContext().demoAs || app.persona, edit: result.value.edit };
  const sourceKey = activeKey, version = app.ontologyVersion;
  putAssistantPrefill(prefill);
  try {
    const failure = route.name === choice.route ? undefined : await router.push({ name: choice.route, query: { ...route.query, ontology: app.ontologyId } });
    if (failure || route.name !== choice.route || app.ontologyId !== prefill.ontologyId || app.ontologyVersion !== version || sourceKey.split(':')[3] !== encodeURIComponent(currentApiContext().demoAs || app.persona)) { clearAssistantPrefill(); error.value = '上下文已变化，请重新生成建议'; return; }
    await nextTick();
    window.dispatchEvent(new CustomEvent('assistant-prefill', { detail: prefill }));
    open.value = false;
    result.value = null;
  } catch { clearAssistantPrefill(); error.value = '无法进入目标页面，请重试'; }
}
function assistantMessage(content: string): string {
  try { return (JSON.parse(content) as AssistantDraft).message; } catch { return content; }
}
function display(value: unknown): string {
  if (value == null) return '—';
  if (Array.isArray(value)) return value.map(display).join('、');
  if (typeof value === 'object') return Object.entries(value).map(([k, v]) => `${k}：${display(v)}`).join('；');
  return String(value);
}
function toggleField(key: string, checked: boolean) {
  acceptedFields.value = checked ? [...acceptedFields.value, key] : acceptedFields.value.filter(value => value !== key);
}
</script>

<template>
  <div class="assistant">
    <button v-if="!open" class="assistant-launch" type="button" aria-label="打开构建助手" @click="open = true"><MessageOutlined /> <span>构建助手</span></button>
    <section v-else class="assistant-panel" :class="{ maximized }" aria-label="构建助手">
      <header class="assistant-header"><strong>构建助手<span v-if="editing"> · 修改 {{ editing.name }}（{{ editing.version }}）</span></strong><div class="assistant-header-actions"><a-popconfirm title="清空当前页面的对话和未发送内容？" ok-text="清空" cancel-text="取消" @confirm="clearConversation"><button type="button" class="assistant-clear">新建对话</button></a-popconfirm><button type="button" class="assistant-close" :aria-label="maximized ? '恢复窗口' : '放大窗口'" @click="maximized = !maximized"><FullscreenExitOutlined v-if="maximized" /><FullscreenOutlined v-else /></button><button type="button" class="assistant-close" aria-label="关闭助手" @click="open = false"><CloseOutlined /></button></div></header>
      <div ref="messages" class="assistant-body">
        <div v-if="!history.length && !result" class="assistant-empty">
          <template v-if="editing"><strong>想如何修改这个函数？</strong><p>描述需要调整的内容，其余定义会保留。</p></template>
          <template v-else><strong>有什么可以帮你构建？</strong><p>例如：创建一个函数，根据状态生成标签</p><p>或者描述一个对象、关系或规则</p></template>
        </div>
        <div v-if="history.length" class="assistant-history"><template v-for="(turn, index) in history" :key="index"><div v-if="turn.role === 'user'" class="assistant-turn"><b>你</b><p>{{ turn.content }}</p></div><div v-else class="assistant-turn reply"><b>助手</b><p>{{ assistantMessage(turn.content) }}</p></div></template></div>
        <div v-if="loading" class="assistant-pending">正在生成建议…</div>
        <div v-if="error" class="assistant-error" role="alert">{{ error }}</div>
        <div v-if="storageNotice" class="assistant-error" role="status">{{ storageNotice }}</div>
        <div v-if="result" class="assistant-result">
          <div class="assistant-result-title">建议预览 · {{ result.edit ? `修改 ${editing?.name || result.edit.targetId}（${result.edit.version}）` : choices.find(c => c.value === result?.target)?.label || '待明确模块' }} <span>{{ result.target === 'function' ? '待校验' : '待确认' }}</span></div>
          <p>{{ result.message }}</p>
          <dl><template v-for="(value, key) in result.draft" :key="key"><dt><a-checkbox v-if="result.edit" :checked="acceptedFields.includes(String(key))" @change="(e: any) => toggleField(String(key), e.target.checked)">{{ key }}</a-checkbox><template v-else>{{ key }}</template></dt><dd v-if="result.edit && editing">原值：{{ display(editing.base[String(key)]) }}</dd><dd>{{ result.edit ? '建议：' : '' }}{{ display(value) }}</dd></template></dl>
          <ul v-if="result.warnings?.some(w => !w.includes('函数代码未经验证'))"><li v-for="warning in result.warnings.filter(w => !w.includes('函数代码未经验证'))" :key="warning">{{ warning }}</li></ul>
          <p class="assistant-note">确认后进入表单检查和编辑，助手不会自动保存。</p>
          <a-button v-if="result.target !== 'auto' && Object.keys(result.draft).length" type="primary" block :disabled="loading" @click="apply">确认并进入表单</a-button>
        </div>
      </div>
      <div class="assistant-composer">
        <div class="assistant-input-box">
          <a-textarea id="assistant-prompt" v-model:value="prompt" :auto-size="{ minRows: 2, maxRows: 6 }" :maxlength="2000" :disabled="loading" aria-label="输入构建需求" placeholder="描述你想构建或调整的内容" @keydown="onPromptKeydown" />
          <div class="assistant-composer-actions"><span>Enter 发送 · Shift + Enter 换行</span><a-button type="primary" :loading="loading" :disabled="!prompt.trim() || loading" aria-label="发送" @click="generate"><template #icon><SendOutlined /></template>发送</a-button></div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.assistant { position: fixed; right: 24px; bottom: 24px; z-index: 1000; }
.assistant-launch { border: 0; border-radius: 24px; background: #2457d6; color: white; height: 48px; padding: 0 20px; font-weight: 600; box-shadow: 0 8px 24px #163b8d33; cursor: pointer; display: flex; align-items: center; gap: 8px; }
.assistant-launch:hover { background: #1d4cbd; }
.assistant-panel { width: min(880px, calc(100vw - 32px)); height: min(82dvh, calc(100dvh - 48px)); display: flex; flex-direction: column; background: white; border: 1px solid #e3e8f1; border-radius: 16px; box-shadow: 0 16px 48px #172a4d2b; overflow: hidden; }
.assistant-panel.maximized { width: calc(100vw - 48px); height: calc(100dvh - 48px); }
.assistant-header { flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; padding: 14px 20px; border-bottom: 1px solid #e3e8f1; }
.assistant-header-actions { display: flex; align-items: center; gap: 12px; }
.assistant-header-actions :deep(.ant-select) { width: 150px; }
.assistant-header strong { display: block; font-size: 16px; color: #18243a; }
.assistant-header small { display: block; margin-top: 4px; color: #68778d; }
.assistant-close { border: 0; background: transparent; color: #64748b; padding: 5px; cursor: pointer; }
.assistant-clear { border: 0; background: transparent; color: #53677f; font-size: 13px; cursor: pointer; }
.assistant-context { display: grid; grid-template-columns: auto 1fr; gap: 5px 12px; background: #f5f8fd; padding: 12px 20px; font-size: 12px; color: #75839a; }
.assistant-context strong { color: #34445c; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.assistant-body { flex: 1; min-height: 0; min-width: 0; overflow: auto; padding: 20px 24px; }
.assistant-empty { color: #718096; padding: 12px 0; }
.assistant-empty strong { color: #334155; font-size: 16px; }
.assistant-empty p { margin: 10px 0 0; font-size: 13px; }
.assistant-history { display: flex; flex-direction: column; gap: 12px; }
.assistant-turn { align-self: flex-end; max-width: 85%; min-width: 0; padding: 10px 14px; border-radius: 12px; background: #edf3ff; font-size: 13px; overflow-wrap: anywhere; }
.assistant-turn.reply { align-self: flex-start; background: #f5f7fa; }
.assistant-turn b { color: #53677f; }
.assistant-turn p { white-space: pre-wrap; margin: 4px 0 0; }
.assistant-pending { margin-top: 12px; color: #718096; font-size: 13px; }
.assistant-composer { flex-shrink: 0; padding: 12px 20px 18px; border-top: 1px solid #e3e8f1; background: #fff; }
.assistant-input-box { border: 1px solid #d9dfe9; border-radius: 10px; padding: 4px 6px 6px; }
.assistant-input-box:focus-within { border-color: #2457d6; box-shadow: 0 0 0 2px #2457d61a; }
.assistant-composer :deep(textarea) { resize: none; border: 0; box-shadow: none; }
.assistant-composer-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 0 4px; color: #8793a4; font-size: 12px; }
.assistant-error { margin-top: 12px; color: #b42318; }
.assistant-result { min-width: 0; overflow-x: auto; margin-top: 18px; border: 1px solid #dce6f8; border-radius: 10px; padding: 14px; background: #f9fbff; }
.assistant-result-title { font-weight: 600; color: #20365e; }
.assistant-result-title span { float: right; color: #8a6614; font-size: 12px; font-weight: 400; }
.assistant-result p { margin: 10px 0; white-space: pre-wrap; }
.assistant-result dl { margin: 0; font-size: 12px; }
.assistant-result dt { color: #718096; margin-top: 9px; }
.assistant-result dd { margin: 2px 0 0; color: #283b55; overflow-wrap: anywhere; white-space: pre-wrap; }
.assistant-result ul { padding-left: 18px; color: #906519; }
.assistant-note { color: #718096; font-size: 12px; }
button:focus-visible { outline: 2px solid #2457d6; outline-offset: 3px; }
</style>
