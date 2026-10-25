<script setup lang="ts">
/**
 * 用户与组织（示例：full 分页，120 个 mock 用户）。一屏 21 行，页码/条数在 URL ?page=&size=。
 * 停用：确认卡说明会话保留为只读、待办审批需转交，并选择转交人（ConfirmActionModal 的 picker）。
 */
import { computed, ref } from 'vue';
import { message } from 'ant-design-vue';
import { useConfirmStore } from '@/stores/confirm';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { ADMIN_USERS, ADMIN_DEPTS, paginate, EMPTY, type AdminUser } from '@/mock';
import { pendingApprovals } from '@/mock/admin';

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
const badge = (s: AdminUser['status']) => (s === '启用' ? 'default' : s === '停用' ? 'default' : 'warning');
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
async function toggle(u: AdminUser & { pending: number }) {
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
    <a-button>批量导入</a-button>
    <a-button type="primary">新建用户</a-button>
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
            <a>编辑</a>
            <a v-if="record.status !== '待激活'" @click="toggle(record)">{{ record.status === '停用' ? '启用' : '停用' }}</a>
          </a-space>
        </template>
      </a-table>
    </div>
    <AppPagination
      v-model:page="pq.page.value" v-model:size="pq.size.value" class="au-pager"
      variant="full" :total="paged.total" :default-size="pq.defaultSize" :empty="false"
    />
  </div>
</template>

<style scoped>
.au { flex: 1; min-height: 0; }
.au-table { flex: 1; min-height: 0; padding: 0 16px; }
.au-pager { padding: 4px 16px 8px; }
.au-w120 { width: 120px; }
.au-w200 { width: 200px; }
.au-w240 { width: 240px; }
</style>
