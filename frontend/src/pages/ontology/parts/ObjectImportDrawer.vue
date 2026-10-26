<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { currentApiContext } from '@/api/http';
import { listDatasources, listDsTables, isDataReal } from '@/api/datasources';
import { definitionChoices } from '@/api/definitionForms';
import { previewObjectImport, commitObjectImport, type ImportPreview, type ImportObject } from '@/api/objectImport';
import type { Datasource, DsTable } from '@/mock/datasources';
import { useUnsavedGuard, confirmDiscard } from '@/composables/useUnsavedGuard';
import { useReadonly } from '@/composables/useReadonly';
import StartChangeModal from './StartChangeModal.vue';
const open = defineModel<boolean>('open', { default: false });
const emit = defineEmits<{ saved: [result: { id: string; crId: string }] }>();
const { readonly, reason } = useReadonly();
const sources = ref<Datasource[]>([]), tables = ref<DsTable[]>([]), owners = ref<{ value: string; label: string }[]>([]);
const datasource = ref<string>(), schema = ref<string>(), selected = ref<string[]>([]);
const preview = ref<ImportPreview>(), objects = ref<ImportObject[]>([]), selectedLinks = ref<string[]>([]);
const loading = ref(false), busy = ref(false), error = ref(''), changeOpen = ref(false);
const initialSnapshot = ref('');
const formSignature = () => JSON.stringify([objects.value, selectedLinks.value]);
const dirty = computed(() => open.value && !!preview.value && !!initialSnapshot.value && initialSnapshot.value !== formSignature());
const guard = useUnsavedGuard(dirty);
function close() { if (!busy.value) void guard(() => { open.value = false; }); }
let seq = 0, tablesSeq = 0, previewSeq = 0;
const ctxKey = () => JSON.stringify(currentApiContext());
function resetPreview() { initialSnapshot.value = ''; previewSeq++; preview.value = undefined; objects.value = []; selectedLinks.value = []; changeOpen.value = false; }
watch(open, (v) => { if (v) { void load(); } else { seq++; tablesSeq++; resetPreview(); } });
watch(ctxKey, () => { if (open.value) { open.value = false; message.info('身份或版本已切换，请重新打开导入'); } });
async function load() {
  const my = ++seq, key = ctxKey(); loading.value = true; busy.value = false; error.value = '';
  sources.value = []; tables.value = []; owners.value = []; datasource.value = undefined; schema.value = undefined; selected.value = []; resetPreview();
  if (!isDataReal()) { error.value = '数据源导入需要启用真实数据接口'; loading.value = false; return; }
  try {
    const [rows, choices] = await Promise.all([listDatasources(), definitionChoices('object')]);
    if (my !== seq || key !== ctxKey() || !open.value) return;
    sources.value = rows; owners.value = choices.owners.map((u) => ({ value: u.id, label: u.name }));
  } catch (e) { if (my === seq && key === ctxKey()) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (my === seq && key === ctxKey()) loading.value = false; }
}
watch(datasource, async (id) => {
  const my = ++tablesSeq, key = ctxKey(); tables.value = []; schema.value = undefined; selected.value = []; resetPreview();
  if (!id || !open.value) return;
  loading.value = true; error.value = '';
  try { const result = await listDsTables(id); if (my === tablesSeq && key === ctxKey() && open.value) tables.value = result; }
  catch (e) { if (my === tablesSeq && key === ctxKey()) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (my === tablesSeq && key === ctxKey()) loading.value = false; }
});
watch(schema, () => { selected.value = []; resetPreview(); });
watch(selected, resetPreview);
const schemaOptions = computed(() => [...new Set(tables.value.map((t) => t.schema))].map((value) => ({ value })));
const tableOptions = computed(() => tables.value.filter((t) => t.schema === schema.value).map((t) => ({ value: t.name, label: `${t.name}${t.comment ? ` · ${t.comment}` : ''}` })));
const canPreview = computed(() => isDataReal() && !readonly.value && !loading.value && !busy.value && !!datasource.value && !!schema.value && !!selected.value.length && selected.value.every((name) => tableOptions.value.some((t) => t.value === name)));
async function buildPreview() {
  if (!canPreview.value || (dirty.value && !await confirmDiscard())) return;
  if (!canPreview.value || !open.value) return;
  const my = ++previewSeq, key = ctxKey(); busy.value = true; error.value = ''; preview.value = undefined;
  try {
    const result = await previewObjectImport(datasource.value!, schema.value!, [...selected.value]);
    if (my !== previewSeq || key !== ctxKey() || !open.value) return;
    preview.value = result; objects.value = result.proposal.objectTypes.map((o) => ({ ...o, id: o.api })); selectedLinks.value = []; initialSnapshot.value = formSignature();
  } catch (e) { if (my === previewSeq && key === ctxKey()) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (my === previewSeq && key === ctxKey()) busy.value = false; }
}
const validation = computed(() => {
  if (!preview.value) return '请先选择库表并生成真实预览';
  if (objects.value.some((o) => preview.value!.tables.find((t) => `${preview.value!.schema}.${t.name}` === o.table || t.name === o.table)?.pk.length !== 1)) return '所选对象存在无主键或复合主键表，需要人工建模，不能直接导入';
  if (!objects.value.length) return '当前选择未产生对象类型；请调整库表选择';
  if (objects.value.some((o) => !o.name.trim() || !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(o.api) || !o.owner || !owners.value.some((u) => u.value === o.owner))) return '请检查对象名称、API名并选择真实负责人';
  if (new Set(objects.value.map((o) => o.api)).size !== objects.value.length) return '对象API名不能重复';
  return '';
});
const canCommit = computed(() => isDataReal() && !readonly.value && !loading.value && !busy.value && !validation.value);
async function commit(opts: { join?: string; title?: string; desc?: string }) {
  if (!canCommit.value || !preview.value) return;
  const my = previewSeq, key = ctxKey(); busy.value = true; error.value = '';
  try {
    const rows = objects.value.map((o) => ({ ...o, id: o.api }));
    const result = await commitObjectImport(preview.value, [...selected.value], rows, !!selectedLinks.value.length, opts, [...selectedLinks.value]);
    if (my !== previewSeq || key !== ctxKey() || !open.value) return;
    initialSnapshot.value = formSignature();
    message.success(`已保存到变更单 ${result.crId}，审核发布后生效`);
    emit('saved', { id: result.targets.find((t) => t.kind === 'object')?.id ?? rows[0].id, crId: result.crId }); open.value = false;
  } catch (e) { if (my === previewSeq && key === ctxKey()) error.value = `${e instanceof Error ? e.message : String(e)}；输入已保留，结构变化时请重新生成预览。`; }
  finally { if (my === previewSeq && key === ctxKey()) busy.value = false; }
}
</script>
<template>
  <a-drawer :open="open" title="从数据源导入对象定义" :width="1040" :closable="!busy" :mask-closable="!busy" @close="close">
    <a-alert type="info" message="只从真实只读内省生成定义草稿；不会自动授予任意库表查询权限。发布前现有定义不变。" show-icon />
    <a-alert v-if="readonly" type="warning" :message="reason" show-icon />
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-spin :spinning="loading">
      <a-form layout="vertical" :disabled="readonly || busy || loading" class="import-form">
        <div class="import-grid"><a-form-item label="数据连接" required><a-select v-model:value="datasource" :disabled="!!preview" :options="sources.map((s) => ({ value: s.id, label: s.name }))" placeholder="选择真实数据库连接" /></a-form-item><a-form-item label="Schema" required><a-select v-model:value="schema" :disabled="!!preview" :options="schemaOptions" /></a-form-item></div>
        <a-form-item label="库表（同一Schema）" required><a-select v-model:value="selected" :disabled="!!preview" mode="multiple" :options="tableOptions" /></a-form-item>
        <a-button :disabled="!canPreview" :loading="busy" @click="buildPreview">{{ preview ? '重新生成规则预览' : '生成规则映射预览' }}</a-button>
        <a-button v-if="preview" style="margin-left: 12px" @click="guard(resetPreview)">重新选择库表</a-button>
        <template v-if="preview">
          <div class="t-14 c-3 import-note">生成方式：{{ preview.generator }} · {{ preview.schema }} · {{ preview.tables.length }} 张真实表。以下对象全部加入当前导入；中间表按规则转换关系。</div>
          <a-alert v-for="warning in preview.warnings" :key="warning" type="warning" :message="warning" show-icon />
          <div v-for="(object, index) in objects" :key="object.table" class="import-object">
            <div class="t-16 fw-600">{{ index+1 }}. {{ object.table }}</div>
            <div class="import-grid"><a-form-item label="名称"><a-input v-model:value="object.name" :maxlength="64" /></a-form-item><a-form-item label="API名 / 新业务编号"><a-input v-model:value="object.api" :maxlength="64" /></a-form-item><a-form-item label="负责人"><a-select v-model:value="object.owner" :options="owners" /></a-form-item><a-form-item label="主键"><span class="t-14">{{ object.pk || '未发现，需在草稿补齐' }}</span></a-form-item></div>
            <a-table :columns="[{title:'属性',dataIndex:'name'},{title:'API名',dataIndex:'api'},{title:'类型',dataIndex:'type'},{title:'源列',dataIndex:'source'}]" :data-source="object.props" size="small" :pagination="{pageSize:8,hideOnSinglePage:true}" row-key="api" />
          </div>
          <div class="import-note t-14">外键关系建议（默认不选；逐条勾选确认，共 {{ preview.proposal.linkTypes.length }} 条）</div>
          <a-table v-if="preview.proposal.linkTypes.length" :columns="[{title:'关系',dataIndex:'name'},{title:'源对象',dataIndex:'from'},{title:'目标对象',dataIndex:'to'}]" :data-source="preview.proposal.linkTypes" row-key="id" size="small" :row-selection="{ selectedRowKeys: selectedLinks, onChange: (keys: (string | number)[]) => selectedLinks = keys.map(String) }" :pagination="{pageSize:8,hideOnSinglePage:true}" />
          <p class="t-12 c-3">属性与关系只采用确认时重新内省的结构；结构已变化返回409，需重新预览。保存后在对象草稿中继续编辑。</p>
        </template>
      </a-form>
    </a-spin>
    <template #footer><div class="import-footer"><span class="t-12 c-3">{{ validation || '预览确认后保存到CR，不立即发布' }}</span><a-button :disabled="busy" @click="close">取消</a-button><a-button type="primary" :loading="busy" :disabled="!canCommit" @click="changeOpen = true">保存到变更草稿</a-button></div></template>
  </a-drawer>
  <StartChangeModal v-model:open="changeOpen" target-name="导入源表对象定义" @ok="commit" />
</template>
<style scoped>
.import-form { margin-top: 16px; }
.import-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
.import-object { border: 1px solid var(--g5); border-radius: 4px; padding: 16px; margin-top: 16px; }
.import-object > .import-grid { margin-top: 12px; }
.import-note { margin: 16px 0; }
.import-footer { display: flex; align-items: center; gap: 12px; }
.import-footer > span { flex: 1; }
</style>
