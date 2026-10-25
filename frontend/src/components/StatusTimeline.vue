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
      <div class="t-14 stl-row" :title="t.desc ? `${t.text}\n${t.desc}` : t.text">
        <span class="c-1 stl-ell">{{ t.text }}</span>
        <span v-if="t.status === 'unknown'" class="tl-attention stl-none">· 结果未知 · 待对账</span>
        <span class="c-2 num stl-time">· {{ t.time }}</span>
      </div>
    </a-timeline-item>
  </a-timeline>
</template>

<style scoped>
.stl { padding-top: 8px; }
.stl-row { display: flex; gap: 4px; min-width: 0; white-space: nowrap; }
.stl-ell { flex: none; max-width: 65%; overflow: hidden; text-overflow: ellipsis; }
.stl-time { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.stl-none { flex: none; }
</style>
