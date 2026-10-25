<script setup lang="ts">
/** 对话区：页面里唯一的滚动区（.scroll-area）。内容不足时贴底显示 */
import { ref, onMounted, nextTick, watch } from 'vue';
import { useRoute } from 'vue-router';
import { LockOutlined, InboxOutlined, ClockCircleOutlined } from '@ant-design/icons-vue';
import { useAppStore } from '@/stores/app';
import QaAnswer from './QaAnswer.vue';
import QaStoreAnswer from './QaStoreAnswer.vue';
/** applied：越权那一轮已提交申请；tips：本会话中途修改范围/口径后插入的系统提示（追加在最新一轮之后） */
const props = defineProps<{ applied: boolean; tips: string[] }>();
const emit = defineEmits<{ (e: 'suggest'): void; (e: 'pin'): void; (e: 'apply'): void }>();
const me = useAppStore().user.name.slice(0, 1);
const route = useRoute();
const conv = ref<HTMLElement>();
const storeTurn = ref<HTMLElement>();
// 默认停在最新一轮（底部）；URL 带 ?tblPage 时定位到门店表那一轮，便于复现翻页状态
onMounted(async () => {
  await nextTick();
  const el = conv.value; if (!el) return;
  if (route.query.tblPage && storeTurn.value) el.scrollTop += storeTurn.value.getBoundingClientRect().top - el.getBoundingClientRect().top - 12;
  else el.scrollTop = el.scrollHeight;
});
// 新插入系统提示时滚到底部
watch(() => props.tips.length, async () => { await nextTick(); if (conv.value) conv.value.scrollTop = conv.value.scrollHeight; });
</script>

<template>
  <div ref="conv" class="qa-conv scroll-area">
    <div ref="storeTurn" class="qa-u"><span class="t-12 c-3">14:05</span><span class="qa-bubble">上个月华东各重点门店的销售额？</span><a-avatar size="small" class="qa-me">{{ me }}</a-avatar></div>
    <QaStoreAnswer />
    <div class="qa-sys t-12 c-3"><span>14:20 · 从这里开始按 大区 = 华东、口径 = 不含税 回答</span></div>
    <div class="qa-u"><span class="qa-bubble">上个月华南大区复购客户的销售额？</span><a-avatar size="small" class="qa-me">{{ me }}</a-avatar></div>
    <div class="qa-b">
      <LockOutlined class="c-warning" />
      <span class="qa-b-txt"><span class="fw-600">你问的华南大区不在你的权限范围内</span>（当前可查：大区 = 华东）。</span>
      <span v-if="applied" class="qa-b-state c-3"><ClockCircleOutlined />已申请 · 待审批</span>
      <template v-else><a-tag>权限拦截</a-tag><a-button size="small" @click="emit('apply')">申请权限</a-button></template>
    </div>
    <div class="qa-u"><span class="qa-bubble">上个月嘉兴南湖店的销售额？</span><a-avatar size="small" class="qa-me">{{ me }}</a-avatar></div>
    <div class="qa-b">
      <InboxOutlined class="c-3" />
      <span class="qa-b-txt">查询已执行，<span class="fw-600">没有符合条件的记录</span>（0 行）。该门店 2026-10-18 开业，9 月尚无订单。</span>
      <a-tag>结果为空</a-tag><a-button size="small">改查 10 月</a-button>
    </div>
    <div class="qa-u"><span class="t-12 c-3">15:32</span><span class="qa-bubble">上个月华东大区复购客户的销售额是多少？</span><a-avatar size="small" class="qa-me">{{ me }}</a-avatar></div>
    <QaAnswer @suggest="emit('suggest')" @pin="emit('pin')" />
    <div v-for="(t, i) in tips" :key="i" class="qa-sys t-12 c-3"><span>{{ t }}</span></div>
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
.qa-b-state { display: inline-flex; align-items: center; gap: 4px; }
/* 系统提示：灰色细线居中，标出范围/口径从哪一问开始变化；之前的答案保留各自依据 */
.qa-sys { display: flex; align-items: center; gap: 12px; margin: 0 40px 10px 0; }
.qa-sys::before, .qa-sys::after { content: ''; flex: 1; border-top: var(--border); }
.qa-ans + .qa-sys, :deep(.qa-ans) + .qa-sys { margin-top: 10px; }
</style>
