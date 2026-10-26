<script setup lang="ts">
/** 指标口径（真接口由 StaticGate 隔离身份/版本，Body 分页读取列表与所选详情，页面本体见 ./MetricsBody.vue） */
import StaticGate from './parts/StaticGate.vue';
import Body from './MetricsBody.vue';
</script>

<template>
  <StaticGate paged :sets="['metrics']" v-slot="{ verKey }"><Body :key="verKey" /></StaticGate>
</template>
