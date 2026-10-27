<script setup lang="ts">
/**
 * 用户与组织（示例：full 分页，120 个 mock 用户）。一屏 17 行，页码/条数在 URL ?page=&size=。
 * 停用：确认卡说明会话保留为只读、待办审批需转交，并选择转交人（ConfirmActionModal 的 picker）。
 * 真模式读取数据库，加载失败保留空态；mock 仅由显式开关选择。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { useConfirmStore } from '@/stores/confirm';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { ADMIN_USERS, ADMIN_DEPTS, paginate, EMPTY, type AdminUser } from '@/mock';
import { pendingApprovals } from '@/mock/admin';
import { ROLES } from '@/mock/roles';
import { useReadonly } from '@/composables/useReadonly';
import { confirmDiscard, useUnsavedGuard } from '@/composables/useUnsavedGuard';
import { adminApiOn, createUser, disableUser, enableUser, fetchUserOptions, importUsers, previewImport, queryUsers, updateUser, type ImportRow, type UserRow } from '@/api/admin';
import { currentApiContext, isApiError } from '@/api/http';

const dept = ref('全部');
const status = ref('全部');
const kw = ref('');
const realUsers = adminApiOn();
const loadError = ref('');
const optionsError = ref('');
const userReady = ref(!realUsers);
const optionsReady = ref(!realUsers);
const canAssignRole = ref(!realUsers);
const rolePermissionReason = computed(() => realUsers && !canAssignRole.value ? '新建、导入用户或更改主角色需要角色管理权限' : '');
const loading = ref(false);
const pq = usePagedQuery({ defaultSize: 10, filters: [dept, status, kw] });
const users = ref<UserRow[]>(realUsers ? [] : ADMIN_USERS.map((u) => ({ ...u, pending: pendingApprovals(u) })));
const rows = computed(() => users.value.filter((u) =>
  (dept.value === '全部' || u.dept === dept.value) &&
  (status.value === '全部' || u.status === status.value) &&
  (!kw.value || u.name.includes(kw.value) || u.account.includes(kw.value) || u.id.includes(kw.value))));
// 真接口始终是一页数据；仅 mock 模式使用 null。
const remote = ref<{ items: UserRow[]; total: number } | null>(realUsers ? { items: [], total: 0 } : null);
const remoteContext = ref('');
const live = computed(() => realUsers);
const paged = computed(() => realUsers ? remoteContext.value === JSON.stringify(currentApiContext()) ? remote.value! : { items: [], total: 0 } : paginate(rows.value, { page: pq.page.value, size: pq.size.value }));
const pool = computed(() => realUsers ? paged.value.items : users.value);
const errText = (e: unknown, fallback: string) => (isApiError(e) ? e.message || e.code : fallback);
let seq = 0;
async function reload() {
  const n = ++seq;
  const context = JSON.stringify(currentApiContext());
  if (realUsers) { if (remoteContext.value !== context) { remote.value = { items: [], total: 0 }; remoteContext.value = context; } userReady.value = false; }
  loading.value = true;
  loadError.value = '';
  try {
    const r = await queryUsers({ dept: dept.value, status: status.value, kw: kw.value, page: pq.page.value, size: pq.size.value });
    if (n === seq && context === JSON.stringify(currentApiContext())) { remote.value = r; userReady.value = true; }
  } catch (e) {
    if (n === seq && context === JSON.stringify(currentApiContext())) { loadError.value = `用户加载失败（${errText(e, '未知错误')}）`; message.error(loadError.value); }
  } finally { if (n === seq) loading.value = false; }
}
watch([dept, status, kw, pq.page, pq.size, () => JSON.stringify(currentApiContext())], reload, { immediate: true });
const depts = ref<string[]>(realUsers ? [] : ADMIN_DEPTS);
const roleOptions = ref<{ name: string }[]>(realUsers ? [] : ROLES);
let optionsSeq = 0;
async function loadOptions() {
  if (!realUsers) return;
  const n = ++optionsSeq;
  const context = JSON.stringify(currentApiContext());
  optionsReady.value = false; canAssignRole.value = false; optionsError.value = ''; depts.value = []; roleOptions.value = [];
  try {
    const { depts: d, roles: r, canAssignRole: assignable } = await fetchUserOptions();
    if (n !== optionsSeq || context !== JSON.stringify(currentApiContext())) return;
    depts.value = d; roleOptions.value = r; canAssignRole.value = assignable; optionsReady.value = true;
  } catch (e) {
    if (n === optionsSeq && context === JSON.stringify(currentApiContext())) optionsError.value = `用户候选加载失败（${errText(e, '未知错误')}）`;
  }
}
const badge = (_s: AdminUser['status']) => 'default';
// 每列都给宽度（账号按邮箱长度），剩余宽度按比例分摊，不在某一列后面留大空白
const columns = [
  { title: '用户 ID', dataIndex: 'id', key: 'id', width: 100 },
  { title: '姓名', dataIndex: 'name', key: 'name', width: 90 },
  { title: '账号', dataIndex: 'account', key: 'account', width: 230, ellipsis: true },
  { title: '部门', dataIndex: 'dept', key: 'dept', width: 230, ellipsis: true },
  { title: realUsers ? '主角色' : '角色', dataIndex: 'role', key: 'role', width: 130 },
  { title: '状态', key: 'status', width: 90 },
  { title: '待办审批', key: 'pending', width: 90, align: 'right' as const },
  { title: '最近登录', key: 'lastLogin', width: 170 },
  { title: '创建日期', dataIndex: 'createdAt', key: 'createdAt', width: 110 },
  { title: '操作', key: 'op', width: 110 },
];

const confirm = useConfirmStore();
const { readonly: viewReadonly, reason: viewReason } = useReadonly();
const readonly = computed(() => viewReadonly.value || (realUsers && (!userReady.value || !optionsReady.value)));
const reason = computed(() => viewReason.value || (readonly.value ? '请先成功加载用户与候选数据' : ''));
const route = useRoute();
const router = useRouter();
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });

// 新建 / 编辑：页面内抽屉 ?user=<id|new>（全局 ?drawer= 留给对象抽屉，user 不是注册对象）
type Form = { name: string; account: string; dept: string; role: string; status: AdminUser['status'] };
const ROLE_OPTS = computed(() => roleOptions.value.map((r) => ({ value: r.name, label: r.name })));
const DEPT_OPTS = computed(() => depts.value.map((v) => ({ value: v, label: v })));
const STATUS_OPTS = (['启用', '停用', '待激活'] as const).map((v) => ({ value: v, label: v }));
const userKey = computed(() => (typeof route.query.user === 'string' ? route.query.user : ''));
const editing = computed(() => (userKey.value && userKey.value !== 'new' ? pool.value.find((u) => u.id === userKey.value) ?? null : null));
const drawerOpen = computed(() => !viewReadonly.value && (userKey.value === 'new' || !!editing.value));
const blank = (): Form => ({ name: '', account: '', dept: realUsers ? '' : depts.value[0] ?? '', role: realUsers ? '' : ROLE_OPTS.value[0]?.value ?? '', status: '待激活' });
const form = reactive<Form>(blank());
const editVersion = ref<number>();
const base = ref('');
const snap = () => JSON.stringify(form);
watch([userKey, () => editing.value?.id, live], () => {
  const u = editing.value;
  editVersion.value = u?.lockVersion;
  Object.assign(form, u ? { name: u.name, account: u.account ?? '', dept: u.dept ?? '', role: u.role ?? '', status: u.status } : blank());
  base.value = snap();
}, { immediate: true });
const dirty = computed(() => drawerOpen.value && snap() !== base.value);
useUnsavedGuard(dirty);
const formOk = computed(() => !readonly.value && (canAssignRole.value || !!editing.value && form.role === editing.value.role) && !!form.name.trim() && /^\S+@\S+$/.test(form.account.trim()) && !!form.dept.trim() && form.dept.trim().length <= 128 && !/[\u0000-\u001f\u007f]/.test(form.dept.trim()) && ROLE_OPTS.value.some((r) => r.value === form.role));
watch([userKey, () => JSON.stringify(currentApiContext())], loadOptions, { immediate: true });
async function closeDrawer() {
  if (dirty.value && !(await confirmDiscard())) return;
  setQuery({ user: undefined });
}
async function saveUser() {
  if (readonly.value || !formOk.value) return;
  const u = editing.value;
  const ok = await confirm.ask({
    title: u ? '保存用户修改' : '新建用户', target: `${form.name}（${form.account}）`, okText: u ? '保存' : '新建',
    fields: [{ label: '部门', value: form.dept }, { label: '角色', value: form.role }, { label: '状态', value: form.status }],
    impact: u ? '主角色变更在用户下次请求时生效，额外分配的其他角色保持不变。' : live.value ? '新建用户记录，保存后生效。' : '新用户状态为「待激活」时，会收到激活邮件，激活后才能登录。',
  });
  if (!ok || readonly.value || !formOk.value) return;
  if (live.value) {
    const f = { ...form, name: form.name.trim(), account: form.account.trim(), dept: form.dept.trim() };
    try {
      if (u) await updateUser(u.id, { ...f, lockVersion: editVersion.value }); else await createUser(f);
    } catch (e) {
      message.error(isApiError(e) && e.status === 409 && e.code === 'VERSION_CONFLICT' ? '该用户已被修改，当前编辑已保留；请刷新后核对最新数据' : errText(e, '保存失败'));
      return;
    }
    await Promise.all([reload(), loadOptions()]);
  } else if (u) Object.assign(u, { ...form, name: form.name.trim(), account: form.account.trim() });
  else {
    const id = `U${String(1000 + users.value.length + 1)}`;
    users.value.unshift({ id, ...form, name: form.name.trim(), account: form.account.trim(), lastLogin: null, createdAt: '2026-10-25', pending: 0 } as AdminUser & { pending: number });
  }
  base.value = snap();
  setQuery({ user: undefined });
  message.success(u ? `已保存：${form.name}` : `已新建用户：${form.name}`);
}

// 批量导入：下载模板 → 上传 → 预览 → 确认卡
const importOpen = ref(false);
const importFile = ref('');
const preview = ref<(Form & Pick<ImportRow, 'error'>)[]>([]);
const importLoading = ref(false);
const importing = ref(false);
let importSeq = 0;
let previewContext: string | null = null;
const importContext = () => JSON.stringify({ ...currentApiContext(), live: live.value });
function clearImportPreview() {
  importSeq += 1;
  preview.value = [];
  previewContext = null;
  importLoading.value = false;
}
watch([importContext, readonly], clearImportPreview, { flush: 'sync' });
watch(importOpen, (open) => { if (!open) clearImportPreview(); }, { flush: 'sync' });
const previewCols = [
  { title: '姓名', dataIndex: 'name', key: 'name', width: 90 },
  { title: '账号', dataIndex: 'account', key: 'account', ellipsis: true },
  { title: '部门', dataIndex: 'dept', key: 'dept', width: 160, ellipsis: true },
  { title: '角色', dataIndex: 'role', key: 'role', width: 120, ellipsis: true },
  { title: '校验', dataIndex: 'error', key: 'error', width: 150, ellipsis: true },
];
function openImport() {
  if (readonly.value || !canAssignRole.value || importing.value) return;
  importFile.value = ''; clearImportPreview(); importOpen.value = true;
}
function xlsxBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] ?? '');
    reader.onerror = reader.onabort = () => reject(new Error('文件读取失败，请重新选择'));
    reader.readAsDataURL(file);
  });
}
async function onUpload(file: File) {
  if (readonly.value || !canAssignRole.value || importing.value) return false;
  clearImportPreview();
  importFile.value = file.name;
  if (!/\.(csv|xlsx)$/i.test(file.name)) { message.error('仅支持模板 CSV 或无宏 XLSX'); return false; }
  if (!live.value) { message.info('演示模式不解析导入文件，请切换真接口'); return false; }
  const xlsx = /\.xlsx$/i.test(file.name);
  if (file.size > (xlsx ? 2 * 1024 * 1024 : 4_000_000)) { message.error('文件过大：XLSX 最多 2 MiB，CSV 最多 100 万字符'); return false; }
  const n = importSeq;
  const context = importContext();
  const current = () => n === importSeq && context === importContext() && !readonly.value && canAssignRole.value && importOpen.value;
  importLoading.value = true;
  try {
    const source = xlsx ? { xlsxBase64: await xlsxBase64(file) } : { csv: await file.text() };
    if (!current()) return false;
    const rows = await previewImport(source);
    if (!current()) return false;
    preview.value = rows.map((r) => ({ ...r, status: '待激活' as const }));
    previewContext = context;
    if (!rows.length) message.info('文件中没有可导入的数据');
  } catch (e) {
    if (current()) message.error(errText(e, e instanceof Error ? e.message : '预览失败'));
  } finally {
    if (n === importSeq) importLoading.value = false;
  }
  return false;
}
function downloadTpl() {
  const csv = realUsers ? '姓名,账号,部门,角色\n' : `姓名,账号,部门,角色\n张三,zhang.san@freshmart.cn,${ADMIN_DEPTS[0]},门店店长\n`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['\ufeff' + csv], { type: 'text/csv' }));
  a.download = '用户导入模板.csv'; a.click(); URL.revokeObjectURL(a.href);
}
async function doImport() {
  if (readonly.value || !canAssignRole.value || importLoading.value || importing.value || !preview.value.length || !live.value) return;
  const context = importContext();
  if (previewContext !== context) { clearImportPreview(); return; }
  const n = importSeq;
  const selected = preview.value.map((r) => ({ ...r }));
  const current = () => n === importSeq && context === importContext() && !readonly.value && canAssignRole.value && importOpen.value;
  importing.value = true;
  try {
    const ok = await confirm.ask({ title: '导入用户', target: `${importFile.value} · ${selected.length} 行`, okText: '导入', impact: `将新建最多 ${selected.length} 个「待激活」用户；校验错误和已存在账号会跳过。` });
    if (!ok || !current()) return;
    const done = await importUsers(selected);
    if (!current()) return;
    importOpen.value = false;
    await Promise.all([reload(), loadOptions()]);
    if (context === importContext()) message.success(`已导入 ${done} 个用户${done < selected.length ? `（跳过 ${selected.length - done} 行）` : ''}`);
  } catch (e) {
    if (current()) message.error(errText(e, '导入失败'));
  } finally {
    importing.value = false;
  }
}
async function toggle(u: AdminUser & { pending: number }) {
  if (readonly.value) return;
  if (u.status === '停用') {
    const ok = await confirm.ask({ title: '启用用户', target: `${u.name}（${u.account}）`, okText: '启用', impact: '恢复登录和原有角色权限。' });
    if (!ok) return;
    if (live.value) {
      try { await enableUser(u.id); } catch (e) { message.error(errText(e, '启用失败')); return; }
      await reload();
    } else u.status = '启用';
    message.success(`已启用：${u.name}`);
    return;
  }
  if (live.value && u.pending > 0) { message.warning('请先完成待审批事项后再停用此用户'); return; }
  const candidates = pool.value
    .filter((x) => x.id !== u.id && x.status === '启用')
    .sort((a, b) => Number(b.dept === u.dept) - Number(a.dept === u.dept))
    .map((x) => ({ value: x.id, label: `${x.name} · ${x.dept}${x.dept === u.dept ? '（同部门）' : ''}` }));
  const ok = await confirm.ask({
    title: '停用用户',
    target: `${u.name}（${u.account}）`,
    danger: true,
    okText: '停用',
    fields: [
      { label: '部门', value: u.dept },
      { label: '角色', value: u.role },
      { label: '待办审批', value: u.pending ? `${u.pending} 条` : '无' },
    ],
    impact: `停用后不能再登录。已登录的会话保留为只读，直到过期；${u.pending ? `名下 ${u.pending} 条待办审批必须转交给其他人后才能停用。` : '名下没有待办审批。'}`,
    picker: u.pending ? { label: '审批转交给', placeholder: '选择用户（同部门优先）', options: candidates, required: true } : undefined,
  });
  if (!ok) return;
  const to = pool.value.find((x) => x.id === confirm.pickValue);
  if (live.value) {
    try { await disableUser(u.id, u.pending ? confirm.pickValue : undefined); } catch (e) { message.error(errText(e, '停用失败')); return; }
    await reload();
    message.success(to && u.pending ? `已停用 ${u.name}，待办审批已转交给 ${to.name}` : `已停用 ${u.name}`);
    return;
  }
  if (to) { to.pending += u.pending; u.pending = 0; }
  u.status = '停用';
  message.success(to ? `已停用 ${u.name}，待办审批已转交给 ${to.name}` : `已停用 ${u.name}`);
}
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="reason || rolePermissionReason"><a-button :disabled="readonly || !canAssignRole" @click="openImport">批量导入</a-button></a-tooltip>
    <a-tooltip :title="reason || rolePermissionReason"><a-button type="primary" :disabled="readonly || !canAssignRole" @click="setQuery({ user: 'new' })">新建用户</a-button></a-tooltip>
  </Teleport>
  <div class="panel au">
    <div class="panel-head">
      <span class="t-16 fw-600">用户</span>
      <a-space>
        <a-select v-model:value="dept" size="small" class="au-w240" :options="['全部', ...depts].map((v) => ({ value: v, label: v === '全部' ? '全部部门' : v }))" />
        <a-select v-model:value="status" size="small" class="au-w120" :options="['全部', '启用', '停用', '待激活'].map((v) => ({ value: v, label: v === '全部' ? '全部状态' : v }))" />
        <a-input-search v-model:value="kw" size="small" placeholder="姓名 / 账号 / ID" class="au-w200" />
      </a-space>
    </div>
    <a-alert v-if="loadError || optionsError" type="error" show-icon :message="loadError || optionsError">
      <template #action><a-button size="small" @click="reload(); loadOptions()">重试</a-button></template>
    </a-alert>
    <div class="au-table paged-scroll">
      <a-table size="small" row-key="id" :loading="loading" :columns="columns" :data-source="paged.items" :pagination="false">
        <template #bodyCell="{ column, record }">
          <span v-if="column.key === 'id'" class="num">{{ record.id }}</span>
          <a-badge v-else-if="column.key === 'status'" :status="badge(record.status)" :text="record.status" />
          <span v-else-if="column.key === 'lastLogin'" class="num">{{ record.lastLogin ?? EMPTY }}</span>
          <span v-else-if="column.key === 'createdAt'" class="num">{{ record.createdAt }}</span>
          <span v-else-if="column.key === 'pending'" class="num" :class="record.pending ? 'c-1' : 'c-3'">{{ record.pending || EMPTY }}</span>
          <a-space v-else-if="column.key === 'op'" :size="12">
            <a-tooltip :title="reason"><a :class="{ 'au-dis': readonly }" @click="!readonly && setQuery({ user: record.id })">编辑</a></a-tooltip>
            <a-tooltip v-if="record.status !== '待激活'" :title="reason"><a :class="{ 'au-dis': readonly }" @click="toggle(record)">{{ record.status === '停用' ? '启用' : '停用' }}</a></a-tooltip>
          </a-space>
        </template>
      </a-table>
    </div>
    <AppPagination
      v-model:page="pq.page.value" v-model:size="pq.size.value" class="au-pager"
      variant="full" :total="paged.total" :default-size="pq.defaultSize" :empty="false"
    />
  </div>

  <a-drawer :open="drawerOpen" :width="480" :title="editing ? `编辑用户 · ${editing.name}` : '新建用户'" @close="closeDrawer">
    <a-form layout="vertical" :disabled="readonly">
      <a-form-item label="姓名" required><a-input v-model:value="form.name" :maxlength="20" placeholder="真实姓名" /></a-form-item>
      <a-form-item label="账号" required :extra="live ? '公司邮箱' : '公司邮箱，用于登录和接收激活邮件'"><a-input v-model:value="form.account" :disabled="!!editing" :placeholder="realUsers ? 'name@example.com' : 'name@freshmart.cn'" /></a-form-item>
      <a-form-item label="部门" required><a-auto-complete v-if="realUsers" v-model:value="form.dept" :options="DEPT_OPTS" :filter-option="(input: string, option: { value: string }) => option.value.toLowerCase().includes(input.toLowerCase())" placeholder="选择已有部门或输入新部门"><a-input :maxlength="128" /></a-auto-complete><a-select v-else v-model:value="form.dept" :options="DEPT_OPTS" /></a-form-item>
      <a-form-item :label="realUsers ? '主角色' : '角色'" required><a-select :value="form.role || undefined" :disabled="!canAssignRole" :title="rolePermissionReason || undefined" :options="ROLE_OPTS" placeholder="请选择主角色" @change="(value: string) => form.role = value" /></a-form-item>
      <a-form-item label="状态"><a-radio-group v-model:value="form.status" :options="STATUS_OPTS" /></a-form-item>
    </a-form>
    <template #footer>
      <div class="au-foot">
        <a-button @click="closeDrawer">取消</a-button>
        <a-button type="primary" :disabled="!formOk || (!!editing && !dirty)" @click="saveUser">{{ editing ? '保存' : '新建' }}</a-button>
      </div>
    </template>
  </a-drawer>

  <a-modal v-model:open="importOpen" title="批量导入用户" :width="640" :footer="null">
    <div class="au-imp">
      <div class="t-14 c-2">第 1 步：<a @click="downloadTpl">下载导入模板（CSV）</a>，按模板填写姓名、账号、部门、角色</div>
      <a-upload-dragger :before-upload="onUpload" :disabled="readonly || !canAssignRole || importing" :show-upload-list="false" accept=".csv,.xlsx" :max-count="1">
        <div class="t-14 c-1">第 2 步：点击或拖入填好的文件</div>
        <div class="t-12 c-3">{{ importLoading ? '正在解析并校验文件…' : importFile || '支持 .csv / .xlsx，一次最多 500 人；XLSX ≤ 2 MiB' }}</div>
      </a-upload-dragger>
      <template v-if="preview.length">
        <div class="t-14 c-2">第 3 步：预览（{{ preview.length }} 人）</div>
        <a-table size="small" row-key="account" :columns="previewCols" :data-source="preview" :pagination="false" />
      </template>
      <div class="au-foot">
        <a-button @click="importOpen = false">取消</a-button>
        <a-button type="primary" :loading="importing" :disabled="readonly || !canAssignRole || importLoading || !preview.length" @click="doImport">导入</a-button>
      </div>
    </div>
  </a-modal>
</template>

<style scoped>
.au { flex: 1; min-height: 0; }
.au-table { flex: 1; min-height: 0; padding: 0 16px; }
.au-pager { padding: 4px 16px 8px; }
.au-w120 { width: 120px; }
.au-w200 { width: 200px; }
.au-w240 { width: 240px; }
.au-dis { color: var(--g6, #bfbfbf); cursor: not-allowed; }
.au-foot { display: flex; justify-content: flex-end; gap: 8px; }
.au-imp { display: flex; flex-direction: column; gap: 12px; }
</style>
