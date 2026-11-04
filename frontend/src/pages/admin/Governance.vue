<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { currentApiContext, isApiError } from '@/api/http';
import { loadPolicy, savePolicy, policyEffective, OPERATIONS, OP_NAMES, type Operation, type PolicyDocument, type PolicyDefinition, type ResourcePolicy, type Downgrade, type Vocabulary } from '@/api/governance';
import { useReadonly } from '@/composables/useReadonly';
import { useUnsavedGuard } from '@/composables/useUnsavedGuard';

const { readonly, reason } = useReadonly();
const document = ref<PolicyDocument>();
const draft = ref<PolicyDefinition>({ classes: [], levels: [], purposes: [], grants: [], resources: [], downgrades: [] });
const baseline = ref(''), loading = ref(false), saving = ref(false), error = ref('');
const tab = ref('vocabulary'), selectedResource = ref<string>(), effectiveSearch = ref('');
const operationOptions = OPERATIONS.map(value => ({ value, label: OP_NAMES[value] }));
const checkRole = ref<string>(), checkPurpose = ref<string>(), checkOperation = ref<Operation>('read');
const decisions = ref<Record<string, { allowed: boolean; mask: string; reason?: string }>>({}), checking = ref(false), checkError = ref('');
let checkSeq = 0;
watch([checkRole, checkPurpose, checkOperation, () => document.value?.revision, () => JSON.stringify(currentApiContext())], async () => {
  const seq = ++checkSeq;
  decisions.value = {}; checkError.value = ''; checking.value = false;
  if (!checkRole.value || !checkPurpose.value || !document.value) return;
  checking.value = true;
  try { const result = await policyEffective(checkRole.value, checkPurpose.value, checkOperation.value); if (seq === checkSeq) decisions.value = result; }
  catch (e) { if (seq === checkSeq) checkError.value = isApiError(e) ? e.message : String(e); }
  finally { if (seq === checkSeq) checking.value = false; }
});
const dirty = computed(() => JSON.stringify(draft.value) !== baseline.value);
useUnsavedGuard(dirty);
const locked = computed(() => readonly.value || loading.value || saving.value || !document.value);
const options = (rows: Vocabulary[]) => rows.map(r => ({ value: r.code, label: r.name || r.code }));
const resources = computed(() => (document.value?.resources ?? []).map(n => ({ value: n.id, label: `${n.name} · ${n.kind} · ${n.code}` })));
const resourceName = (id: string) => resources.value.find(n => n.value === id)?.label ?? id;
const current = computed(() => draft.value.resources.find(r => r.resource === selectedResource.value));
const effectiveRows = computed(() => Object.entries(document.value?.effective ?? {}).filter(([id, state]) => state.protected && resourceName(id).toLowerCase().includes(effectiveSearch.value.toLowerCase())).map(([id, state]) => ({ id, ...state })));
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
let sequence = 0;
async function reload() {
  const seq = ++sequence, context = JSON.stringify(currentApiContext());
  loading.value = true; error.value = ''; document.value = undefined;
  try {
    const result = await loadPolicy();
    if (seq !== sequence || context !== JSON.stringify(currentApiContext())) return;
    document.value = result; draft.value = clone(result.definition); baseline.value = JSON.stringify(draft.value);
    selectedResource.value = draft.value.resources[0]?.resource;
  } catch (e) { if (seq === sequence) error.value = isApiError(e) ? e.message : String(e); }
  finally { if (seq === sequence) loading.value = false; }
}
watch(() => JSON.stringify(currentApiContext()), () => { void reload(); }, { immediate: true });
async function save() {
  if (locked.value || !document.value) return;
  saving.value = true; error.value = '';
  try {
    await savePolicy(document.value.revision, clone(draft.value));
    window.dispatchEvent(new Event('governance-policy-updated'));
    baseline.value = JSON.stringify(draft.value);
    await reload(); message.success('治理策略已保存');
  } catch (e) { error.value = isApiError(e) ? e.message : String(e); }
  finally { saving.value = false; }
}
function addResource() {
  const first = resources.value.find(n => !draft.value.resources.some(r => r.resource === n.value));
  if (!first) return;
  const row: ResourcePolicy = { resource: first.value, classes: [], level: null, purposes: [], mask: 'none', dependencies: [], dependencies_complete: false, downgrade: null, reason: '' };
  draft.value.resources.push(row); selectedResource.value = row.resource;
}
function removeResource() {
  draft.value.resources = draft.value.resources.filter(r => r.resource !== selectedResource.value);
  selectedResource.value = draft.value.resources[0]?.resource;
}
const downgradeLabel = (r: Downgrade) => `${r.from_level ?? '未分级'} → ${r.to_level ?? '未分级'}${r.remove_classes.length ? ` · 移除 ${r.remove_classes.join('、')}` : ''}`;
const downgradeValue = computed({
  get: () => current.value?.downgrade ? JSON.stringify(current.value.downgrade) : undefined,
  set: (v: string | undefined) => { if (current.value) current.value.downgrade = v ? JSON.parse(v) : null; },
});
const vocabularySections = [{ key: 'classes', title: '分类集合' }, { key: 'levels', title: '等级顺序 · 从低到高' }, { key: 'purposes', title: '数据用途' }] as const;
</script>

<template>
  <section class="gov">
    <div class="gov-tools"><span class="c-3">{{ document ? `策略版本 ${document.revision}` : '' }}</span><a-space><a-button :loading="loading" :disabled="saving || dirty" @click="reload">刷新</a-button><a-button type="primary" :disabled="locked || !dirty" :loading="saving" @click="save">保存策略</a-button></a-space></div>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-alert v-if="readonly" type="warning" :message="reason" show-icon />
    <a-spin :spinning="loading">
      <a-tabs v-model:active-key="tab">
        <a-tab-pane key="vocabulary" tab="分类、等级与用途">
          <div class="gov-vocab">
            <section v-for="section in vocabularySections" :key="section.key" class="gov-card">
              <div class="gov-section"><h3>{{ section.title }}</h3><a-button size="small" :disabled="locked" @click="draft[section.key].push({ code: '', name: '' })">添加</a-button></div>
              <div v-for="(item, index) in draft[section.key]" :key="index" class="gov-vocab-row">
                <span v-if="section.key === 'levels'" class="c-3">{{ index + 1 }}</span>
                <a-input v-model:value="item.code" :disabled="locked" placeholder="稳定编码" aria-label="稳定编码" />
                <a-input v-model:value="item.name" :disabled="locked" placeholder="显示名称" aria-label="显示名称" />
                <a-button type="text" danger :disabled="locked" @click="draft[section.key].splice(index, 1)">移除</a-button>
              </div>
              <a-empty v-if="!draft[section.key].length" description="尚未声明" />
            </section>
          </div>
        </a-tab-pane>
        <a-tab-pane key="grants" tab="角色用途授权">
          <div class="gov-section"><span class="c-3">用途授权与原有页面、对象和行级权限共同生效</span><a-space><a-button :disabled="locked" @click="loadPolicy().then(fresh => { if (document) document.roles = fresh.roles; }).catch(e => { error = isApiError(e) ? e.message : String(e); })">更新角色</a-button><a-button :disabled="locked" @click="draft.grants.push({ role: '', purposes: [], classes: [], level: null, operations: [], unmask: false })">添加授权</a-button></a-space></div>
          <div v-for="(grant, index) in draft.grants" :key="index" class="gov-card gov-grant">
            <a-form layout="vertical" class="gov-fields">
              <a-form-item label="角色"><a-select v-model:value="grant.role" :disabled="locked" :options="options(document?.roles ?? [])" /></a-form-item>
              <a-form-item label="允许用途"><a-select v-model:value="grant.purposes" mode="multiple" :disabled="locked" :options="options(draft.purposes)" /></a-form-item>
              <a-form-item label="允许分类集合"><a-select v-model:value="grant.classes" mode="multiple" :disabled="locked" :options="options(draft.classes)" /></a-form-item>
              <a-form-item label="最高等级"><a-select v-model:value="grant.level" allow-clear :disabled="locked" :options="options(draft.levels)" @clear="grant.level = null" /></a-form-item>
              <a-form-item label="操作"><a-select v-model:value="grant.operations" mode="multiple" :disabled="locked" :options="operationOptions" /></a-form-item>
              <a-form-item label="明文权限"><a-checkbox v-model:checked="grant.unmask" :disabled="locked">允许明文、筛选、聚合及外发</a-checkbox></a-form-item>
            </a-form>
            <a-button type="text" danger :disabled="locked" @click="draft.grants.splice(index, 1)">移除授权</a-button>
          </div>
          <a-empty v-if="!draft.grants.length" description="尚未授予用途权限" />
        </a-tab-pane>
        <a-tab-pane key="resources" tab="资源策略">
          <div class="gov-resource">
            <aside class="gov-card gov-list"><a-button block :disabled="locked" @click="addResource">添加资源策略</a-button><button v-for="row in draft.resources" :key="row.resource" class="gov-resource-button" :class="{ selected: row.resource === selectedResource }" @click="selectedResource = row.resource">{{ resourceName(row.resource) }}</button></aside>
            <a-form v-if="current" layout="vertical" class="gov-card gov-resource-form">
              <a-form-item label="已发布资源"><a-select :value="current.resource" show-search :filter-option="(input: string, option: { label: string }) => option.label.toLowerCase().includes(input.toLowerCase())" :disabled="locked" :options="resources.filter(r => r.value === current?.resource || !draft.resources.some(p => p.resource === r.value))" @change="(v: string) => { if (current) { current.resource = v; selectedResource = v; } }" /></a-form-item>
              <div class="gov-fields">
                <a-form-item label="声明分类"><a-select v-model:value="current.classes" mode="multiple" :disabled="locked" :options="options(draft.classes)" /></a-form-item>
                <a-form-item label="声明等级"><a-select v-model:value="current.level" allow-clear :disabled="locked" :options="options(draft.levels)" @clear="current.level = null" /></a-form-item>
                <a-form-item label="允许用途"><a-select v-model:value="current.purposes" mode="multiple" :disabled="locked" :options="options(draft.purposes)" /></a-form-item>
                <a-form-item label="无明文授权时的返回值"><a-select v-model:value="current.mask" :disabled="locked" :options="[{ value: 'none', label: '原值' }, { value: 'null', label: '空值' }, { value: 'redact', label: '文字脱敏，其他类型为空值' }]" /></a-form-item>
              </div>
              <a-form-item label="补充已确认依赖"><a-select v-model:value="current.dependencies" mode="multiple" show-search :disabled="locked" :options="resources.filter(r => r.value !== current?.resource)" :filter-option="(input: string, option: { label: string }) => option.label.toLowerCase().includes(input.toLowerCase())" /></a-form-item>
              <a-form-item><a-checkbox v-model:checked="current.dependencies_complete" :disabled="locked">确认已补全本资源未解析依赖</a-checkbox></a-form-item>
              <a-form-item label="允许的降级规则"><a-select v-model:value="downgradeValue" allow-clear :disabled="locked" :options="draft.downgrades.map(r => ({ value: JSON.stringify(r), label: downgradeLabel(r) }))" /></a-form-item>
              <a-form-item label="依赖确认或降级依据"><a-textarea v-model:value="current.reason" :disabled="locked" :rows="3" :maxlength="512" /></a-form-item>
              <a-button danger :disabled="locked" @click="removeResource">移除本资源策略</a-button>
            </a-form>
            <a-empty v-else description="选择或添加资源策略" />
          </div>
        </a-tab-pane>
        <a-tab-pane key="downgrades" tab="允许的降级">
          <div class="gov-section"><span class="c-3">资源须单独选用规则并填写依据</span><a-button :disabled="locked" @click="draft.downgrades.push({ from_level: null, to_level: null, remove_classes: [] })">添加规则</a-button></div>
          <div v-for="(rule, index) in draft.downgrades" :key="index" class="gov-card gov-fields">
            <a-form-item label="原等级"><a-select v-model:value="rule.from_level" allow-clear :disabled="locked" :options="options(draft.levels)" @clear="rule.from_level = null" /></a-form-item>
            <a-form-item label="允许降至"><a-select v-model:value="rule.to_level" allow-clear :disabled="locked" :options="options(draft.levels)" @clear="rule.to_level = null" /></a-form-item>
            <a-form-item label="允许移除分类"><a-select v-model:value="rule.remove_classes" mode="multiple" :disabled="locked" :options="options(draft.classes)" /></a-form-item>
            <a-button type="text" danger :disabled="locked" @click="draft.downgrades.splice(index, 1)">移除</a-button>
          </div>
          <a-empty v-if="!draft.downgrades.length" description="未允许任何降级" />
        </a-tab-pane>
        <a-tab-pane key="effective" tab="生效结果与传播依据">
          <a-space wrap style="margin-bottom:16px"><a-input-search v-model:value="effectiveSearch" placeholder="搜索资源" style="width:260px" /><a-select v-model:value="checkRole" :options="options(document?.roles ?? [])" placeholder="核对角色" style="width:180px" /><a-select v-model:value="checkPurpose" :options="options(document?.definition.purposes ?? [])" placeholder="核对用途" style="width:180px" /><a-select v-model:value="checkOperation" :options="operationOptions" style="width:150px" /><span v-if="dirty" class="c-3">核对结果按已保存策略计算</span></a-space>
          <a-alert v-if="checkError" type="error" :message="checkError" show-icon />
          <a-table :loading="checking" :data-source="effectiveRows" row-key="id" :pagination="{ pageSize: 15, showSizeChanger: false }" :columns="[{ title: '资源', key: 'resource' }, { title: '分类', dataIndex: 'classes', key: 'classes' }, { title: '等级', dataIndex: 'level' }, { title: '允许用途', key: 'purposes' }, { title: '状态', key: 'state' }]">
            <template #bodyCell="{ column, record }"><template v-if="column.key === 'resource'">{{ resourceName(record.id) }}</template><template v-else-if="column.key === 'classes'">{{ record.classes.join('、') || '—' }}</template><template v-else-if="column.key === 'purposes'">{{ record.purposes.join('、') || '无' }}</template><template v-else-if="column.key === 'state'"><a-tag :color="record.unresolved ? 'red' : 'green'">{{ record.unresolved ? '依赖未确认，拒绝访问' : '按角色用途校验' }}</a-tag></template></template>
            <template #expandedRowRender="{ record }"><div class="gov-evidence"><div v-if="decisions[record.id]"><a-tag :color="decisions[record.id].allowed ? 'green' : 'red'">{{ decisions[record.id].allowed ? '用途策略允许' : '用途策略拒绝' }}</a-tag>{{ decisions[record.id].reason || `返回策略 ${decisions[record.id].mask}` }}<span class="c-3"> · 页面、对象及行级权限仍独立校验</span></div><div>属性返回策略 {{ record.mask }}</div><div>{{ record.declared ? '含本资源显式声明' : '由上游传播' }}</div><div v-for="id in record.dependencies" :key="id">← {{ resourceName(id) }}</div><div v-if="!record.dependencies.length">无上游依赖</div></div></template>
          </a-table>
        </a-tab-pane>
      </a-tabs>
    </a-spin>
  </section>
</template>

<style scoped>
.gov { display:flex; flex-direction:column; gap:16px; min-width:0; }
.gov-tools,.gov-section { display:flex; align-items:center; justify-content:space-between; gap:16px; }
.gov-section { margin-bottom:16px; }.gov-section h3 { margin:0; font-size:15px; }
.gov-vocab { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; }
.gov-card { border:1px solid var(--border); border-radius:8px; padding:20px; background:var(--surface,transparent); margin-bottom:16px; }
.gov-vocab-row { display:flex; gap:8px; align-items:center; margin-bottom:12px; }
.gov-vocab-row .ant-input { min-width:0; }.gov-vocab-row .ant-btn { flex-shrink:0; }
.gov-fields { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:0 20px; }
.gov-fields .ant-select,.gov-resource-form .ant-select { width:100%; }
.gov-resource { display:grid; grid-template-columns:320px minmax(0,1fr); gap:20px; align-items:start; }
.gov-list { max-height:650px; overflow:auto; padding:12px; }.gov-resource-button { width:100%; padding:12px; text-align:left; border:0; border-radius:5px; background:transparent; color:inherit; cursor:pointer; margin-top:8px; overflow-wrap:anywhere; }.gov-resource-button.selected { background:var(--g1); color:var(--g8); }.gov-resource-button:focus-visible { outline:2px solid var(--g8); outline-offset:2px; }
.gov-evidence { display:grid; gap:8px; padding:8px; overflow-wrap:anywhere; }
@media(max-width:1200px) { .gov-vocab,.gov-fields { grid-template-columns:1fr; }.gov-resource { grid-template-columns:260px minmax(0,1fr); } }
</style>
