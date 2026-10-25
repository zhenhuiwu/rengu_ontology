<script setup lang="ts">
/** 本体总览（占位）：带一个 X6 空画布示例，关系图谱在此基础上开发。 */
import GraphCanvas from '@/components/GraphCanvas.vue';
</script>

<template>
  <div class="panel oo">
    <div class="panel-head">
      <span class="t-16 fw-600">关系图谱</span>
      <span class="t-12 c-3">待设计 · 画布示例：拖动平移，Ctrl + 滚轮缩放</span>
    </div>
    <div class="oo-canvas">
      <GraphCanvas><template #empty>待设计</template></GraphCanvas>
    </div>
  </div>
</template>

<style scoped>
.oo { flex: 1; }
.oo-canvas { flex: 1; min-height: 0; }
</style>
