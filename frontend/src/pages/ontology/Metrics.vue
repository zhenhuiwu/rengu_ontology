<script setup lang="ts">
/** 指标口径（真接口时先由 StaticGate 拉静态本体数据，页面本体见 ./MetricsBody.vue） */
import StaticGate from './parts/StaticGate.vue';
import Body from './MetricsBody.vue';
</script>

<template>
  <StaticGate :sets="['metrics']" v-slot="{ verKey }"><Body :key="verKey" /></StaticGate>
</template>
