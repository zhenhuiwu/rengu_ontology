<script setup lang="ts">
import { ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { useAuthStore } from '@/stores/auth';
import { isApiError } from '@/api/http';

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ 'update:open': [value: boolean] }>();
const currentPassword = ref(''), newPassword = ref(''), confirmation = ref(''), error = ref(''), saving = ref(false);
const auth = useAuthStore();
function clear() { currentPassword.value = ''; newPassword.value = ''; confirmation.value = ''; error.value = ''; }
watch(() => props.open, clear);
function close() { if (!saving.value) emit('update:open', false); }
async function save() {
  if (saving.value) return;
  error.value = '';
  if (!currentPassword.value || !newPassword.value || !confirmation.value) { error.value = '请填写全部密码项'; return; }
  if (newPassword.value !== confirmation.value) { error.value = '两次输入的新密码不一致'; return; }
  if (newPassword.value.length < 8 || newPassword.value.length > 128) { error.value = '新密码须为8–128位'; return; }
  if (newPassword.value === currentPassword.value) { error.value = '新密码不能与原密码相同'; return; }
  saving.value = true;
  try {
    await auth.changePassword(currentPassword.value, newPassword.value);
    clear(); emit('update:open', false); message.success('密码已修改');
  } catch (e) { error.value = isApiError(e) ? (e.status === 401 ? '原密码不正确，请重新输入' : e.message) : '修改失败，请稍后重试'; }
  finally { saving.value = false; }
}
</script>

<template>
  <a-modal :open="open" title="修改密码" :confirm-loading="saving" :closable="!saving" :mask-closable="false" :keyboard="!saving" :cancel-button-props="{ disabled: saving }" ok-text="保存密码" @cancel="close" @ok="save">
    <form class="password-form" :aria-busy="saving" @submit.prevent="save">
      <div class="password-field"><label for="change-current-password">原密码</label><a-input-password id="change-current-password" v-model:value="currentPassword" autocomplete="current-password" :disabled="saving" /></div>
      <div class="password-field"><label for="change-new-password">新密码</label><a-input-password id="change-new-password" v-model:value="newPassword" autocomplete="new-password" :disabled="saving" aria-describedby="password-requirements" /></div>
      <p id="password-requirements" class="password-requirements">8–128位，不能与原密码相同。</p>
      <div class="password-field"><label for="change-confirm-password">确认新密码</label><a-input-password id="change-confirm-password" v-model:value="confirmation" autocomplete="new-password" :disabled="saving" /></div>
      <p v-if="error" class="password-error" role="alert">{{ error }}</p>
      <button type="submit" class="password-enter" tabindex="-1" aria-hidden="true" :disabled="saving">保存密码</button>
    </form>
  </a-modal>
</template>

<style scoped>
.password-form { padding-top: 12px; }.password-field { margin-bottom: 20px; }.password-field label { display: block; margin-bottom: 8px; color: var(--g10); font-size: var(--fs-14); }.password-requirements { color: var(--g9); font-size: var(--fs-12); margin: -10px 0 20px; line-height: 20px; }.password-error { margin-bottom: 0; color: var(--error); font-size: var(--fs-14); }.password-enter { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); border: 0; }
:global(html[data-chrome="navy"]) .password-error { color: #ffb4ae; }
</style>
