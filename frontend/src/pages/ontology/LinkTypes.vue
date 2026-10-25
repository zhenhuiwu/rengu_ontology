<script setup lang="ts">
/** 关系类型（真接口时先由 StaticGate 拉静态本体数据，页面本体见 ./LinkTypesBody.vue） */
import StaticGate from './parts/StaticGate.vue';
import Body from './LinkTypesBody.vue';
</script>

<template>
  <StaticGate :sets="['objects', 'links']" v-slot="{ verKey }"><Body :key="verKey" /></StaticGate>
</template>
