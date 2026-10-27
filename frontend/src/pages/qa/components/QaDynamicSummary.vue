<script setup lang="ts">
import type { QaDynamicSummary } from '@/api/qa';
import ObjRef from '@/components/ObjRef.vue';
defineProps<{ items: QaDynamicSummary[] }>();
</script>

<template>
  <a-collapse v-if="items.length" ghost class="qa-dynamic">
    <a-collapse-panel v-for="item in items" :key="`${item.objKind}:${item.id}`">
      <template #header><span>对象动态 · {{ item.objKindLabel || item.objKind }} · {{ item.objLabel || item.id }}</span></template>
      <ObjRef :kind="item.objKind" :id="item.id">{{ item.objLabel || item.id }}</ObjRef>
      <a-descriptions size="small" :column="1" class="qa-dynamic-values">
        <a-descriptions-item label="未关闭告警">{{ item.alerts.available ? item.alerts.openCount ?? '—' : '当前身份不可查看' }}</a-descriptions-item>
        <a-descriptions-item label="规则命中">{{ item.ruleHits.available ? item.ruleHits.total ?? '—' : '当前身份不可查看' }}</a-descriptions-item>
        <a-descriptions-item label="生命周期">{{ !item.lifecycle.available ? '当前身份不可查看' : !item.lifecycle.defined ? '未定义' : item.lifecycle.state || '未记录当前状态' }}</a-descriptions-item>
        <a-descriptions-item label="动作记录">{{ item.actionRuns.available ? item.actionRuns.total ?? '—' : '当前身份不可查看' }}</a-descriptions-item>
      </a-descriptions>
      <div v-if="item.alerts.available" class="qa-dynamic-alerts">
        <a-tag v-for="alert in item.alerts.latest || []" :key="alert.id">{{ alert.id }} · {{ alert.ruleId }} · {{ alert.status }}</a-tag>
      </div>
    </a-collapse-panel>
  </a-collapse>
</template>

<style scoped>
.qa-dynamic { margin: 8px 0; border: var(--border); border-radius: var(--radius); }
.qa-dynamic-values { margin-top: 8px; }
.qa-dynamic-alerts { display: flex; flex-wrap: wrap; gap: 4px; }
</style>
