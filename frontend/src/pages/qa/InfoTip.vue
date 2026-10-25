<script setup lang="ts">
/** 标题旁的 ⓘ：说明文字放进悬停提示，不在标题下另起一行灰字（问数、对象、问数调优页共用） */
import { InfoCircleOutlined } from '@ant-design/icons-vue';
defineProps<{ title: string }>();
</script>

<template>
  <a-tooltip :title="title"><InfoCircleOutlined class="info-tip c-3" /></a-tooltip>
</template>

<style scoped>
.info-tip { font-size: var(--fs-14); cursor: help; flex: none; }
</style>
