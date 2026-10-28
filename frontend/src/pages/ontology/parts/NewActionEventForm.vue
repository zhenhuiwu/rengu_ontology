<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { apiClient, currentApiContext, isApiError, type PageResult } from '@/api/http';
import { actionContextKey, crWriteOn, crStart, crErrText, fetchCR, fetchCRItems, saveCRItems, crChanges, type CRKind } from '@/api/action';
import { useReadonly } from '@/composables/useReadonly';
import { useConfirmStore } from '@/stores/confirm';
import { applyModelChange, fetchCurrentAfterWrite } from '@/api/static';
import ActionExecutionEditor from './ActionExecutionEditor.vue';
import PayloadSchemaEditor from '../PayloadSchemaEditor.vue';
import type { ActionParam, ActionType } from '@/mock/actionEvent';

interface Field { k: string; label: string; kind?: 'input' | 'select' | 'switch' | 'textarea'; options?: { value: any; label: string }[]; required?: boolean }
interface RowForm { title: string; fields: () => Field[]; blank: () => Record<string, any> }
interface DraftRow { key: string; op: 'add' | 'update' | 'delete'; after?: unknown }
const props = defineProps<{ kind: CRKind; options: { join?: string; title?: string; desc?: string }; rowForms: Record<string, RowForm>; assistantDraft?: Record<string, unknown> }>();
const open = defineModel<boolean>('open', { default: false });
const emit = defineEmits<{ saved: [id: string] }>();
const router = useRouter();
const route = useRoute();
const confirm = useConfirmStore();
const { readonly, reason } = useReadonly();
const api = apiClient('static');
const action = apiClient('action');
const lockVersion = ref<number>();
const dynamic = apiClient('dynamic');
const form = reactive<Record<string, any>>({});
const rows = reactive<Record<string, Record<string, any>[]>>({});
const owners = ref<{ id: string; name: string }[]>([]);
const objects = ref<{ id: string; name: string; api: string; status?: string }[]>([]);
const rules = ref<{ id: string; name: string }[]>([]);
const busy = ref(false);
const loading = ref(false);
const error = ref('');
const saveError = ref('');
const savedState = ref('');
const fingerprint = computed(() => JSON.stringify([form, rows]));
const saved = computed(() => !!savedState.value && savedState.value === fingerprint.value);
const baseline: Record<string, DraftRow[]> = {};
const crId = ref('');
const version = ref<number>();
const loadedKey = ref('');
const activeTab = ref('def');
const executionEditors = ref<{ validate: () => boolean }[]>([]);
const newAction = computed(() => ({ ...form, id: form.api, target: objects.value.find(o => o.id === form.targetKind)?.name ?? '', targetApi: objects.value.find(o => o.id === form.targetKind)?.api ?? '' }) as ActionType);
const label = computed(() => props.kind === 'action' ? '动作类型' : '事件类型');
const validContext = computed(() => loadedKey.value === actionContextKey());
const platformActionEvent = computed(() => props.kind === 'event' && form.api === 'ActionExecuted' && form.source === '动作');
const fields = computed<Field[]>(() => [
  { k: 'name', label: '名称', required: true }, { k: 'api', label: 'API 名', required: true },
  ...(props.kind === 'action' ? [
    { k: 'group', label: '分组' }, { k: 'targetKind', label: '关联对象', kind: 'select', required: true },
  ] : [
    { k: 'source', label: '来源', kind: 'select', required: true, options: ['数据同步', '规则', '动作', '外部系统'].map(value => ({ value, label: value })) },
    { k: 'sourceRef', label: '来源说明' }, { k: 'ruleId', label: '来源规则', kind: 'select' },
    { k: 'objKind', label: '关联对象', kind: 'select', required: true },
  ]) as Field[],
  { k: 'owner', label: '负责人', kind: 'select', required: true }, { k: 'desc', label: '说明', kind: 'textarea' },
  ...(props.kind === 'action' ? [
    { k: 'runners', label: '允许发起人' },
  ] : [
    { k: 'dedupKey', label: '去重键' }, { k: 'dedupWindow', label: '去重窗口' },
    { k: 'retention', label: '保留时长' }, { k: 'delivery', label: '投递保证' }, { k: 'ordering', label: '顺序保证' },
  ]) as Field[],
]);
const choices = (field: Field) => field.k === 'owner' ? owners.value.map(u => ({ value: u.id, label: `${u.name}（${u.id}）` }))
  : ['targetKind', 'objKind'].includes(field.k) ? [
    ...objects.value.map(o => ({ value: o.id, label: `${o.name}（${o.api}）` })),
    ...(field.k === 'objKind' && platformActionEvent.value ? [{ value: 'platform:ActionRun', label: '动作执行记录（平台）' }] : []),
  ]
    : field.k === 'ruleId' ? rules.value.map(r => ({ value: r.id, label: `${r.id} · ${r.name}` })) : field.options;
const rowKey = (section: string, row: Record<string, any>) => section === 'config' ? 'cfg'
  : section === 'edits' ? `${row.op}:${row.obj}` : String(row.api ?? row.target ?? row.name ?? '');
function validSchema(node: any, depth = 0): boolean {
  if (depth > 4) return false;
  if (node.type === '对象') {
    const fields = node.schema?.fields;
    return Array.isArray(fields) && fields.length > 0 && fields.length <= 100 && new Set(fields.map((f: any) => f.api)).size === fields.length && fields.every((f: any) => f.name?.trim() && /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(f.api) && validSchema(f, depth + 1));
  }
  if (node.type === '数组') return !!node.schema?.item && validSchema(node.schema.item, depth + 1);
  return true;
}
const canSave = computed(() => !readonly.value && action.isReal() && lockVersion.value !== undefined && !busy.value && !loading.value && validContext.value && !error.value
  && fields.value.every(f => !f.required || String(form[f.k] ?? '').trim())
  && /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(form.api ?? '')
  && (props.kind !== 'event' || form.source !== '规则' || !!form.ruleId)
  && Object.entries(props.rowForms).every(([section, spec]) => {
    const values = rows[section] ?? [];
    return new Set(values.map(row => rowKey(section, row))).size === values.length
      && values.every(row => (section !== 'payload' || validSchema(row)) && spec.fields().every(f => !f.required || (section === 'config' && !row.sysId) || String(row[f.k] ?? '').trim()));
  }));

async function load() {
  const ctx = { ...currentApiContext() }, key = actionContextKey(ctx);
  loadedKey.value = key; loading.value = true; error.value = ''; saveError.value = ''; savedState.value = ''; lockVersion.value = undefined;
  for (const section of Object.keys(baseline)) delete baseline[section];
  owners.value = []; objects.value = []; rules.value = []; crId.value = ''; version.value = undefined;
  for (const k of Object.keys(form)) delete form[k];
  Object.assign(form, { name: '', api: '', group: '', targetKind: null, objKind: null, owner: '', desc: '', source: '外部系统', sourceRef: '', ruleId: null, retention: '30 天', runners: '', approval: '免审', dedupKey: '', dedupWindow: '24 小时', delivery: '', ordering: '' });
  for (const section of Object.keys(props.rowForms)) rows[section] = [];
  const options = { readonlyParams: false, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' }, query: { ver: ctx.ver, previewAs: ctx.previewAs } };
  try {
    lockVersion.value = (await fetchCurrentAfterWrite()).lockVersion;
    const [people, first] = await Promise.all([
      api.get<{ id: string; name: string }[]>('/build/owners', { ...options, query: { ...options.query, kind: props.kind } }),
      api.get<PageResult<{ id: string; name: string; api: string; status?: string }>>('/object-types', { ...options, query: { ...options.query, page: 1, size: 200 } }),
    ]);
    if (key !== actionContextKey() || !open.value) return;
    const all = [...first.items];
    for (let page = 2; all.length < first.total; page++) {
      const next = await api.get<typeof first>('/object-types', { ...options, query: { ...options.query, page, size: 200 } });
      if (key !== actionContextKey() || !open.value) return;
      if (!next.items.length) throw new Error('对象目录分页不完整');
      all.push(...next.items);
    }
    owners.value = people; objects.value = all.filter(o => !o.status || o.status === '已发布');
    form.owner = people.find(u => u.id === ctx.demoAs)?.id ?? '';
    if (props.kind === 'action' && props.assistantDraft) for (const field of ['name', 'api', 'group', 'targetKind', 'desc']) {
      if (typeof props.assistantDraft[field] === 'string') form[field] = props.assistantDraft[field];
    }
    if (props.kind === 'event' && props.assistantDraft) {
      for (const field of ['name', 'api', 'sourceRef', 'desc', 'dedupKey', 'dedupWindow', 'retention']) {
        if (typeof props.assistantDraft[field] === 'string') form[field] = props.assistantDraft[field];
      }
      const source = props.assistantDraft.source;
      form.source = typeof source === 'string' && ['数据同步', '规则', '动作', '外部系统'].includes(source) ? source : '';
      const object = props.assistantDraft.objKind;
      if (typeof object === 'string') {
        const matches = objects.value.filter(o => [o.id, o.api, o.name].includes(object));
        if (matches.length === 1) form.objKind = matches[0].id;
      }
    }
    const restoredId = String(route.query.createType ?? '');
    const restoredCr = String(route.query.createCr ?? '');
    if (restoredId && restoredCr) {
      const header = await fetchCR(restoredCr);
      const items = await fetchCRItems(restoredCr, { kind: props.kind, targetId: restoredId });
      if (key !== actionContextKey() || !open.value) return;
      crId.value = header.id; version.value = header.lockVersion; form.api = restoredId;
      for (const item of items) {
        (baseline[item.section] ??= []).push({ key: item.key, op: item.op, after: item.after });
        if (item.op === 'delete') continue;
        if (item.section === 'def') form[item.key] = item.after;
        else if (item.section in props.rowForms) rows[item.section].push(item.after as Record<string, any>);
      }
      if (!people.some(u => u.id === form.owner)) {
        const matches = people.filter(u => u.name === form.owner);
        form.owner = matches.length === 1 ? matches[0].id : '';
      }
      savedState.value = fingerprint.value;
    }
  } catch (e) { if (key === actionContextKey() && open.value) error.value = crErrText(e); }
  finally { if (key === actionContextKey()) loading.value = false; }
}
watch(open, value => { if (value) { activeTab.value = 'def'; void load(); } }, { immediate: true });
watch(platformActionEvent, enabled => { if (!enabled && form.objKind === 'platform:ActionRun') form.objKind = null; });
watch(() => actionContextKey(), () => { if (open.value) { open.value = false; owners.value = []; objects.value = []; rules.value = []; } }, { flush: 'sync' });
watch(() => form.source, async source => {
  if (!open.value || props.kind !== 'event' || source !== '规则') return;
  const key = actionContextKey();
  try {
    const all: { id: string; name: string }[] = [];
    for (let page = 1;; page++) {
      const result = await dynamic.get<PageResult<{ id: string; name: string }>>('/rules', { query: { page, size: 200, status: '已发布' } });
      if (key !== actionContextKey() || !open.value) return;
      all.push(...result.items);
      if (all.length >= result.total) break;
      if (!result.items.length) throw new Error('规则目录分页不完整');
    }
    rules.value = all;
  } catch (e) { if (key === actionContextKey()) message.error(`来源规则读取失败：${crErrText(e)}`); }
});
async function remove(section: string, index: number) {
  if (readonly.value || busy.value) return;
  if (await confirm.ask({ title: '删除草稿条目', target: `${props.rowForms[section].title} · ${rowKey(section, rows[section][index])}`, impact: '保存草稿后生效' })) rows[section].splice(index, 1);
}
function mergeRows(base: DraftRow[], local: DraftRow[], remote: DraftRow[]): DraftRow[] {
  const encode = (row?: DraftRow) => JSON.stringify(row, (_key, value) => value && typeof value === 'object' && !Array.isArray(value)
    ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))) : value);
  const maps = [base, local, remote].map(items => new Map(items.map(row => [row.key, row])));
  const result: DraftRow[] = [];
  for (const key of new Set([...maps[2].keys(), ...maps[1].keys(), ...maps[0].keys()])) {
    const [before, ours, theirs] = maps.map(map => map.get(key));
    const same = (a?: DraftRow, b?: DraftRow) => encode(a) === encode(b);
    if (!same(ours, before) && !same(theirs, before) && !same(ours, theirs)) throw new Error(`字段 ${key} 已在别处修改，本地输入已保留，请重新载入后比较`);
    const row = same(ours, before) ? theirs : ours;
    if (row) result.push(row);
  }
  return result;
}
async function save() {
  if (!canSave.value || lockVersion.value === undefined) return;
  if (props.kind === 'action' && executionEditors.value.some(editor => !editor.validate())) { message.error('请检查本体编辑或副作用的映射配置'); return; }
  const key = actionContextKey(), id = String(form.api);
  if (crId.value) { message.error('历史草稿不能直接保存，请先处理该草稿'); return; }
  saveError.value = '';
  busy.value = true;
  try {
    const definitions = fields.value.map(f => ({ key: f.k, op: 'add' as const, after: form[f.k] ?? '' }));
    if (props.kind === 'action') definitions.push({ key: 'approval', op: 'add', after: '免审' });
    const sections = [{ section: 'def', rows: definitions }, ...Object.keys(props.rowForms).map(section => ({ section, rows: rows[section].map(row => ({ key: rowKey(section, row), op: 'add' as const, after: section === 'config' && !row.sysId ? { sysId: null, endpoint: '—', idemKey: '' } : row })) }))];
    const result = await applyModelChange({ kind: props.kind, id, name: form.name, isNew: true }, sections, lockVersion.value);
    if (key !== actionContextKey() || !open.value) return;
    savedState.value = fingerprint.value;
    message.success('已保存并生效'); open.value = false; emit('saved', result.targetId);
  } catch (e) { if (key === actionContextKey() && open.value) { saveError.value = crErrText(e); message.error(saveError.value); } }
  finally { busy.value = false; }
}

async function close() {
  if (busy.value) return;
  if (!saved.value && (form.api || form.name) && !await confirm.ask({ title: '关闭新建表单', target: label.value, impact: '未保存输入将丢失；已入库的草稿仍保留' })) return;
  open.value = false;
  if (route.query.createType) await router.replace({ query: { ...route.query, createType: undefined, createCr: undefined } });
}
</script>

<template>
  <a-drawer :open="open" :title="`新建${label}`" :width="800" :closable="!busy" :mask-closable="false" :keyboard="!busy" :body-style="{ overflow: 'auto' }" @close="close">
    <a-alert v-if="readonly" type="warning" :message="reason" show-icon />
    <a-alert v-if="saveError" type="error" :message="saveError" show-icon />
    <a-alert v-if="error" type="error" :message="error" show-icon /><a-button v-if="error" @click="load">重新读取</a-button>
    <a-button v-else-if="crId" :disabled="busy || loading" @click="async () => { if (await confirm.ask({ title: '重新载入草稿', target: crId, impact: '未保存输入将被数据库中的草稿替换' })) await load(); }">重新载入草稿</a-button>
    <a-spin :spinning="loading">
      <a-tabs v-model:active-key="activeTab">
        <a-tab-pane key="def" tab="基本信息">
          <a-form layout="vertical" :disabled="readonly || busy || loading || !validContext" class="nt-grid">
            <a-form-item v-for="f in fields" :key="f.k" :label="f.label" :required="f.required" :class="{ 'nt-wide': f.kind === 'textarea' }">
              <a-select v-if="f.kind === 'select'" v-model:value="form[f.k]" :options="choices(f)" allow-clear show-search option-filter-prop="label" />
              <a-textarea v-else-if="f.kind === 'textarea'" v-model:value="form[f.k]" :rows="3" :maxlength="4000" />
              <a-input v-else v-model:value="form[f.k]" :maxlength="['name', 'api'].includes(f.k) ? 64 : f.k === 'group' ? 32 : 64" :disabled="f.k === 'api' && !!crId" :placeholder="f.k === 'api' ? '英文字母、数字和下划线，以字母或下划线开头' : undefined" />
            </a-form-item>
          </a-form>
        </a-tab-pane>
        <a-tab-pane v-for="(spec, section) in rowForms" :key="section" :tab="spec.title">
          <div v-for="(row, index) in rows[section]" :key="index" class="nt-row">
            <div class="nt-row-head t-14 fw-600"><span>{{ spec.title }} {{ index + 1 }}</span><a-button size="small" :disabled="readonly || busy" @click="remove(String(section), index)">删除</a-button></div>
            <ActionExecutionEditor v-if="kind === 'action' && (section === 'edits' || section === 'effects')" ref="executionEditors" v-model="rows[section][index]" :section="section" :action="newAction" :params="(rows.params ?? []) as ActionParam[]" />
            <a-form v-else layout="vertical" class="nt-grid" :disabled="readonly || busy || loading || !validContext">
              <a-form-item v-for="f in spec.fields()" :key="f.k" :label="f.label" :required="f.required">
                <a-select v-if="f.kind === 'select'" v-model:value="row[f.k]" :options="f.options" />
                <a-switch v-else-if="f.kind === 'switch'" v-model:checked="row[f.k]" />
                <a-textarea v-else-if="f.kind === 'textarea'" v-model:value="row[f.k]" :rows="2" />
                <a-input v-else v-model:value="row[f.k]" :disabled="section === 'config' && !row.sysId" />
              </a-form-item>
            </a-form>
            <PayloadSchemaEditor v-if="kind === 'event' && section === 'payload'" :node="row" />
          </div>
          <a-button :disabled="readonly || busy || loading || (section === 'config' && rows[section]?.length > 0)" @click="rows[section].push(section === 'config' ? { sysId: null, endpoint: '—', idemKey: '' } : spec.blank())">添加{{ spec.title }}</a-button>
        </a-tab-pane>
      </a-tabs>
    </a-spin>
    <template #footer>
      <div class="nt-footer"><span class="t-12 c-3">保存后生效</span><a-space><a-button @click="close">关闭</a-button><a-button type="primary" :loading="busy" :disabled="!canSave" @click="save">保存</a-button></a-space></div>
    </template>
  </a-drawer>
</template>

<style scoped>
.nt-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
.nt-wide { grid-column: 1 / -1; }
.nt-row { padding: 12px; margin-bottom: 12px; border: var(--border); border-radius: var(--radius); }
.nt-row-head, .nt-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.nt-row-head { margin-bottom: 12px; }
</style>
