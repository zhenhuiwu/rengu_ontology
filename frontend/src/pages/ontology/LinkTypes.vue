<script setup lang="ts">
/** 关系类型（真接口由 StaticGate 隔离身份/版本，Body 分页读取列表与所选详情，页面本体见 ./LinkTypesBody.vue） */
import StaticGate from './parts/StaticGate.vue';
import Body from './LinkTypesBody.vue';
</script>

<template>
  <StaticGate paged :sets="['objects', 'links']" v-slot="{ verKey }"><Body :key="verKey" /></StaticGate>
</template>
