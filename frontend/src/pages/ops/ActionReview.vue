<script setup lang="ts">
import { humanCheckText, declaredEditText, type ActionReview } from './actionReview';
defineOptions({ inheritAttrs: false });
defineProps<ActionReview>();
</script>

<template>
  <a-alert v-if="reviewUnavailable" type="info" :message="reviewUnavailable" show-icon />
  <div v-else-if="humanChecks?.length || declaredEdits?.length" class="action-review">
    <div v-if="humanChecks?.length">
      <div class="fw-600">需人工复核</div>
      <div v-for="(item, index) in humanChecks" :key="index">{{ index + 1 }}. {{ humanCheckText(item) }}</div>
    </div>
    <div v-if="declaredEdits?.length">
      <div class="fw-600">外部系统声明效果</div>
      <div class="c-3">以下由外部系统承担，不代表本地已完成。</div>
      <div v-for="(item, index) in declaredEdits" :key="index">{{ index + 1 }}. {{ declaredEditText(item) }}</div>
    </div>
  </div>
</template>

<style scoped>
.action-review { display: flex; flex-direction: column; gap: 12px; padding: 12px 0; overflow-wrap: anywhere; white-space: pre-wrap; }
</style>
