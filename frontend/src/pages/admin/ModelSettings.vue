<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { currentApiContext, isApiError } from '@/api/http';
import { loadModelSettings, saveModelSettings, testModelConnection, type ModelSettingsData, type ModelConnectionResult } from '@/api/settings';
import { confirmDiscard } from '@/composables/useUnsavedGuard';
import { useConfirmStore } from '@/stores/confirm';

const props = defineProps<{ readonly: boolean; reason: string }>();
const emit = defineEmits<{ dirty: [value: boolean] }>();
type ModelDraft = ModelSettingsData & { changeKey: boolean; apiKey: string };
const items = ref<ModelDraft[]>([]);
const saved = ref<ModelSettingsData[]>([]);
const selected = ref<ModelSettingsData['usage']>('qa');
const active = computed(() => items.value.find(row => row.usage === selected.value) ?? items.value[0]);
const loading = ref(false), saving = ref(''), testing = ref(''), error = ref(''), canManage = ref(false);
const connections = ref<Partial<Record<ModelSettingsData['usage'], ModelConnectionResult>>>({});
const confirm = useConfirmStore();
let sequence = 0, connectionSequence = 0;
const editable = computed(() => canManage.value && !props.readonly && !loading.value && !saving.value);
const disabledReason = computed(() => props.reason || (!canManage.value ? '没有系统配置管理权限' : ''));
const fields = (row: ModelSettingsData) => ({ provider: row.provider ?? 'openai', model: row.model, baseUrl: row.baseUrl, timeoutSec: row.timeoutSec, enabled: row.enabled, authMode: row.authMode, lockVersion: row.lockVersion });
const signature = (row: ModelDraft) => JSON.stringify({ ...fields(row), changeKey: row.changeKey, apiKey: row.apiKey });
const connectionSignature = (row: ModelDraft) => JSON.stringify({ provider: row.provider, baseUrl: row.baseUrl, authMode: row.authMode, changeKey: row.changeKey, apiKey: row.apiKey, timeoutSec: row.timeoutSec });
const payload = (row: ModelDraft) => ({ ...fields(row), ...(row.authMode === 'key' && row.changeKey ? { apiKey: row.apiKey } : {}) });
const dirtyRow = (row: ModelDraft) => {
  const old = saved.value.find(x => x.usage === row.usage);
  return !old || row.changeKey || JSON.stringify(fields(row)) !== JSON.stringify(fields(old));
};
const dirty = computed(() => items.value.some(dirtyRow));
watch(dirty, (value) => emit('dirty', value), { immediate: true });
watch(() => items.value.map(connectionSignature), (next, previous) => {
  for (const [i, row] of items.value.entries()) if (next[i] !== previous?.[i]) delete connections.value[row.usage];
});
const errText = (e: unknown) => isApiError(e) ? `${e.message}（${e.code}）` : '请求失败，请稍后重试';
const identity = () => currentApiContext().demoAs ?? '';
const context = () => JSON.stringify(currentApiContext());
const emptyKey = () => { for (const row of items.value) { row.apiKey = ''; row.changeKey = false; } };
const usageDescription = (usage: string) => usage === 'qa' ? '理解问题，生成查询与动作建议' : '辅助创建和修改本体定义';
const providerOptions = [{ value: 'openai', label: 'OpenAI 兼容服务' }, { value: 'ollama', label: 'Ollama' }];
const filterModel = (input: string, option: { value: string }) => option.value.toLowerCase().includes(input.toLowerCase());
function reusableKey(row: ModelDraft, requirePresent = true): boolean {
  const old = saved.value.find(x => x.usage === row.usage);
  try { return !!old && (!requirePresent || old.hasKey) && old.authMode === 'key' && (old.provider ?? 'openai') === row.provider && new URL(old.baseUrl).origin === new URL(row.baseUrl).origin; }
  catch { return false; }
}
function changeProvider(row: ModelDraft, provider: unknown) {
  if (provider !== 'openai' && provider !== 'ollama') return;
  row.provider = provider;
  row.baseUrl = provider === 'ollama' ? 'http://localhost:11434' : 'https://api.openai.com/v1';
  row.authMode = provider === 'ollama' ? 'none' : 'key';
  row.apiKey = ''; row.changeKey = false;
}
async function load() {
  const seq = ++sequence, user = identity();
  ++connectionSequence; testing.value = ''; connections.value = {};
  loading.value = true; error.value = '';
  try {
    const data = await loadModelSettings();
    if (seq !== sequence || user !== identity()) return;
    emptyKey();
    saved.value = data.items.map(row => ({ ...row, provider: row.provider ?? 'openai' }));
    items.value = saved.value.map(row => ({ ...row, changeKey: false, apiKey: '' }));
    canManage.value = data.canManage;
  } catch (e) { if (seq === sequence && user === identity()) error.value = `模型配置加载失败：${errText(e)}`; }
  finally { if (seq === sequence) loading.value = false; }
}
watch(context, () => { emptyKey(); items.value = []; saved.value = []; canManage.value = false; saving.value = ''; void load(); }, { immediate: true, flush: 'sync' });
onBeforeUnmount(() => { sequence++; connectionSequence++; emptyKey(); emit('dirty', false); });
async function reload() {
  if (loading.value || saving.value) return;
  const seq = sequence, scope = context();
  if (dirty.value && !(await confirmDiscard())) return;
  if (seq === sequence && scope === context()) await load();
}
function validation(row: ModelDraft, requireModel = true): string {
  if ((requireModel || row.model) && !/^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$/.test(row.model)) return '请输入有效模型名';
  try {
    const url = new URL(row.baseUrl);
    if (!['http:', 'https:'].includes(url.protocol) || row.baseUrl.includes('\\') || /[\s\x00-\x1f]/.test(row.baseUrl)) return '请输入有效的 HTTP(S) Base URL';
    if (url.username || url.password || /^https?:\/\/[^/]*@/i.test(row.baseUrl)) return '地址不能包含凭据，请填写密钥字段';
    if (row.baseUrl.includes('?') || url.search) return '暂不支持查询参数，请移除后重试';
    if (row.baseUrl.includes('#') || url.hash) return '地址片段不会发送给模型服务，请移除后重试';
  } catch { return '请输入有效的 HTTP(S) Base URL'; }
  if (!(row.timeoutSec >= 5 && row.timeoutSec <= 300)) return '超时须在 5～300 秒之间';
  if (row.authMode === 'key' && row.changeKey && !/^[\x21-\x7e]{1,4096}$/.test(row.apiKey)) return '新密钥不能为空或包含空白';
  if (row.authMode === 'key' && !row.changeKey && !reusableKey(row) && (!requireModel || row.enabled || !reusableKey(row, false))) return '请为当前服务填写新密钥';
  return '';
}
async function testConnection(row: ModelDraft) {
  if (!editable.value || testing.value) return;
  const invalid = validation(row, false);
  if (invalid) { connections.value[row.usage] = { ok: false, provider: row.provider, baseUrl: row.baseUrl, modelOptions: [], selectedModelAvailable: null, error: invalid }; return; }
  const id = ++connectionSequence, seq = sequence, scope = context(), snapshot = signature(row);
  const { lockVersion: _version, ...body } = payload(row);
  testing.value = row.usage;
  const current = () => id === connectionSequence && seq === sequence && scope === context() && items.value.includes(row) && snapshot === signature(row) && !props.readonly;
  try {
    const result = await testModelConnection(row.usage, body);
    if (current()) connections.value[row.usage] = result;
  } catch (e) {
    if (current()) connections.value[row.usage] = { ok: false, provider: row.provider, baseUrl: row.baseUrl, modelOptions: [], selectedModelAvailable: null, error: errText(e) };
  } finally { if (id === connectionSequence) testing.value = ''; }
}
async function save(row: ModelDraft) {
  if (!editable.value || !dirtyRow(row)) return;
  const invalid = validation(row);
  if (invalid) { message.error(invalid); return; }
  const seq = sequence, scope = context(), snapshot = signature(row), body = payload(row);
  const ok = await confirm.ask({ title: '保存模型配置', target: row.label, okText: '保存',
    fields: [{ label: '服务', value: row.provider === 'ollama' ? 'Ollama' : 'OpenAI 兼容服务' }, { label: '地址', value: row.baseUrl }, { label: '模型', value: row.model }, { label: '状态', value: row.enabled ? '启用' : '停用' }, { label: '认证', value: row.authMode === 'none' ? '无鉴权' : row.changeKey ? '更换密钥，保存后不可查看' : '保留当前密钥' }],
    impact: '配置对之后新发起的调用生效，并记入审计；本次保存不会调用模型。' });
  if (!ok || !editable.value) return;
  if (seq !== sequence || scope !== context() || !items.value.includes(row) || snapshot !== signature(row)) { message.info('配置已变化，请重新确认后保存'); return; }
  saving.value = row.usage; error.value = '';
  try {
    const data = await saveModelSettings(row.usage, body);
    if (seq !== sequence || scope !== context() || !items.value.includes(row)) return;
    const value = { ...data, provider: data.provider ?? 'openai' as const };
    saved.value = saved.value.map(x => x.usage === row.usage ? value : x);
    Object.assign(row, value, { changeKey: false, apiKey: '' });
    message.success('模型配置已保存');
  } catch (e) {
    if (seq !== sequence || scope !== context()) return;
    error.value = isApiError(e) && e.code === 'VERSION_CONFLICT'
      ? '配置已被其他人修改，当前输入已保留。请重新加载最新配置后再编辑。'
      : `保存失败，当前输入已保留：${errText(e)}`;
    message.error(error.value);
  } finally { if (seq === sequence) saving.value = ''; }
}
async function discard(row: ModelDraft) {
  if (!editable.value || !dirtyRow(row)) return;
  const seq = sequence, scope = context(), snapshot = signature(row);
  if (!(await confirmDiscard()) || seq !== sequence || scope !== context() || snapshot !== signature(row) || !items.value.includes(row)) return;
  Object.assign(row, saved.value.find((x) => x.usage === row.usage), { changeKey: false, apiKey: '' });
}
</script>

<template>
  <div class="models">
    <div class="models-tools"><span class="t-12 c-2">平台共用配置</span><a-button size="small" :disabled="loading || !!saving" @click="reload">重新加载</a-button></div>
    <a-alert v-if="error" type="error" show-icon :message="error" />
    <a-spin :spinning="loading" class="models-loading">
      <div v-if="active" class="models-shell">
        <nav class="model-usages" aria-label="模型用途">
          <button v-for="row in items" :key="row.usage" type="button" class="model-usage" :class="{ selected: active.usage === row.usage }" :aria-current="active.usage === row.usage ? 'page' : undefined" @click="selected = row.usage">
            <span class="model-usage-title t-14 fw-600">{{ row.usage === 'qa' ? '问数解析' : '本体构建助手' }}<span v-if="dirtyRow(row)" class="model-dirty" aria-label="有未保存修改" /></span>
            <span class="t-12 c-2">{{ usageDescription(row.usage) }}</span>
            <span class="t-12" :class="row.enabled ? 'c-2' : 'c-3'">{{ row.enabled ? '已启用' : '已停用' }}</span>
          </button>
        </nav>
        <section class="model-panel" :aria-label="active.label">
          <header class="model-heading">
            <div><h2 class="t-20 fw-600 c-1">{{ active.usage === 'qa' ? '问数解析' : '本体构建助手' }}</h2><p class="t-12 c-2">{{ usageDescription(active.usage) }}</p></div>
            <div class="model-source"><a-tag>{{ active.source === 'database' ? '平台配置' : '部署配置' }}</a-tag><span v-if="active.updatedAt" class="t-12 c-3">最近修改：{{ active.updatedAt }}</span></div>
          </header>
          <a-form layout="vertical" :disabled="!editable" class="model-body">
            <div class="model-service">
              <h3 class="t-14 fw-600 c-1 model-section-title">服务与模型</h3>
              <a-form-item label="服务类型"><a-select :value="active.provider" :options="providerOptions" @change="(value: unknown) => changeProvider(active!, value)" /></a-form-item>
              <a-form-item label="服务地址" required><a-input v-model:value="active.baseUrl" :maxlength="1000" class="num" autocomplete="off" :placeholder="active.provider === 'ollama' ? 'http://localhost:11434' : 'https://api.openai.com/v1'" /></a-form-item>
              <a-form-item label="模型名称" required extra="直接输入，或检测连接后从服务提供的模型中选择">
                <a-auto-complete v-model:value="active.model" :options="connections[active.usage]?.modelOptions ?? []" :filter-option="filterModel" :show-action="['focus']" class="model-full"><a-input :maxlength="128" autocomplete="off" placeholder="输入模型名称" class="num" /></a-auto-complete>
              </a-form-item>
              <a-button :loading="testing === active.usage" :disabled="!editable || !!testing" @click="testConnection(active)">检测连接并读取模型</a-button>
              <div v-if="connections[active.usage]" class="model-connection t-12" :class="{ failed: !connections[active.usage]!.ok }" :role="connections[active.usage]!.ok ? 'status' : 'alert'">
                <template v-if="connections[active.usage]!.ok">连接成功 · 读取到 {{ connections[active.usage]!.modelOptions.length }} 个模型<span v-if="active.model && !connections[active.usage]!.modelOptions.some(option => option.value === active!.model)" class="model-connection-note">目录中未找到当前模型，请核对名称；仍可手动配置。</span></template>
                <template v-else>{{ connections[active.usage]!.error || '连接失败，请检查服务地址与认证配置' }}</template>
              </div>
            </div>
            <div class="model-access">
              <h3 class="t-14 fw-600 c-1 model-section-title">访问与运行</h3>
              <a-form-item label="认证方式"><a-select v-model:value="active.authMode" :options="[{ value: 'key', label: 'API 密钥' }, { value: 'none', label: '无鉴权' }]" @change="active.apiKey = ''; active.changeKey = false" /></a-form-item>
              <a-form-item v-if="active.authMode === 'key'" label="密钥">
                <div class="model-key"><span class="t-12 c-2">{{ reusableKey(active) ? '已配置 · 不可查看' : '当前服务尚未配置密钥' }}<span v-if="reusableKey(active)"> · {{ active.keySource === 'database' ? '平台加密保存' : '部署配置' }}</span></span><a-checkbox v-model:checked="active.changeKey" @change="!active.changeKey && (active.apiKey = '')">{{ reusableKey(active) ? '更换密钥' : '填写密钥' }}</a-checkbox></div>
                <a-input-password v-if="active.changeKey" v-model:value="active.apiKey" :visibility-toggle="false" autocomplete="new-password" placeholder="输入新密钥" :maxlength="4096" class="model-secret" />
              </a-form-item>
              <div class="model-runtime"><a-form-item label="请求超时"><a-input-number v-model:value="active.timeoutSec" :min="5" :max="300" addon-after="秒" /></a-form-item><a-form-item label="状态"><a-switch v-model:checked="active.enabled" checked-children="启用" un-checked-children="停用" /></a-form-item></div>
            </div>
          </a-form>
          <footer class="model-actions"><span class="t-12 c-2">{{ dirtyRow(active) ? '有未保存修改' : '配置已同步' }}</span><a-button :disabled="!editable || !dirtyRow(active)" @click="discard(active)">放弃修改</a-button><a-tooltip :title="disabledReason"><a-button type="primary" :disabled="!editable || !dirtyRow(active)" :loading="saving === active.usage" @click="save(active)">保存配置</a-button></a-tooltip></footer>
        </section>
      </div>
      <a-empty v-else-if="!loading && !error" description="暂无模型配置" />
    </a-spin>
  </div>
</template>

<style scoped>
.models { flex: 1; min-height: 0; overflow: auto; padding: 20px 24px 24px; display: flex; flex-direction: column; gap: 16px; }
.models-tools, .model-heading, .model-key, .model-runtime, .model-actions { display: flex; align-items: center; gap: 12px; }
.models-tools { justify-content: space-between; }
.models-shell { display: grid; grid-template-columns: 216px minmax(0, 1fr); gap: 24px; align-items: start; }
.model-usages { display: flex; flex-direction: column; gap: 8px; }
.model-usage { appearance: none; border: 1px solid transparent; border-radius: var(--radius); background: transparent; color: var(--g9); padding: 16px; display: flex; flex-direction: column; gap: 8px; text-align: left; cursor: pointer; font: inherit; }
.model-usage:hover { background: var(--g2); }
.model-usage.selected { background: var(--brand-bg); border-color: var(--brand); }
.model-usage:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.model-usage-title { display: flex; align-items: center; gap: 8px; }
.model-dirty { width: 6px; height: 6px; border-radius: 50%; background: var(--warning); }
.model-panel { min-width: 0; border: var(--border); border-radius: var(--radius); background: var(--g1); }
.model-heading { justify-content: space-between; align-items: flex-start; padding: 24px; border-bottom: var(--border); gap: 20px; }
.model-heading h2, .model-heading p { margin: 0; }
.model-heading p { margin-top: 8px; }
.model-source { display: flex; align-items: flex-end; flex-direction: column; gap: 8px; }
.model-source :deep(.ant-tag) { margin: 0; }
.model-body { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 32px; padding: 24px; }
.model-service, .model-access { min-width: 0; }
.model-access { border-left: var(--border); padding-left: 32px; }
.model-section-title { margin: 0 0 24px; }
.model-full { width: 100%; }
.model-runtime { gap: 24px; align-items: flex-start; flex-wrap: wrap; }
.model-key { align-items: flex-start; flex-direction: column; }
.model-secret { margin-top: 12px; }
.model-connection { margin-top: 12px; color: var(--success); overflow-wrap: anywhere; }
.model-connection.failed { color: var(--error); }
.model-connection-note { display: block; color: var(--warning); margin-top: 4px; }
.model-actions { justify-content: flex-end; padding: 16px 24px; border-top: var(--border); }
.model-actions > span { margin-right: auto; }
@media (max-width: 1600px) { .models-shell { grid-template-columns: 184px minmax(0, 1fr); gap: 16px; } .model-body { gap: 24px; } .model-access { padding-left: 24px; } }
@media (max-width: 1450px) { .model-body { grid-template-columns: minmax(0, 1fr); } .model-access { padding: 24px 0 0; border-left: 0; border-top: var(--border); } .model-heading { flex-wrap: wrap; } .model-source { align-items: flex-start; } }
@media (max-width: 900px) { .models-shell { grid-template-columns: minmax(0, 1fr); } .model-usages { flex-direction: row; } .model-usage { flex: 1; } }
</style>
