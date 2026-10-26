<script setup lang="ts">
/** 业务知识（真接口由 StaticGate 隔离身份/版本，Body 分页读取列表与所选详情，页面本体见 ./GlossaryBody.vue） */
import StaticGate from './parts/StaticGate.vue';
import Body from './GlossaryBody.vue';
</script>

<template>
  <StaticGate paged :sets="['knowledge']" v-slot="{ verKey }"><Body :key="verKey" /></StaticGate>
</template>
