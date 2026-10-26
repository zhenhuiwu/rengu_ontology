<script setup lang="ts">
/** 维度与字典（真接口由 StaticGate 隔离身份/版本，Body 分页读取列表与所选详情，页面本体见 ./DimensionsBody.vue） */
import StaticGate from './parts/StaticGate.vue';
import Body from './DimensionsBody.vue';
</script>

<template>
  <StaticGate paged :sets="['dimensions']" v-slot="{ verKey }"><Body :key="verKey" /></StaticGate>
</template>
