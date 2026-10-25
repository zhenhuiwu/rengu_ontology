<script setup lang="ts">
/** 对话区：页面里唯一的滚动区（.scroll-area）。内容不足时贴底显示 */
import { ref, computed, onMounted, nextTick, watch, type ComponentPublicInstance } from 'vue';
import { useRoute } from 'vue-router';
import { LockOutlined, InboxOutlined, ClockCircleOutlined, ApartmentOutlined, MessageOutlined } from '@ant-design/icons-vue';
import { QA_DEMO_TURNS, QA_DEMO_CANNED } from '@/mock';
import { useReadonly } from '@/composables/useReadonly';
import { useQaSession } from '../useQaSession';
import { useAppStore } from '@/stores/app';
import QaAnswer from './QaAnswer.vue';
import QaStoreAnswer from './QaStoreAnswer.vue';
import type { QaRealTurn, QaChainAnswer } from '@/api/qa';
/** applied：越权那一轮已提交申请；tips：本会话中途修改范围/口径后插入的系统提示（追加在最新一轮之后） */
const props = defineProps<{ applied: boolean; tips: string[] }>();
const emit = defineEmits<{ (e: 'suggest'): void; (e: 'pin'): void; (e: 'apply', turnId?: string): void }>();
const { isNew, activeTurns, activeId, draft, real, detail, sending, loading } = useQaSession();
const { readonly, reason } = useReadonly();
/** 示例会话的轮次（mock/qa.ts）；新追加的轮次统一用 QA_DEMO_CANNED */
const TURNS = computed<QaRealTurn[]>(() => (real ? detail.value?.turns ?? [] : QA_DEMO_TURNS));
/** 真接口：链路新生成的回答（card = table）——沿用新轮次的回答气泡，多行结果在气泡下给出结果表 */
type ChainTurn = Extract<QaRealTurn, { answer: QaChainAnswer }>;
const isChain = (t: QaRealTurn): t is ChainTurn => t.kind === 'answer' && (t.answer as { card: string }).card === 'table';
const chainMeta = computed(() => `按 大区 = ${detail.value?.scope ?? '—'}、口径 = ${detail.value?.caliber ?? '—'} 回答`);
const tableCols = (t: ChainTurn) => t.answer.table.columns.map((c) => ({ dataIndex: c.key, title: c.title, align: (c.align ?? 'left') as 'left' | 'right' }));
const tableRows = (t: ChainTurn) => t.answer.table.items.map((r, i) => ({ ...r, _k: i }));
const showTable = (t: ChainTurn) => t.answer.table.items.length > 1 || t.answer.table.columns.length > 1;
function retry(text: string) { draft.value = text; }
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
</script>

<template>
  <div ref="conv" class="qa-conv scroll-area">
    <div v-if="real && loading && !detail" class="qa-empty"><a-spin /></div>
    <a-empty v-if="isNew && !activeTurns.length" class="qa-empty" description="新会话：在下方输入问题，Enter 发送">
      <template #image><MessageOutlined class="qa-empty-ic c-3" /></template>
    </a-empty>
    <template v-if="!isNew">
    <template v-for="t in TURNS" :key="t.id">
    <div v-if="t.kind === 'system'" class="qa-sys t-12 c-3"><span>{{ t.time }} · {{ t.text }}</span></div>
    <template v-else>
    <div :ref="t.kind === 'answer' && t.answer.card === 'store' ? setStoreTurn : undefined" class="qa-u"><span v-if="t.time" class="t-12 c-3">{{ t.time }}</span><span class="qa-bubble">{{ t.question }}</span><a-avatar size="small" class="qa-me">{{ me }}</a-avatar></div>
    <template v-if="isChain(t)">
      <div class="qa-b qa-b-ans">
        <ApartmentOutlined class="qa-b-av" />
        <span class="qa-b-txt">{{ t.answer.text }}</span>
        <span class="t-12 c-3 qa-b-meta">{{ chainMeta }}</span>
      </div>
      <a-table v-if="showTable(t)" class="qa-chain-tbl" size="small" row-key="_k" :columns="tableCols(t)" :data-source="tableRows(t)"
        :pagination="t.answer.table.paged ? { pageSize: 10, size: 'small', showSizeChanger: false } : false" :scroll="{ x: 'max-content' }" />
    </template>
    <template v-else-if="t.kind === 'answer'">
    <QaStoreAnswer v-if="t.answer.card === 'store'" :card="t.answer" :turn-id="real ? t.id : undefined" />
    <QaAnswer v-else :card="t.answer" :turn-id="real ? t.id : undefined" :vote="'vote' in t ? t.vote : undefined" @suggest="emit('suggest')" @pin="emit('pin')" />
    </template>
    <div v-else-if="t.kind === 'denied'" class="qa-b">
      <LockOutlined class="c-3" />
      <span class="qa-b-txt">{{ t.lead }}<span class="fw-600">{{ t.bold }}</span>{{ t.tail }}</span>
      <span v-if="real ? t.applied : applied" class="qa-b-state c-3"><ClockCircleOutlined />已申请 · 待审批</span>
      <template v-else><a-tag>{{ t.tag }}</a-tag><a-button size="small" @click="emit('apply', t.id)">申请权限</a-button></template>
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
    <div v-if="real && sending" class="qa-b qa-b-ans"><ApartmentOutlined class="qa-b-av" /><span class="qa-b-txt c-3">正在查询…</span><a-spin size="small" /></div>
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
.qa-chain-tbl { margin: 0 40px 10px 0; background: var(--g1); }
.qa-empty-ic { font-size: 40px; }
.qa-b-state { display: inline-flex; align-items: center; gap: 4px; }
/* 系统提示：灰色细线居中，标出范围/口径从哪一问开始变化；之前的答案保留各自依据 */
.qa-sys { display: flex; align-items: center; gap: 12px; margin: 0 40px 10px 0; }
.qa-sys::before, .qa-sys::after { content: ''; flex: 1; border-top: var(--border); }
.qa-ans + .qa-sys, :deep(.qa-ans) + .qa-sys { margin-top: 10px; }
</style>
