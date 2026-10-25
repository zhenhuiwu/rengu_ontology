<script setup lang="ts">
/**
 * 动作确认卡：全站所有动作共用，只挂一个（在 AppLayout 里）。
 * 用法：const ok = await useConfirmStore().ask({ title, target, fields, impact, irreversible })
 */
import { ref, watch } from 'vue';
import { useConfirmStore } from '@/stores/confirm';

const store = useConfirmStore();
const ack = ref(false);
watch(() => store.open, (v) => { if (v) ack.value = false; });
</script>

<template>
  <a-modal
    :open="store.open"
    :width="520"
    :mask-closable="false"
    :ok-text="store.req?.okText ?? '确认执行'"
    cancel-text="取消"
    :ok-button-props="{ disabled: !!store.req?.irreversible && !ack }"
    @ok="store.settle(true)"
    @cancel="store.settle(false)"
  >
    <template #title><span class="t-16 fw-600">{{ store.req?.title }}</span><a-tag v-if="store.req?.tag" class="ca-tag">{{ store.req.tag }}</a-tag></template>
    <template v-if="store.req">
      <div class="ca-target t-14 c-2">作用对象：<span class="c-1">{{ store.req.target }}</span></div>
      <a-descriptions v-if="store.req.fields?.length" :column="1" size="small" bordered :label-style="{ width: '96px' }">
        <a-descriptions-item v-for="f in store.req.fields" :key="f.label" :label="f.label">
          <span class="num">{{ f.value }}</span>
        </a-descriptions-item>
      </a-descriptions>
      <div v-if="store.req.impact" class="ca-impact t-14 c-2">{{ store.req.impact }}</div>
      <a-checkbox v-if="store.req.irreversible" v-model:checked="ack">此操作会写回业务系统，提交后不能撤销</a-checkbox>
    </template>
  </a-modal>
</template>

<style scoped>
.ca-tag { margin-inline-start: 8px; vertical-align: 2px; }
.ca-target { margin: 8px 0 12px; }
.ca-impact { margin: 12px 0; padding: 8px 12px; background: var(--g2); border: var(--border); border-radius: var(--radius); }
</style>
