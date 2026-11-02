<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { CloseOutlined, PlusOutlined, SendOutlined, MessageOutlined } from '@ant-design/icons-vue';
import AgentAnswer from './AgentAnswer.vue';
import { createAgentChat } from './useAgentChat';

const route = useRoute(), router = useRouter();
let storage: Storage | undefined;
try { storage = window.sessionStorage; } catch { /* Private browsing can disable storage. */ }
const chat = createAgentChat(undefined, storage);
const { config, ontologyId, turns, draft, pendingQuestion, loading, sending, error } = chat;
const messages = ref<HTMLElement | null>(null);
const choosing = ref(false);
const navy = computed(() => route.query.theme === 'navy' || route.query.theme === 'dark');
const embedded = window.parent !== window;
const externalClose = computed(() => embedded && route.query.embed !== '1');
const selected = computed(() => config.value?.selected);
const choices = computed(() => config.value?.workspaces.map(w => ({ value: String(w.id), label: w.label })) ?? []);
const canSend = computed(() => !!selected.value && !loading.value && !sending.value && !!draft.value.trim());
let parentOrigin = '';
try { const source = new URL(document.referrer); if (/^https?:$/.test(source.protocol)) parentOrigin = source.origin; } catch { /* Standalone document. */ }
function notify(type: 'rengu-agent:close' | 'rengu-agent:ready') { if (embedded && parentOrigin) window.parent.postMessage({ type }, parentOrigin); }
function escape(event: KeyboardEvent) { if (event.key === 'Escape') notify('rengu-agent:close'); }
function choose(id: string) { choosing.value = false; void router.replace({ query: { ...route.query, ontology: id } }); }
function chooseOther() { choosing.value = false; void router.replace({ query: { ...route.query, ontology: undefined } }); }
function sendKey(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); if (canSend.value) void chat.send(); }
}
watch(() => route.query.ontology, (id) => { choosing.value = false; void chat.load(id); }, { immediate: true, flush: 'sync' });
watch(() => [turns.value.length, pendingQuestion.value, error.value], async () => {
  await nextTick(); messages.value?.scrollTo({ top: messages.value.scrollHeight, behavior: 'auto' });
});
onMounted(() => { window.addEventListener('keydown', escape); notify('rengu-agent:ready'); });
onBeforeUnmount(() => { chat.dispose(); window.removeEventListener('keydown', escape); });
</script>

<template>
  <main class="agent-shell">
    <header class="agent-header">
      <div class="agent-heading">
        <h1 class="t-16 fw-600">{{ selected?.label || '智能问数' }}</h1>
        <a-button v-if="selected && choices.length > 1" size="small" type="text" @click="choosing = !choosing">切换本体</a-button>
      </div>
      <div class="agent-header-actions">
        <a-button :disabled="!selected || loading" @click="chat.newSession()"><template #icon><PlusOutlined /></template>新会话</a-button>
        <a-button v-if="externalClose" type="text" aria-label="关闭问数" @click="notify('rengu-agent:close')"><template #icon><CloseOutlined /></template></a-button>
      </div>
    </header>
    <div v-if="choosing" class="agent-switch">
      <label for="agent-ontology">选择本体</label>
      <a-select id="agent-ontology" :value="ontologyId" :options="choices" class="agent-selector" @change="choose" />
    </div>
    <section ref="messages" class="agent-messages scroll-area" aria-label="对话记录" aria-live="polite" :aria-busy="loading || sending">
      <div class="agent-message-inner">
        <div v-if="loading" class="agent-center"><a-spin /><span class="c-3">正在载入会话</span></div>
        <div v-else-if="!selected" class="agent-welcome">
          <MessageOutlined class="agent-welcome-icon c-brand" />
          <h2 class="t-20 fw-600">选择本体，开始问数</h2>
          <p class="c-3">围绕当前本体提问，查看回答与数据。</p>
          <a-select v-if="choices.length" aria-label="选择本体" placeholder="请选择本体" :options="choices" class="agent-welcome-selector" @change="choose" />
          <p v-else-if="config && !error" class="c-3">暂无可用本体</p>
        </div>
        <div v-else-if="!turns.length && !pendingQuestion" class="agent-welcome">
          <MessageOutlined class="agent-welcome-icon c-brand" />
          <h2 class="t-20 fw-600">有什么想了解的？</h2>
          <p class="c-3">输入问题，查询「{{ selected.label }}」中的数据。</p>
        </div>
        <article v-for="turn in turns" :key="turn.id" class="agent-turn">
          <div class="agent-question"><span class="agent-question-bubble">{{ turn.question }}</span></div>
          <div class="agent-response">
            <span class="agent-response-label t-12 c-3">智能问数</span>
            <AgentAnswer :turn="turn" :busy="sending" :navy="navy" @clarify="chat.send" />
          </div>
        </article>
        <div v-if="pendingQuestion" class="agent-turn">
          <div class="agent-question"><span class="agent-question-bubble">{{ pendingQuestion }}</span></div>
          <div class="agent-pending c-3"><a-spin size="small" /><span>正在思考</span></div>
        </div>
        <div v-if="error" class="agent-error" role="alert">
          <a-alert type="error" show-icon :message="error" />
          <div class="agent-error-actions">
            <a-button size="small" @click="chat.load(route.query.ontology)">重试连接</a-button>
            <a-button v-if="!selected" size="small" type="text" @click="chooseOther">选择其他本体</a-button>
          </div>
        </div>
      </div>
    </section>
    <footer class="agent-composer">
      <div class="agent-composer-inner">
        <div class="agent-input-wrap">
          <a-textarea v-model:value="draft" aria-label="输入问题" placeholder="输入问题…" :disabled="!selected || loading || sending" :auto-size="{ minRows: 2, maxRows: 6 }" :maxlength="500" @keydown="sendKey" />
          <a-button type="primary" :disabled="!canSend" :loading="sending" aria-label="发送问题" @click="chat.send()"><template #icon><SendOutlined v-if="!sending" /></template>发送</a-button>
        </div>
        <div class="agent-input-help t-12 c-3">Enter 发送 · Shift + Enter 换行</div>
      </div>
    </footer>
  </main>
</template>

<style scoped>
.agent-shell { height: 100%; min-width: 0; display: flex; flex-direction: column; background: var(--g1); color: var(--g10); }
.agent-header { flex: none; display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 68px; padding: 12px 24px; border-bottom: var(--border); }
.agent-heading { min-width: 0; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; }
.agent-heading h1 { margin: 0; overflow-wrap: anywhere; }
.agent-header-actions { flex: none; display: flex; align-items: center; gap: 4px; }
.agent-switch { flex: none; display: flex; align-items: center; gap: 12px; padding: 12px 24px; border-bottom: var(--border); }
.agent-selector { flex: 1; min-width: 0; }
.agent-messages { flex: 1; overscroll-behavior: contain; }
.agent-message-inner { width: 100%; max-width: 880px; min-height: 100%; margin: 0 auto; padding: 28px 24px 20px; box-sizing: border-box; }
.agent-center, .agent-welcome { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 16px; min-height: 280px; text-align: center; padding: 36px 0; }
.agent-welcome h2, .agent-welcome p { margin: 0; }
.agent-welcome-icon { font-size: var(--fs-kpi); padding: 16px; border: var(--border); border-radius: 12px; }
.agent-welcome-selector { width: 100%; max-width: 320px; text-align: left; }
.agent-turn { margin-bottom: 32px; }
.agent-question { display: flex; justify-content: flex-end; margin-bottom: 20px; }
.agent-question-bubble { display: block; max-width: 90%; padding: 12px 16px; border-radius: 12px 12px 4px 12px; background: var(--brand-bg); white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
.agent-response { min-width: 0; }
.agent-response-label { display: block; margin-bottom: 8px; }
.agent-pending { display: flex; align-items: center; gap: 10px; padding: 8px 0; }
.agent-error { margin: 16px 0; }
.agent-error-actions { display: flex; gap: 8px; margin-top: 10px; }
.agent-composer { flex: none; border-top: var(--border); padding: 16px 24px 12px; }
.agent-composer-inner { max-width: 832px; margin: 0 auto; }
.agent-input-wrap { display: flex; align-items: flex-end; gap: 12px; border: 1px solid var(--g6); border-radius: 8px; padding: 12px; background: var(--g1); }
.agent-input-wrap:focus-within { border-color: var(--brand); }
.agent-input-wrap :deep(textarea.ant-input) { padding: 0; resize: none; border: 0; box-shadow: none; background: transparent; }
.agent-input-wrap :deep(.ant-btn) { flex: none; }
.agent-input-help { margin-top: 8px; text-align: right; }
@media (max-width: 480px) {
  .agent-header { padding-inline: 16px; }
  .agent-message-inner { padding: 20px 16px; }
  .agent-composer { padding: 12px 16px; }
  .agent-input-wrap { gap: 8px; }
  .agent-header-actions :deep(.ant-btn) { padding-inline: 8px; }
}
</style>
