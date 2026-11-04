<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { apiClient, currentApiContext, type PageResult } from '@/api/http';
import { saveLifecycle, type LifecycleDto } from '@/api/dynamic';
import { fetchCurrentAfterWrite, invalidateStaticAfterWrite } from '@/api/static';
import { useReadonly } from '@/composables/useReadonly';
import { confirmDiscard, useUnsavedGuard } from '@/composables/useUnsavedGuard';
import type { DefinitionProperty } from '@/api/definitionForms';
import type { LcState, LcTransition } from '@/mock/lifecycle';

const props = defineProps<{ open: boolean; objectId: string; lifecycle: LifecycleDto | null; readonly: boolean }>();
const emit = defineEmits<{ 'update:open': [value: boolean]; saved: [ver: string] }>();
const api = apiClient('dynamic');
const route = useRoute(), router = useRouter();
const { readonly: contextReadonly } = useReadonly();
const form = reactive({ field: '', desc: '', states: [] as LcState[], transitions: [] as LcTransition[] });
const loading = ref(false), saving = ref(false), loadError = ref(''), saveError = ref('');
const fields = ref<DefinitionProperty[]>([]);
const releaseLock = ref(0), expectedVersion = ref(0), initial = ref('');
const originalStates = ref(new WeakSet<LcState>());
type Choice = { id: string; name: string; object: string };
const catalog = ref<{ actions: Choice[]; events: Choice[] }>({ actions: [], events: [] });
const catalogLoading = ref(false), catalogError = ref('');
let sequence = 0, disposed = false, controller: AbortController | undefined;
const context = () => JSON.stringify([props.objectId, currentApiContext()]);
const valid = (seq: number, key: string) => !disposed && props.open && seq === sequence && key === context();
const dirty = computed(() => props.open && !loading.value && JSON.stringify(form) !== initial.value);
useUnsavedGuard(dirty);
onBeforeRouteUpdate(async (to, from) =>
  (to.query.type !== from.query.type || to.query.tab !== from.query.tab) && dirty.value ? await confirmDiscard() : true);
onBeforeUnmount(() => { disposed = true; ++sequence; controller?.abort(); });
const editable = computed(() => !props.readonly && !contextReadonly.value);
const stateFields = computed(() => fields.value.filter(p =>
  ['string', 'text', 'enum', 'varchar', 'char', 'nvarchar'].includes(p.type.toLowerCase()) || !!p.enumName)
  .map(p => ({ value: p.api, label: `${p.name} · ${p.api}` })));
const stateOptions = computed(() => form.states.filter(s => s.key).map(s => ({ value: s.key, label: s.name || s.key })));
const needsCatalog = computed(() => form.transitions.some(t => t.trigger === '事件' || t.trigger === '动作'));
const stateKinds = ['初始', '中间', '终态'];
const triggers = ['人工', '事件', '动作', '系统'];
const problem = computed(() => {
  if (!form.field) return '请选择状态字段';
  if (!form.states.length) return '请添加状态';
  if (form.states.some(s => !s.key.trim() || !s.name.trim())) return '请填写状态值和名称';
  if (new Set(form.states.map(s => s.key)).size !== form.states.length) return '状态值不能重复';
  if (form.states.filter(s => s.kind === '初始').length !== 1) return '请选择一个初始状态';
  const keys = new Set(form.states.map(s => s.key));
  for (const t of form.transitions) {
    if (!keys.has(t.from) || !keys.has(t.to)) return '请选择迁移的原状态和新状态';
    if (form.states.find(s => s.key === t.from)?.kind === '终态') return '终态不能设置离开的迁移';
    if (!t.by.trim()) return '请填写触发来源';
    if (t.trigger === '事件' && !t.eventId || t.trigger === '动作' && !t.actionId) return '请选择触发的事件或动作';
  }
  return '';
});
function addState() {
  let position = form.states.length;
  while (form.states.some(s => s.x === 40 + position % 4 * 260 && s.y === 80 + Math.floor(position / 4) * 180)) position++;
  form.states.push({ key: '', name: '', kind: form.states.length ? '中间' : '初始', desc: '', count: 0,
    x: 40 + position % 4 * 260, y: 80 + Math.floor(position / 4) * 180 });
}
function removeState(index: number) {
  const [state] = form.states.splice(index, 1);
  if (state!.key && !form.states.some(s => s.key === state!.key)) {
    form.transitions = form.transitions.filter(t => t.from !== state!.key && t.to !== state!.key);
  }
}
function addTransition() {
  const next = Math.max(0, ...form.transitions.map(t => Number(t.id.replace(/^T/, '')) || 0)) + 1;
  form.transitions.push({ id: `T${next}`, from: '', to: '', trigger: '人工', by: '', guard: '', effect: '', weekly: 0 });
}
function triggerChanged(t: LcTransition) { t.by = ''; delete t.actionId; delete t.eventId; }
function selectReference(t: LcTransition, id: string) {
  const choices = t.trigger === '事件' ? catalog.value.events : catalog.value.actions;
  t.by = choices.find(c => c.id === id)?.name ?? '';
}
async function loadCatalog() {
  if (!props.open || !needsCatalog.value || catalogLoading.value) return;
  const seq = sequence, key = context(); catalogLoading.value = true; catalogError.value = '';
  try {
    const data = await api.get<{ actions?: Choice[]; events?: Choice[] }>('/meta/capabilities', { signal: controller?.signal });
    if (valid(seq, key)) catalog.value = {
      actions: (data.actions ?? []).filter(c => c.object === props.objectId),
      events: (data.events ?? []).filter(c => c.object === props.objectId),
    };
  } catch (cause) { if (valid(seq, key)) catalogError.value = cause instanceof Error ? cause.message : '触发来源加载失败'; }
  finally { if (valid(seq, key)) catalogLoading.value = false; }
}
watch(needsCatalog, value => { if (value && !catalog.value.actions.length && !catalog.value.events.length) void loadCatalog(); });
async function load() {
  const seq = ++sequence, key = context(); controller?.abort(); controller = new AbortController();
  loading.value = true; saving.value = false; loadError.value = ''; saveError.value = ''; catalogError.value = ''; catalogLoading.value = false;
  fields.value = []; catalog.value = { actions: [], events: [] };
  expectedVersion.value = props.lifecycle?.version ?? 0;
  const lc = props.lifecycle;
  Object.assign(form, { field: lc?.field ?? '', desc: lc?.desc ?? '',
    states: lc ? lc.states.map(s => ({ ...s })) : [], transitions: lc ? lc.transitions.map(t => ({ ...t })) : [] });
  originalStates.value = new WeakSet(form.states);
  if (new Set(form.states.map(s => `${s.x}:${s.y}`)).size < form.states.length) {
    form.states.forEach((s, i) => { s.x = 40 + i % 4 * 260; s.y = 80 + Math.floor(i / 4) * 180; });
  }
  if (!lc) addState();
  initial.value = JSON.stringify(form);
  try {
    const [head, properties] = await Promise.all([fetchCurrentAfterWrite(), (async () => {
      const rows: DefinitionProperty[] = [];
      for (let page = 1; ; page++) {
        const result = await api.get<PageResult<DefinitionProperty>>(`/object-types/${encodeURIComponent(props.objectId)}/properties`,
          { query: { page, size: 200 }, signal: controller!.signal });
        if (!valid(seq, key)) return [];
        rows.push(...result.items);
        if (!result.items.length || rows.length >= result.total) return rows;
      }
    })()]);
    if (!valid(seq, key)) return;
    releaseLock.value = head.lockVersion; fields.value = properties;
    if (lc && !properties.some(p => p.api === form.field)) {
      form.field = properties.find(p => p.name === lc.field || lc.field.endsWith(`（${p.api}）`) || lc.field === `${props.objectId}.${p.api}`)?.api ?? '';
    }
    initial.value = JSON.stringify(form);
    if (needsCatalog.value) void loadCatalog();
  } catch (cause) { if (valid(seq, key)) loadError.value = cause instanceof Error ? cause.message : '生命周期编辑数据加载失败'; }
  finally { if (valid(seq, key)) loading.value = false; }
}
watch(() => [props.open, context()], () => {
  ++sequence; controller?.abort();
  if (props.open) void load();
}, { immediate: true, flush: 'sync' });
async function close() {
  if (saving.value || dirty.value && !(await confirmDiscard())) return;
  emit('update:open', false);
}
async function goToProperties() {
  if (saving.value || dirty.value && !(await confirmDiscard())) return;
  emit('update:open', false);
  await router.replace({ query: { ...route.query, tab: 'properties' } });
}
async function save() {
  if (!editable.value || loading.value || saving.value || loadError.value || problem.value || !dirty.value) return;
  const seq = sequence, key = context(), id = props.objectId;
  saving.value = true; saveError.value = '';
  try {
    const result = await saveLifecycle(id, { ...form, expectedVersion: expectedVersion.value, releaseLockVersion: releaseLock.value });
    if (!valid(seq, key)) return;
    initial.value = JSON.stringify(form);
    saving.value = false;
    invalidateStaticAfterWrite(result.ver);
    message.success('生命周期已保存');
    emit('update:open', false); emit('saved', result.ver);
  } catch (cause) { if (valid(seq, key)) saveError.value = cause instanceof Error ? cause.message : '生命周期保存失败'; }
  finally { if (!disposed && seq === sequence) saving.value = false; }
}
</script>

<template>
  <a-modal :open="open" :title="lifecycle ? '编辑生命周期' : '新建生命周期'" :width="1120" :confirm-loading="saving"
    ok-text="保存" :ok-button-props="{ disabled: !editable || loading || !!loadError || !!problem || !dirty }"
    :mask-closable="!saving" :keyboard="!saving" :closable="!saving" @ok="save" @cancel="close">
    <a-spin :spinning="loading">
      <div class="lce-content">
        <a-alert v-if="loadError" type="error" show-icon :message="loadError"><template #action><a-button size="small" @click="load">重试</a-button></template></a-alert>
        <a-alert v-if="saveError" type="error" show-icon :message="saveError" />
        <a-form v-if="!loading && !loadError" layout="vertical" :disabled="saving || !editable">
          <div class="lce-basics">
            <a-form-item label="状态字段" required>
              <a-select v-model:value="form.field" aria-label="状态字段" :options="stateFields" show-search option-filter-prop="label" placeholder="选择文本或枚举属性" />
              <span v-if="!loading && !loadError && !stateFields.length" class="t-12 c-3">请先添加文本或枚举状态字段。<a-button type="link" size="small" @click="goToProperties">前往属性</a-button></span>
            </a-form-item>
            <a-form-item label="说明"><a-input v-model:value="form.desc" :maxlength="2000" placeholder="描述此对象的状态流转" /></a-form-item>
          </div>
          <div class="lce-heading"><span class="fw-600">状态</span><a-button size="small" @click="addState">添加状态</a-button></div>
          <div class="lce-state-labels"><span>状态值</span><span>显示名称</span><span>类型</span><span>说明</span><span /></div>
          <div v-for="(state, index) in form.states" :key="index" class="lce-state-row">
            <a-input v-model:value="state.key" :aria-label="`状态值 ${index + 1}`" :disabled="saving || !editable || originalStates.has(state)" :maxlength="64" placeholder="库中的实际值" />
            <a-input v-model:value="state.name" :aria-label="`状态名称 ${index + 1}`" :maxlength="64" placeholder="显示名称" />
            <a-select v-model:value="state.kind" :aria-label="`状态类型 ${index + 1}`" :options="stateKinds.map(value => ({value, label: value === '初始' ? '初始状态' : value === '终态' ? '终态' : '中间状态'}))" />
            <a-input v-model:value="state.desc" :aria-label="`状态说明 ${index + 1}`" :maxlength="2000" placeholder="可选" />
            <a-button type="text" danger :aria-label="`移除状态 ${index + 1}`" @click="removeState(index)">移除</a-button>
          </div>
          <div class="lce-heading"><span class="fw-600">迁移</span><a-button size="small" :disabled="form.states.length < 2" @click="addTransition">添加迁移</a-button></div>
          <div v-if="!form.transitions.length" class="lce-no-transitions c-3">尚未添加迁移</div>
          <div v-for="(transition, index) in form.transitions" :key="transition.id" class="lce-transition">
            <div class="lce-transition-top"><span class="num fw-600">{{ transition.id }}</span><a-button type="text" danger :aria-label="`移除迁移 ${index + 1}`" @click="form.transitions.splice(index, 1)">移除</a-button></div>
            <div class="lce-transition-fields">
              <a-form-item label="原状态" required><a-select v-model:value="transition.from" :aria-label="`原状态 ${index + 1}`" :options="stateOptions.filter(s => !form.states.some(state => state.key === s.value && state.kind === '终态'))" /></a-form-item>
              <a-form-item label="新状态" required><a-select v-model:value="transition.to" :aria-label="`新状态 ${index + 1}`" :options="stateOptions" /></a-form-item>
              <a-form-item label="触发方式"><a-select v-model:value="transition.trigger" :aria-label="`触发方式 ${index + 1}`" :options="triggers.map(value => ({value, label:value}))" @change="triggerChanged(transition)" /></a-form-item>
              <a-form-item label="触发来源" required>
                <a-select v-if="transition.trigger === '事件'" v-model:value="transition.eventId" :aria-label="`事件来源 ${index + 1}`" :loading="catalogLoading" :options="catalog.events.map(c => ({value:c.id,label:c.name}))" show-search option-filter-prop="label" @change="selectReference(transition, transition.eventId!)" />
                <a-select v-else-if="transition.trigger === '动作'" v-model:value="transition.actionId" :aria-label="`动作来源 ${index + 1}`" :loading="catalogLoading" :options="catalog.actions.map(c => ({value:c.id,label:c.name}))" show-search option-filter-prop="label" @change="selectReference(transition, transition.actionId!)" />
                <a-input v-else v-model:value="transition.by" :aria-label="`触发来源 ${index + 1}`" :maxlength="200" placeholder="操作或任务名称" />
              </a-form-item>
            </div>
            <div class="lce-basics">
              <a-form-item label="条件说明"><a-input v-model:value="transition.guard" :aria-label="`条件说明 ${index + 1}`" :maxlength="2000" /></a-form-item>
              <a-form-item label="结果说明"><a-input v-model:value="transition.effect" :aria-label="`结果说明 ${index + 1}`" :maxlength="2000" /></a-form-item>
            </div>
          </div>
          <a-alert v-if="catalogError" type="error" show-icon :message="catalogError"><template #action><a-button size="small" @click="loadCatalog">重试</a-button></template></a-alert>
          <div v-if="problem && !loading" class="t-12 c-3 lce-validation">{{ problem }}</div>
        </a-form>
      </div>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.lce-content { min-height: 200px; max-height: calc(100vh - 260px); overflow-y: auto; padding: 4px 8px 4px 0; }
.lce-content > :deep(.ant-alert) { margin-bottom: 16px; }
.lce-basics { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.lce-heading { display: flex; align-items: center; justify-content: space-between; padding: 16px 0 12px; }
.lce-state-labels, .lce-state-row { display: grid; grid-template-columns: 1fr 1fr 140px 1.4fr 60px; gap: 12px; align-items: center; }
.lce-state-labels { padding-bottom: 8px; color: var(--text-3); font-size: 12px; }
.lce-state-row { margin-bottom: 10px; }
.lce-transition { border: var(--border); border-radius: var(--radius); padding: 12px 16px 0; margin-bottom: 12px; }
.lce-transition-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.lce-transition-fields { display: grid; grid-template-columns: 1fr 1fr 120px 1.4fr; gap: 16px; }
.lce-no-transitions { padding: 20px; text-align: center; border: var(--border); border-radius: var(--radius); }
.lce-validation { margin-top: 12px; }
@media (max-width: 900px) { .lce-transition-fields { grid-template-columns: 1fr 1fr; } .lce-state-labels, .lce-state-row { grid-template-columns: 1fr 1fr 120px 1fr 60px; gap: 8px; } }
</style>
