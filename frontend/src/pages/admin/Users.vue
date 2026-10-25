<script setup lang="ts">
/** 用户与组织（示例：full 分页，120 个 mock 用户）。一屏 21 行，页码/条数在 URL ?page=&size=。 */
import { computed, ref } from 'vue';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { ADMIN_USERS, ADMIN_DEPTS, paginate, EMPTY, type AdminUser } from '@/mock';

const dept = ref('全部');
const status = ref('全部');
const kw = ref('');
const pq = usePagedQuery({ defaultSize: 21, filters: [dept, status, kw] });
const rows = computed(() => ADMIN_USERS.filter((u) =>
  (dept.value === '全部' || u.dept === dept.value) &&
  (status.value === '全部' || u.status === status.value) &&
  (!kw.value || u.name.includes(kw.value) || u.account.includes(kw.value) || u.id.includes(kw.value))));
const paged = computed(() => paginate(rows.value, { page: pq.page.value, size: pq.size.value }));
const badge = (s: AdminUser['status']) => (s === '启用' ? 'success' : s === '停用' ? 'default' : 'warning');
const columns = [
  { title: '用户 ID', dataIndex: 'id', key: 'id', width: 110 },
  { title: '姓名', dataIndex: 'name', key: 'name', width: 110 },
  { title: '账号', dataIndex: 'account', key: 'account', ellipsis: true },
  { title: '部门', dataIndex: 'dept', key: 'dept', width: 240 },
  { title: '角色', dataIndex: 'role', key: 'role', width: 120 },
  { title: '状态', key: 'status', width: 96 },
  { title: '最近登录', key: 'lastLogin', width: 160 },
  { title: '创建日期', dataIndex: 'createdAt', key: 'createdAt', width: 120 },
  { title: '操作', key: 'op', width: 120 },
];
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
          <a-space v-else-if="column.key === 'op'" :size="12"><a>编辑</a><a>{{ record.status === '停用' ? '启用' : '停用' }}</a></a-space>
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
