<script setup lang="ts">
/**
 * 越权那一轮的“申请权限”抽屉。状态 ?apply=1。
 * 文案只说“不在权限范围内”，不透露该范围有没有数据；提交后该轮显示“已申请 · 待审批”。
 */
import { computed, ref, watch } from 'vue';
import { QA_APPLY } from '@/mock';
import { qaReal } from '@/api/qa';

/** submitting：真接口提交中（W16）；submit 带上理由和有效期（显示文字，提交时由调用方转天数） */
const props = defineProps<{ open: boolean; submitting?: boolean; readonly?: boolean }>();
const real = qaReal();
const emit = defineEmits<{ (e: 'close'): void; (e: 'submit', v: { scope: string; object: string; reason: string; duration: string }): void }>();
const scope = ref(real ? '' : QA_APPLY.scope);
const object = ref(real ? '' : QA_APPLY.object);
const reason = ref('');
const duration = ref(QA_APPLY.durations[1]);
watch(() => props.open, (open) => { if (real && open) { scope.value = ''; object.value = ''; reason.value = ''; } });
const canSubmit = computed(() => !props.readonly && !props.submitting && !!scope.value.trim() && !!object.value.trim() && !!reason.value.trim());
function submit() {
  if (!canSubmit.value) return;
  emit('submit', { scope: scope.value.trim(), object: object.value.trim(), reason: reason.value.trim(), duration: duration.value });
}
</script>

<template>
  <a-drawer :open="open" title="申请数据权限" :width="480" @close="emit('close')">
    <a-form layout="vertical" class="qa-ap">
      <a-form-item label="申请范围" required>
        <a-input v-model:value="scope" :readonly="!real || readonly" :maxlength="60" />
        <div class="t-12 c-3 qa-ap-hint">{{ real ? '填写实际需要的业务范围。超出系统支持范围的申请需进一步评估。' : '按你刚才的提问预填；通过后，该范围按行级权限对你开放。' }}</div>
      </a-form-item>
      <a-form-item label="涉及指标" :required="real"><a-input v-model:value="object" :readonly="!real || readonly" :maxlength="60" /></a-form-item>
      <a-form-item label="申请理由" required>
        <a-textarea v-model:value="reason" :readonly="readonly" :rows="3" :maxlength="200" show-count :placeholder="real ? '说明使用目的和需要的业务范围' : '例如：Q4 复购召回方案需要对比各大区复购表现'" />
      </a-form-item>
      <a-form-item label="审批人"><a-input :value="real ? '待分配（审批路由尚未配置）' : QA_APPLY.approver" readonly /></a-form-item>
      <a-form-item label="有效期">
        <a-radio-group v-model:value="duration" :disabled="readonly" option-type="button" :options="QA_APPLY.durations" />
      </a-form-item>
    </a-form>
    <template #footer>
      <div class="qa-ap-foot">
        <a-button @click="emit('close')">取消</a-button>
        <a-button type="primary" :disabled="!canSubmit" :loading="submitting" @click="submit">提交申请</a-button>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped>
.qa-ap-hint { margin-top: 4px; }
.qa-ap-foot { display: flex; justify-content: flex-end; gap: 8px; }
</style>
