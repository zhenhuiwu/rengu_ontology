<script setup lang="ts">
import { computed } from 'vue';
import type { DeliveryErrorDetails } from '@/api/action';
import { deliveryErrorPresentation } from './deliveryPresentation';

const props = defineProps<{ details?: DeliveryErrorDetails | null }>();
const diagnostic = computed(() => deliveryErrorPresentation(props.details));
</script>

<template>
  <div v-if="diagnostic" class="delivery-diagnostic t-12" aria-label="失败明细">
    <div class="diagnostic-meta c-2">
      <span v-if="diagnostic.phase">阶段：{{ diagnostic.phase }}</span>
      <span v-if="diagnostic.status">HTTP {{ diagnostic.status }}</span>
      <span v-if="diagnostic.fieldsCount !== undefined">字段问题 {{ diagnostic.fieldsCount }} 项</span>
      <span v-if="diagnostic.failedCount !== undefined">校验项失败 {{ diagnostic.failedCount }} 项</span>
    </div>
    <ul v-if="diagnostic.fields.length || diagnostic.failed.length" class="diagnostic-items">
      <li v-for="(item, index) in diagnostic.fields" :key="`field-${index}`"><span class="fw-600">{{ item.location }}</span> · {{ item.reason }}</li>
      <li v-for="(item, index) in diagnostic.failed" :key="`criterion-${index}`"><span class="fw-600">{{ item.location }}</span> · {{ item.reason }}</li>
    </ul>
    <div v-else class="c-3">未提供字段或校验项明细</div>
    <div v-if="diagnostic.fieldsTruncated || diagnostic.failedTruncated" class="c-3">仅展示接口返回的明细，字段和校验项各最多 20 项。</div>
    <div v-if="diagnostic.valuesOmitted" class="c-3">字段值已省略</div>
  </div>
</template>

<style scoped>
.delivery-diagnostic { margin-top: 8px; padding: 8px 12px; background: var(--g2); border: var(--border); border-radius: var(--radius); color: var(--g10); white-space: normal; overflow-wrap: anywhere; min-width: 0; }
.diagnostic-meta { display: flex; flex-wrap: wrap; gap: 4px 16px; }
.diagnostic-items { padding-left: 20px; margin: 4px 0; }
</style>
