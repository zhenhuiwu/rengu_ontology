<script setup lang="ts">
/** 变更单标签：悬停看变更单摘要（标题、状态、负责人、审核人、更新时间、原因），不跳页 */
import { computed } from 'vue';
import { crById } from '@/mock/qa-tuning';

const props = defineProps<{ id: string; text?: string }>();
const cr = computed(() => crById(props.id));
</script>

<template>
  <a-tag v-if="!cr" class="crt">{{ text ?? id }}<span v-if="!text" class="c-3"> · 草稿</span></a-tag>
  <a-popover v-else placement="bottomLeft">
    <template #content>
      <div class="crt-pop">
        <div class="t-14 fw-600 c-1">{{ cr.id }} {{ cr.title }}</div>
        <div class="t-12 c-2">状态 {{ cr.status }} · {{ cr.level }} · {{ cr.changes }} 处改动</div>
        <div class="t-12 c-2">负责人 {{ cr.owner }} · 审核人 {{ cr.reviewer }}</div>
        <div class="t-12 c-3">更新 <span class="num">{{ cr.updatedAt }}</span></div>
        <div class="t-12 c-2">{{ cr.reason }}</div>
      </div>
    </template>
    <a-tag class="crt" @click.stop>{{ text ?? id }}<span v-if="!text" class="c-3"> · {{ cr.status }}</span></a-tag>
  </a-popover>
</template>

<style scoped>
.crt { cursor: default; margin-inline-end: 0; }
.crt-pop { width: 300px; display: flex; flex-direction: column; gap: 4px; }
</style>
