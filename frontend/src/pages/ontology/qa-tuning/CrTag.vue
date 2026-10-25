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
        <div class="crt-row"><span class="c-3">状态</span><span class="c-1">{{ cr.status }} · {{ cr.level }} · {{ cr.changes }} 处改动</span></div>
        <div class="crt-row"><span class="c-3">负责人</span><span class="c-1">{{ cr.owner }} · 审核人 {{ cr.reviewer }}</span></div>
        <div class="crt-row"><span class="c-3">更新</span><span class="c-1 num">{{ cr.updatedAt }}</span></div>
        <div class="crt-row"><span class="c-3">原因</span><span class="c-1 crt-ell" :title="cr.reason">{{ cr.reason }}</span></div>
      </div>
    </template>
    <a-tag class="crt" @click.stop>{{ text ?? id }}<span v-if="!text" class="c-3"> · {{ cr.status }}</span></a-tag>
  </a-popover>
</template>

<style scoped>
.crt { cursor: default; margin-inline-end: 0; }
.crt-pop { width: 300px; display: flex; flex-direction: column; gap: 4px; }
.crt-row { display: flex; gap: 8px; min-width: 0; }
.crt-row > .c-3 { width: 42px; flex: none; }
.crt-ell { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
