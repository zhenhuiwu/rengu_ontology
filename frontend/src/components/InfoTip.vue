<script setup lang="ts">
/** 标题旁的 ⓘ：说明文字放悬停提示，不在标题下另起灰色说明行（PDE 页面用） */
import { InfoCircleOutlined } from '@ant-design/icons-vue';
defineProps<{ text: string }>();
</script>

<template>
  <a-tooltip :title="text"><InfoCircleOutlined class="info-tip t-14 c-3" :aria-label="text" @click.stop /></a-tooltip>
</template>

<style scoped>
.info-tip { margin-left: 6px; cursor: help; vertical-align: -1px; }
</style>
