<script setup lang="ts">
/** 业务知识（真接口时先由 StaticGate 拉静态本体数据，页面本体见 ./GlossaryBody.vue） */
import StaticGate from './parts/StaticGate.vue';
import Body from './GlossaryBody.vue';
</script>

<template>
  <StaticGate :sets="['knowledge']" v-slot="{ verKey }"><Body :key="verKey" /></StaticGate>
</template>
