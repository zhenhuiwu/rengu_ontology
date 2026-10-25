<script setup lang="ts">
/** 对象类型（真接口时先由 StaticGate 拉静态本体数据，页面本体见 ./ObjectTypesBody.vue） */
import StaticGate from './parts/StaticGate.vue';
import Body from './ObjectTypesBody.vue';
</script>

<template>
  <StaticGate :sets="['objects', 'objectProps', 'links']" v-slot="{ verKey }"><Body :key="verKey" /></StaticGate>
</template>
