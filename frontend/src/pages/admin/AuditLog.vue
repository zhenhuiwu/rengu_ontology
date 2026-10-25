<script setup lang="ts">
/** 审计日志（示例：cursor 分页，512 条 mock）。海量流水不显示总数，只有 上一页 / 下一页，游标在 URL ?cursor=。 */
import { computed, ref } from 'vue';
import AppPagination from '@/components/AppPagination.vue';
import { useCursorQuery } from '@/composables/usePagedQuery';
import { AUDIT_LOGS, paginateCursor } from '@/mock';

const action = ref('全部');
const result = ref('全部');
const cq = useCursorQuery({ size: 21, filters: [action, result] });
const rows = computed(() => AUDIT_LOGS.filter((r) =>
  (action.value === '全部' || r.action === action.value) && (result.value === '全部' || r.result === result.value)));
const res = computed(() => paginateCursor(rows.value, { cursor: cq.cursor.value, size: cq.size }));
const ACTIONS = ['全部', ...new Set(AUDIT_LOGS.map((r) => r.action))];
const columns = [
  { title: '时间', dataIndex: 'time', key: 'time', width: 176 },
  { title: '操作人', dataIndex: 'actor', key: 'actor', width: 180 },
  { title: '动作', dataIndex: 'action', key: 'action', width: 140 },
  { title: '对象', dataIndex: 'target', key: 'target', ellipsis: true },
  { title: '来源 IP', dataIndex: 'ip', key: 'ip', width: 140 },
  { title: '结果', key: 'result', width: 88 },
];
</script>

<template>
  <Teleport defer to="#page-extra">
    <span class="t-12 c-3">日志保留 180 天</span>
    <a-button>导出</a-button>
  </Teleport>
  <div class="panel al">
    <div class="panel-head">
      <span class="t-16 fw-600">操作记录</span>
      <a-space>
        <a-select v-model:value="action" size="small" class="al-w160" :options="ACTIONS.map((v) => ({ value: v, label: v === '全部' ? '全部动作' : v }))" />
        <a-select v-model:value="result" size="small" class="al-w120" :options="['全部', '成功', '失败', '拒绝'].map((v) => ({ value: v, label: v === '全部' ? '全部结果' : v }))" />
      </a-space>
    </div>
    <div class="al-table paged-scroll">
      <a-table size="small" row-key="id" :columns="columns" :data-source="res.items" :pagination="false">
        <template #bodyCell="{ column, record }">
          <span v-if="column.key === 'result'" :class="record.result === '成功' ? 'c-2' : 'c-error'">{{ record.result }}</span>
          <span v-else-if="column.key === 'time' || column.key === 'ip'" class="num">{{ record[column.dataIndex as 'time'] }}</span>
        </template>
      </a-table>
    </div>
    <AppPagination class="al-pager" variant="cursor" :has-prev="cq.hasPrev.value" :has-next="!!res.nextCursor" @prev="cq.prev()" @next="cq.next(res.nextCursor)">
      <template #extra><span class="t-12 c-3">按时间倒序 · 每页 {{ cq.size }} 条</span></template>
    </AppPagination>
  </div>
</template>

<style scoped>
.al { flex: 1; min-height: 0; }
.al-table { flex: 1; min-height: 0; padding: 0 16px; }
.al-pager { padding: 4px 16px 8px; }
.al-w120 { width: 120px; }
.al-w160 { width: 160px; }
</style>
