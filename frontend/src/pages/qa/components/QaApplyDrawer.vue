<script setup lang="ts">
/**
 * 越权那一轮的“申请权限”抽屉。状态 ?apply=1。
 * 文案只说“不在权限范围内”，不透露该范围有没有数据；提交后该轮显示“已申请 · 待审批”。
 */
import { ref } from 'vue';
import { QA_APPLY } from '@/mock';

/** submitting：真接口提交中（W16）；submit 带上理由和有效期（显示文字，提交时由调用方转天数） */
defineProps<{ open: boolean; submitting?: boolean }>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'submit', v: { reason: string; duration: string }): void }>();
const reason = ref('');
const duration = ref(QA_APPLY.durations[1]);
</script>

<template>
  <a-drawer :open="open" title="申请数据权限" :width="480" @close="emit('close')">
    <a-form layout="vertical" class="qa-ap">
      <a-form-item label="申请范围" required>
        <a-input :value="QA_APPLY.scope" readonly />
        <div class="t-12 c-3 qa-ap-hint">按你刚才的提问预填；通过后，该范围按行级权限对你开放。</div>
      </a-form-item>
      <a-form-item label="涉及指标"><a-input :value="QA_APPLY.object" readonly /></a-form-item>
      <a-form-item label="申请理由" required>
        <a-textarea v-model:value="reason" :rows="3" :maxlength="200" show-count placeholder="例如：Q4 复购召回方案需要对比各大区复购表现" />
      </a-form-item>
      <a-form-item label="审批人"><a-input :value="QA_APPLY.approver" readonly /></a-form-item>
      <a-form-item label="有效期">
        <a-radio-group v-model:value="duration" option-type="button" :options="QA_APPLY.durations" />
      </a-form-item>
    </a-form>
    <template #footer>
      <div class="qa-ap-foot">
        <a-button @click="emit('close')">取消</a-button>
        <a-button type="primary" :disabled="!reason.trim()" :loading="submitting" @click="emit('submit', { reason: reason.trim(), duration })">提交申请</a-button>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped>
.qa-ap-hint { margin-top: 4px; }
.qa-ap-foot { display: flex; justify-content: flex-end; gap: 8px; }
</style>
