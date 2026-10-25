<script setup lang="ts">
/**
 * 审计日志（示例：cursor 分页，512 条 mock）。海量流水不显示总数，只有 上一页 / 下一页，游标在 URL ?cursor=。
 * 时间范围必选（默认近 24 小时，不能清空），可按操作人或对象搜索。
 */
import { computed, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import dayjs, { type Dayjs } from 'dayjs';
import AppPagination from '@/components/AppPagination.vue';
import { useCursorQuery } from '@/composables/usePagedQuery';
import { exportAuditLogs, fetchAuditActions, mockAuditActions, queryAuditLogs, queryAuditLogsMock, type AuditQuery } from '@/api/admin';
import { isApiError } from '@/api/http';

// mock 的“现在”= 最新一条日志的时间
const NOW = dayjs('2026-10-25 16:12:41');
const presets = [
  { label: '近 1 小时', value: [NOW.subtract(1, 'hour'), NOW] },
  { label: '近 24 小时', value: [NOW.subtract(24, 'hour'), NOW] },
  { label: '近 7 天', value: [NOW.subtract(7, 'day'), NOW] },
  { label: '近 30 天', value: [NOW.subtract(30, 'day'), NOW] },
] as { label: string; value: [Dayjs, Dayjs] }[];
const range = ref<[Dayjs, Dayjs]>(presets[1].value);
const action = ref('全部');
const result = ref('全部');
const field = ref<'actor' | 'target'>('actor');
const kw = ref('');
const cq = useCursorQuery({ size: 20, filters: [range, action, result, field, kw] });
// 取数在 src/api/admin.ts：mock 模式同步算（和原来一样），real 模式走 /api/admin/v1/audit-logs
const query = computed<AuditQuery>(() => {
  const [from, to] = range.value.map((d) => d.format('YYYY-MM-DD HH:mm:ss'));
  return { from, to, action: action.value, result: result.value, field: field.value, kw: kw.value, cursor: cq.cursor.value, size: cq.size };
});
const res = ref(queryAuditLogsMock(query.value));
let seq = 0;
watch(query, async (q) => {
  const n = ++seq;
  try {
    const r = await queryAuditLogs(q);
    if (n === seq) res.value = r;
  } catch (e) {
    if (n === seq) message.error(`审计日志加载失败（${isApiError(e) ? e.message || e.code : '未知错误'}）`);
  }
}, { immediate: true });
const ACTIONS = ref(['全部', ...mockAuditActions()]);
fetchAuditActions().then((a) => { ACTIONS.value = ['全部', ...a]; }).catch(() => { /* 保留 mock 下拉 */ });
async function onExport() {
  try {
    const job = await exportAuditLogs(query.value);
    if (job) message.success(`已创建导出任务 ${job}`);
  } catch (e) {
    message.error(isApiError(e) ? e.message : '导出失败');
  }
}
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
    <a-button @click="onExport">导出</a-button>
  </Teleport>
  <div class="panel al">
    <div class="panel-head">
      <span class="t-16 fw-600">操作记录</span>
      <a-space>
        <a-range-picker
          v-model:value="range" size="small" show-time :allow-clear="false" :presets="presets"
          format="YYYY-MM-DD HH:mm" class="al-range"
        />
        <a-select v-model:value="action" size="small" class="al-w160" :options="ACTIONS.map((v) => ({ value: v, label: v === '全部' ? '全部动作' : v }))" />
        <a-select v-model:value="result" size="small" class="al-w120" :options="['全部', '成功', '失败', '拒绝'].map((v) => ({ value: v, label: v === '全部' ? '全部结果' : v }))" />
        <a-input-group compact class="al-search">
          <a-select v-model:value="field" size="small" class="al-w96" :options="[{ value: 'actor', label: '操作人' }, { value: 'target', label: '对象' }]" />
          <a-input-search v-model:value="kw" size="small" allow-clear class="al-w200" :placeholder="field === 'actor' ? '姓名 / 用户 ID' : '对象名称 / 编号'" />
        </a-input-group>
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
      <template #extra><span class="t-12 c-3">{{ range[0].format('MM-DD HH:mm') }} 至 {{ range[1].format('MM-DD HH:mm') }} · 按时间倒序 · 每页 {{ cq.size }} 条</span></template>
    </AppPagination>
  </div>
</template>

<style scoped>
.al { flex: 1; min-height: 0; }
.al-table { flex: 1; min-height: 0; padding: 0 16px; }
.al-pager { padding: 4px 16px 8px; }
.al-w120 { width: 120px; }
.al-w96 { width: 96px; }
.al-w200 { width: 200px; }
.al-range { width: 320px; }
.al-search { display: flex; width: auto; }
.al-w160 { width: 160px; }
</style>
