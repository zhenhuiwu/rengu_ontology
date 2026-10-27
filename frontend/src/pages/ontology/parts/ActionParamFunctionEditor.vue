<script setup lang="ts">
import { onMounted, ref } from 'vue';
import type { ActionParam } from '@/mock/actionEvent';
import { getActionExecutionOptions, type ActionExecutionOptions } from '@/api/action';
import ActionValueSource from './ActionValueSource.vue';
const props = defineProps<{ params: ActionParam[]; parameterApi: string }>();
const model = defineModel<string>({ required: true });
const enabled = ref(false), error = ref(''), loading = ref(false), validationError = ref('');
const functions = ref<ActionExecutionOptions['functions']>([]);
const source = ref<Record<string, any>>({function: '', version: '', inputs: {}});
try { const value = JSON.parse(model.value); if (value?.function) { enabled.value = true; source.value = value; } } catch { /* Existing descriptive defaults stay unchanged. */ }
function save() { if (enabled.value) model.value = JSON.stringify(source.value); }
function toggle(value: boolean) { enabled.value = value; model.value = value ? JSON.stringify(source.value) : '—'; }
defineExpose({ validate: () => {
  save();
  const fn = functions.value.find(f => f.id === source.value.function && f.version === source.value.version);
  const valid = !enabled.value || (!loading.value && !error.value && !!fn && fn.inputs.every(p => {
    const v = source.value.inputs?.[p.name];
    return v && ('value' in v || (typeof v.param === 'string' && v.param !== props.parameterApi && props.params.some(p => p.api === v.param)));
  }));
  validationError.value = valid ? '' : '请选择计算函数的固定发布版本，并填写每个输入';
  return valid;
} });
onMounted(async () => {
  loading.value = true;
  try { functions.value = (await getActionExecutionOptions()).functions; }
  catch (e) { error.value = e instanceof Error ? e.message : String(e); }
  finally { loading.value = false; }
});
</script>

<template>
  <a-form-item label="参数取值"><a-switch :checked="enabled" checked-children="函数计算" un-checked-children="手工填写" @change="toggle" /></a-form-item>
  <a-spin v-if="enabled" :spinning="loading">
    <a-alert v-if="validationError" type="error" :message="validationError" show-icon role="alert" />
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-form-item label="计算函数与版本" required>
      <ActionValueSource v-model="source" :params="params.filter(p => p.api !== parameterApi)" :functions="functions" function-only @update:model-value="save" />
    </a-form-item>
  </a-spin>
</template>
