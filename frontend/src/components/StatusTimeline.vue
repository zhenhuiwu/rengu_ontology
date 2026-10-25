<script setup lang="ts">
/**
 * 公共状态时间线。颜色规则（全站统一，见 theme.ts TIMELINE_TONES）：
 *   灰 = 正常 / 已完成；橙 = 需要关注；红 = 失败；蓝 = 仅当前选中节点。
 *   status='unknown'（结果未知）→ 橙色 + 问号图标 + “结果未知 · 待对账”。
 * <StatusTimeline :items="[{ time, text, desc?, status? }]" :selected="i" />
 */
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import { timelineTone } from '@/theme';

export interface TimelineItem { time: string; text: string; desc?: string; status?: 'done' | 'warn' | 'error' | 'unknown' | string }
defineProps<{ items: TimelineItem[]; selected?: number }>();
const color = (s: string | undefined, sel: boolean) => `var(--tl-${timelineTone(s, sel)})`;
</script>

<template>
  <a-timeline class="stl">
    <a-timeline-item v-for="(t, i) in items" :key="i" :color="color(t.status, selected === i)">
      <template v-if="t.status === 'unknown'" #dot><QuestionCircleOutlined class="tl-attention" /></template>
      <div class="t-14 c-1">{{ t.text }}</div>
      <div v-if="t.status === 'unknown'" class="t-12 tl-attention">结果未知 · 待对账</div>
      <div v-if="t.desc" class="t-12 c-3">{{ t.desc }}</div>
      <div class="t-12 c-3 num">{{ t.time }}</div>
    </a-timeline-item>
  </a-timeline>
</template>

<style scoped>
.stl { padding-top: 8px; }
</style>
