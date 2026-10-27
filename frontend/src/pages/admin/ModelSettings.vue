<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { currentApiContext, isApiError } from '@/api/http';
import { loadModelSettings, saveModelSettings, type ModelSettingsData } from '@/api/settings';
import { confirmDiscard } from '@/composables/useUnsavedGuard';
import { useConfirmStore } from '@/stores/confirm';

const props = defineProps<{ readonly: boolean; reason: string }>();
const emit = defineEmits<{ dirty: [value: boolean] }>();
type ModelDraft = ModelSettingsData & { changeKey: boolean; apiKey: string };
const items = ref<ModelDraft[]>([]);
const saved = ref<ModelSettingsData[]>([]);
const loading = ref(false), saving = ref(''), error = ref(''), canManage = ref(false);
const origins = ref<string[]>([]);
const confirm = useConfirmStore();
let sequence = 0;
const editable = computed(() => canManage.value && !props.readonly && !loading.value && !saving.value);
const disabledReason = computed(() => props.reason || (!canManage.value ? '没有系统配置管理权限' : ''));
const fields = (row: ModelSettingsData) => ({ model: row.model, baseUrl: row.baseUrl, timeoutSec: row.timeoutSec, enabled: row.enabled, lockVersion: row.lockVersion });
const dirtyRow = (row: ModelDraft) => row.changeKey || JSON.stringify(fields(row)) !== JSON.stringify(fields(saved.value.find((x) => x.usage === row.usage)!));
const dirty = computed(() => items.value.some(dirtyRow));
watch(dirty, (value) => emit('dirty', value), { immediate: true });
const errText = (e: unknown) => isApiError(e) ? `${e.message}（${e.code}）` : '请求失败，请稍后重试';
const identity = () => currentApiContext().demoAs ?? '';
const emptyKey = () => { for (const row of items.value) { row.apiKey = ''; row.changeKey = false; } };
async function load() {
  const seq = ++sequence, user = identity();
  loading.value = true; error.value = '';
  try {
    const data = await loadModelSettings();
    if (seq !== sequence || user !== identity()) return;
    emptyKey();
    saved.value = data.items;
    items.value = data.items.map((row) => ({ ...row, changeKey: false, apiKey: '' }));
    canManage.value = data.canManage; origins.value = data.trustedOrigins;
  } catch (e) { if (seq === sequence && user === identity()) error.value = `模型配置加载失败：${errText(e)}`; }
  finally { if (seq === sequence) loading.value = false; }
}
watch(identity, () => { emptyKey(); items.value = []; saved.value = []; canManage.value = false; void load(); }, { immediate: true });
onBeforeUnmount(() => { sequence++; emptyKey(); emit('dirty', false); });
async function reload() { if (!dirty.value || await confirmDiscard()) await load(); }
function validation(row: ModelDraft): string {
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$/.test(row.model)) return '请输入有效模型名';
  try {
    const url = new URL(row.baseUrl);
    const origin = `${url.protocol}//${url.hostname}:${url.port || (url.protocol === 'https:' ? '443' : '80')}`;
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !origins.value.includes(origin)) return '模型地址须使用部署已授权源站，且不能含凭据、查询串或片段';
  } catch { return '请输入有效的 HTTP(S) Base URL'; }
  if (!(row.timeoutSec >= 5 && row.timeoutSec <= 300)) return '超时须在 5～300 秒之间';
  if (row.changeKey && !/^[\x21-\x7e]{1,4096}$/.test(row.apiKey)) return '新密钥不能为空或包含空白';
  if (row.enabled && !row.hasKey && !row.changeKey) return '启用模型前请配置密钥';
  return '';
}
async function save(row: ModelDraft) {
  if (!editable.value || !dirtyRow(row)) return;
  const invalid = validation(row);
  if (invalid) { message.error(invalid); return; }
  const ok = await confirm.ask({ title: '保存模型配置', target: row.label, okText: '保存',
    fields: [{ label: '模型', value: row.model }, { label: '状态', value: row.enabled ? '启用' : '停用' }, { label: '密钥', value: row.changeKey ? '更换，保存后不可查看' : '保留当前密钥' }],
    impact: '配置对之后新发起的调用生效，并记入审计；本次保存不会调用模型。' });
  if (!ok || !editable.value) return;
  const seq = sequence, user = identity();
  saving.value = row.usage; error.value = '';
  try {
    const data = await saveModelSettings(row.usage, { ...fields(row), ...(row.changeKey ? { apiKey: row.apiKey } : {}) });
    if (seq !== sequence || user !== identity()) return;
    saved.value = saved.value.map((x) => x.usage === row.usage ? data : x);
    Object.assign(row, data, { changeKey: false, apiKey: '' });
    message.success('模型配置已保存');
  } catch (e) {
    if (seq !== sequence || user !== identity()) return;
    error.value = isApiError(e) && e.code === 'VERSION_CONFLICT'
      ? '配置已被其他人修改，当前输入已保留。请重新加载最新配置后再编辑。'
      : `保存失败，当前输入已保留：${errText(e)}`;
    message.error(error.value);
  } finally { if (seq === sequence) saving.value = ''; }
}
async function discard(row: ModelDraft) {
  if (!(await confirmDiscard())) return;
  Object.assign(row, saved.value.find((x) => x.usage === row.usage), { changeKey: false, apiKey: '' });
}
</script>

<template>
  <div class="models">
    <div class="models-tools"><span class="t-12 c-2">平台共用配置</span><a-button size="small" :disabled="loading || !!saving" @click="reload">重新加载</a-button></div>
    <a-alert v-if="error" type="error" show-icon :message="error" />
    <a-spin :spinning="loading">
      <div v-if="items.length" class="models-grid">
        <section v-for="row in items" :key="row.usage" class="model-form">
          <div class="model-title"><span class="t-14 fw-600 c-1">{{ row.label }}</span><a-tag>{{ row.source === 'database' ? '平台配置' : '部署配置' }}</a-tag></div>
          <div v-if="row.updatedAt" class="t-12 c-3 model-updated">最近修改：{{ row.updatedAt }}</div>
          <a-form layout="vertical" :disabled="!editable">
            <a-form-item label="模型名" required><a-input v-model:value="row.model" :maxlength="128" class="num" autocomplete="off" /></a-form-item>
            <a-form-item label="Base URL" required extra="仅允许部署已授权的模型源站"><a-input v-model:value="row.baseUrl" :maxlength="1000" class="num" autocomplete="off" /></a-form-item>
            <div class="model-row"><a-form-item label="超时"><a-input-number v-model:value="row.timeoutSec" :min="5" :max="300" addon-after="秒" /></a-form-item><a-form-item label="状态"><a-switch v-model:checked="row.enabled" checked-children="启用" un-checked-children="停用" /></a-form-item></div>
            <a-form-item label="密钥">
              <div class="model-key"><span class="c-2">{{ row.hasKey ? '已配置（不可查看）' : '未配置' }}<span v-if="row.hasKey"> · {{ row.keySource === 'database' ? '平台加密保存' : '部署配置' }}</span></span><a-checkbox v-model:checked="row.changeKey" @change="!row.changeKey && (row.apiKey = '')">更换密钥</a-checkbox></div>
              <a-input-password v-if="row.changeKey" v-model:value="row.apiKey" autocomplete="new-password" placeholder="输入新密钥，不填不替换原密钥" :maxlength="4096" class="model-secret" />
            </a-form-item>
          </a-form>
          <div v-if="dirtyRow(row)" class="model-actions"><span class="t-12 c-2">有未保存修改</span><a-button :disabled="!!saving" @click="discard(row)">放弃修改</a-button><a-tooltip :title="disabledReason"><a-button type="primary" :disabled="!editable" :loading="saving === row.usage" @click="save(row)">保存</a-button></a-tooltip></div>
        </section>
      </div>
      <a-empty v-else-if="!loading && !error" description="暂无模型配置" />
    </a-spin>
  </div>
</template>

<style scoped>
.models { flex: 1; min-height: 0; overflow: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 16px; }
.models-tools, .model-title, .model-key, .model-row, .model-actions { display: flex; align-items: center; gap: 12px; }
.models-tools { justify-content: space-between; }
.models-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 28px; }
.model-form { min-width: 0; padding-right: 16px; }
.model-title { margin-bottom: 8px; }
.model-updated { margin-bottom: 16px; }
.model-row { gap: 32px; }
.model-key { flex-wrap: wrap; }
.model-secret { margin-top: 12px; }
.model-actions { justify-content: flex-end; padding-top: 12px; border-top: var(--border); }
.model-actions > span { margin-right: auto; }
@media (max-width: 1200px) { .models-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
