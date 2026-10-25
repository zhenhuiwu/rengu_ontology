<script setup lang="ts">
/**
 * 用户与组织（示例：full 分页，120 个 mock 用户）。一屏 21 行，页码/条数在 URL ?page=&size=。
 * 停用：确认卡说明会话保留为只读、待办审批需转交，并选择转交人（ConfirmActionModal 的 picker）。
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

const dept = ref('全部');
const status = ref('全部');
const kw = ref('');
const pq = usePagedQuery({ defaultSize: 21, filters: [dept, status, kw] });
const users = ref<(AdminUser & { pending: number })[]>(ADMIN_USERS.map((u) => ({ ...u, pending: pendingApprovals(u) })));
const rows = computed(() => users.value.filter((u) =>
  (dept.value === '全部' || u.dept === dept.value) &&
  (status.value === '全部' || u.status === status.value) &&
  (!kw.value || u.name.includes(kw.value) || u.account.includes(kw.value) || u.id.includes(kw.value))));
const paged = computed(() => paginate(rows.value, { page: pq.page.value, size: pq.size.value }));
const badge = (_s: AdminUser['status']) => 'default';
// 每列都给宽度（账号按邮箱长度），剩余宽度按比例分摊，不在某一列后面留大空白
const columns = [
  { title: '用户 ID', dataIndex: 'id', key: 'id', width: 100 },
  { title: '姓名', dataIndex: 'name', key: 'name', width: 90 },
  { title: '账号', dataIndex: 'account', key: 'account', width: 230, ellipsis: true },
  { title: '部门', dataIndex: 'dept', key: 'dept', width: 230, ellipsis: true },
  { title: '角色', dataIndex: 'role', key: 'role', width: 130 },
  { title: '状态', key: 'status', width: 90 },
  { title: '待办审批', key: 'pending', width: 90, align: 'right' as const },
  { title: '最近登录', key: 'lastLogin', width: 170 },
  { title: '创建日期', dataIndex: 'createdAt', key: 'createdAt', width: 110 },
  { title: '操作', key: 'op', width: 110 },
];

const confirm = useConfirmStore();
const { readonly, reason } = useReadonly();
const route = useRoute();
const router = useRouter();
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });

// 新建 / 编辑：页面内抽屉 ?user=<id|new>（全局 ?drawer= 留给对象抽屉，user 不是注册对象）
type Form = { name: string; account: string; dept: string; role: string; status: AdminUser['status'] };
const ROLE_OPTS = ROLES.map((r) => ({ value: r.name, label: r.name }));
const DEPT_OPTS = ADMIN_DEPTS.map((v) => ({ value: v, label: v }));
const STATUS_OPTS = (['启用', '停用', '待激活'] as const).map((v) => ({ value: v, label: v }));
const userKey = computed(() => (typeof route.query.user === 'string' ? route.query.user : ''));
const editing = computed(() => (userKey.value && userKey.value !== 'new' ? users.value.find((u) => u.id === userKey.value) ?? null : null));
const drawerOpen = computed(() => !readonly.value && (userKey.value === 'new' || !!editing.value));
const blank = (): Form => ({ name: '', account: '', dept: ADMIN_DEPTS[0], role: ROLE_OPTS[0]?.value ?? '', status: '待激活' });
const form = reactive<Form>(blank());
const base = ref('');
const snap = () => JSON.stringify(form);
watch([userKey, editing], () => {
  const u = editing.value;
  Object.assign(form, u ? { name: u.name, account: u.account, dept: u.dept, role: u.role, status: u.status } : blank());
  base.value = snap();
}, { immediate: true });
const dirty = computed(() => drawerOpen.value && snap() !== base.value);
useUnsavedGuard(dirty);
const formOk = computed(() => !!form.name.trim() && /^\S+@\S+$/.test(form.account.trim()) && !!form.dept && !!form.role);
async function closeDrawer() {
  if (dirty.value && !(await confirmDiscard())) return;
  setQuery({ user: undefined });
}
async function saveUser() {
  const u = editing.value;
  const ok = await confirm.ask({
    title: u ? '保存用户修改' : '新建用户', target: `${form.name}（${form.account}）`, okText: u ? '保存' : '新建',
    fields: [{ label: '部门', value: form.dept }, { label: '角色', value: form.role }, { label: '状态', value: form.status }],
    impact: u ? '角色变更在用户下次请求时生效。' : '新用户状态为「待激活」时，会收到激活邮件，激活后才能登录。',
  });
  if (!ok) return;
  if (u) Object.assign(u, { ...form, name: form.name.trim(), account: form.account.trim() });
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
const preview = ref<Form[]>([]);
const SAMPLE: Form[] = [
  { name: '陈思远', account: 'chen.siyuan@freshmart.cn', dept: ADMIN_DEPTS[0], role: ROLE_OPTS[0]?.value ?? '', status: '待激活' },
  { name: '林晓', account: 'lin.xiao@freshmart.cn', dept: ADMIN_DEPTS[1] ?? ADMIN_DEPTS[0], role: ROLE_OPTS[1]?.value ?? '', status: '待激活' },
  { name: '周航', account: 'zhou.hang@freshmart.cn', dept: ADMIN_DEPTS[2] ?? ADMIN_DEPTS[0], role: ROLE_OPTS[0]?.value ?? '', status: '待激活' },
];
const previewCols = [
  { title: '姓名', dataIndex: 'name', key: 'name', width: 90 },
  { title: '账号', dataIndex: 'account', key: 'account', ellipsis: true },
  { title: '部门', dataIndex: 'dept', key: 'dept', width: 160, ellipsis: true },
  { title: '角色', dataIndex: 'role', key: 'role', width: 120, ellipsis: true },
];
function openImport() { importFile.value = ''; preview.value = []; importOpen.value = true; }
function onUpload(file: File) { importFile.value = file.name; preview.value = SAMPLE.map((x) => ({ ...x })); return false; }
function downloadTpl() {
  const csv = '姓名,账号,部门,角色\n张三,zhang.san@freshmart.cn,华东事业部,门店店长\n';
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['\ufeff' + csv], { type: 'text/csv' }));
  a.download = '用户导入模板.csv'; a.click(); URL.revokeObjectURL(a.href);
}
async function doImport() {
  const n = preview.value.length;
  const ok = await confirm.ask({ title: '导入用户', target: `${importFile.value} · ${n} 人`, okText: '导入', impact: `将新建 ${n} 个「待激活」用户并发送激活邮件；账号已存在的行会跳过。` });
  if (!ok) return;
  preview.value.forEach((f, i) => users.value.unshift({ id: `U${1000 + users.value.length + i + 1}`, ...f, lastLogin: null, createdAt: '2026-10-25', pending: 0 } as AdminUser & { pending: number }));
  importOpen.value = false;
  message.success(`已导入 ${n} 个用户`);
}
async function toggle(u: AdminUser & { pending: number }) {
  if (readonly.value) return;
  if (u.status === '停用') {
    const ok = await confirm.ask({ title: '启用用户', target: `${u.name}（${u.account}）`, okText: '启用', impact: '恢复登录和原有角色权限。' });
    if (ok) { u.status = '启用'; message.success(`已启用：${u.name}`); }
    return;
  }
  const candidates = users.value
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
  const to = users.value.find((x) => x.id === confirm.pickValue);
  if (to) { to.pending += u.pending; u.pending = 0; }
  u.status = '停用';
  message.success(to ? `已停用 ${u.name}，待办审批已转交给 ${to.name}` : `已停用 ${u.name}`);
}
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="reason"><a-button :disabled="readonly" @click="openImport">批量导入</a-button></a-tooltip>
    <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly" @click="setQuery({ user: 'new' })">新建用户</a-button></a-tooltip>
  </Teleport>
  <div class="panel au">
    <div class="panel-head">
      <span class="t-16 fw-600">用户</span>
      <a-space>
        <a-select v-model:value="dept" size="small" class="au-w240" :options="['全部', ...ADMIN_DEPTS].map((v) => ({ value: v, label: v === '全部' ? '全部部门' : v }))" />
        <a-select v-model:value="status" size="small" class="au-w120" :options="['全部', '启用', '停用', '待激活'].map((v) => ({ value: v, label: v === '全部' ? '全部状态' : v }))" />
        <a-input-search v-model:value="kw" size="small" placeholder="姓名 / 账号 / ID" class="au-w200" />
      </a-space>
    </div>
    <div class="au-table paged-scroll">
      <a-table size="small" row-key="id" :columns="columns" :data-source="paged.items" :pagination="false">
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
    <a-form layout="vertical">
      <a-form-item label="姓名" required><a-input v-model:value="form.name" :maxlength="20" placeholder="真实姓名" /></a-form-item>
      <a-form-item label="账号" required extra="公司邮箱，用于登录和接收激活邮件"><a-input v-model:value="form.account" :disabled="!!editing" placeholder="name@freshmart.cn" /></a-form-item>
      <a-form-item label="部门" required><a-select v-model:value="form.dept" :options="DEPT_OPTS" /></a-form-item>
      <a-form-item label="角色" required><a-select v-model:value="form.role" :options="ROLE_OPTS" /></a-form-item>
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
      <a-upload-dragger :before-upload="onUpload" :show-upload-list="false" accept=".csv,.xlsx" :max-count="1">
        <div class="t-14 c-1">第 2 步：点击或拖入填好的文件</div>
        <div class="t-12 c-3">{{ importFile || '支持 .csv / .xlsx，一次最多 500 人' }}</div>
      </a-upload-dragger>
      <template v-if="preview.length">
        <div class="t-14 c-2">第 3 步：预览（{{ preview.length }} 人）</div>
        <a-table size="small" row-key="account" :columns="previewCols" :data-source="preview" :pagination="false" />
      </template>
      <div class="au-foot">
        <a-button @click="importOpen = false">取消</a-button>
        <a-button type="primary" :disabled="!preview.length" @click="doImport">导入</a-button>
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
