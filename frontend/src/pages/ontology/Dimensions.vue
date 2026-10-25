<script setup lang="ts">
/** 维度与字典（真接口时先由 StaticGate 拉静态本体数据，页面本体见 ./DimensionsBody.vue） */
import StaticGate from './parts/StaticGate.vue';
import Body from './DimensionsBody.vue';
</script>

<template>
  <StaticGate :sets="['dimensions']" v-slot="{ verKey }"><Body :key="verKey" /></StaticGate>
</template>
