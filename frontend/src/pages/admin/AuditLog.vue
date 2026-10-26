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
import { adminApiOn, downloadAuditExport, exportAuditLogs, fetchAuditActions, mockAuditActions, queryAuditLogs, queryAuditLogsMock, type AuditQuery } from '@/api/admin';
import { currentApiContext, isApiError } from '@/api/http';
import { useReadonly } from '@/composables/useReadonly';

const { readonly, reason } = useReadonly();
const exporting = ref(false);
let exportSeq = 0;
watch(() => JSON.stringify(currentApiContext()), () => { exportSeq += 1; }, { flush: 'sync' });

// mock 的“现在”= 最新一条日志的时间
const NOW = adminApiOn() ? dayjs() : dayjs('2026-10-25 16:12:41');
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
const cq = useCursorQuery({ size: 10, filters: [range, action, result, field, kw] });
// 取数在 src/api/admin.ts：mock 模式同步算（和原来一样），real 模式走 /api/admin/v1/audit-logs
const query = computed<AuditQuery>(() => {
  const [from, to] = range.value.map((d) => d.format('YYYY-MM-DD HH:mm:ss'));
  return { from, to, action: action.value, result: result.value, field: field.value, kw: kw.value, cursor: cq.cursor.value, size: cq.size };
});
const empty = () => ({ items: [], nextCursor: null, hasMore: false });
const res = ref(adminApiOn() ? empty() : queryAuditLogsMock(query.value));
const loadError = ref(''), loading = ref(false), loadedContext = ref('');
let seq = 0;
watch([query, () => JSON.stringify(currentApiContext())], async ([q, context]) => {
  const n = ++seq;
  if (adminApiOn() && loadedContext.value !== context) { res.value = empty(); loadedContext.value = context; }
  loading.value = true;
  loadError.value = '';
  try {
    const r = await queryAuditLogs(q);
    if (n === seq && context === JSON.stringify(currentApiContext())) res.value = r;
  } catch (e) {
    if (n === seq && context === JSON.stringify(currentApiContext())) { loadError.value = `审计日志加载失败（${isApiError(e) ? e.message || e.code : '未知错误'}）`; message.error(loadError.value); }
  } finally { if (n === seq) loading.value = false; }
}, { immediate: true });
const ACTIONS = ref(adminApiOn() ? ['全部'] : ['全部', ...mockAuditActions()]);
let actionsSeq = 0;
watch(() => JSON.stringify(currentApiContext()), async (context) => {
  const n = ++actionsSeq; ACTIONS.value = ['全部'];
  try { const actions = await fetchAuditActions(); if (n === actionsSeq && context === JSON.stringify(currentApiContext())) ACTIONS.value = ['全部', ...actions]; }
  catch (e) { if (n === actionsSeq && context === JSON.stringify(currentApiContext())) loadError.value = `操作候选加载失败：${isApiError(e) ? e.message : String(e)}`; }
}, { immediate: true });

async function onExport() {
  if (readonly.value || exporting.value) return;
  exporting.value = true;
  const n = ++exportSeq;
  const context = JSON.stringify(currentApiContext());
  const checkContext = () => {
    if (n !== exportSeq || context !== JSON.stringify(currentApiContext()) || readonly.value) {
      throw new Error('身份或查看版本已切换，请重新导出');
    }
  };
  try {
    const job = await exportAuditLogs(query.value);
    checkContext();
    if (!job) { message.info('演示模式不生成审计导出文件'); return; }
    const blob = await downloadAuditExport(job, checkContext);
    checkContext();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `审计日志-${job}.csv`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message.success('审计日志已下载');
  } catch (e) {
    message.error(isApiError(e) ? e.message : e instanceof Error ? e.message : '导出失败');
  } finally {
    exporting.value = false;
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
    <span class="t-12 c-3">{{ adminApiOn() ? '按数据库记录查询' : '日志保留 180 天' }}</span>
    <a-tooltip :title="reason"><a-button :disabled="readonly" :loading="exporting" @click="onExport">导出</a-button></a-tooltip>
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
    <a-alert v-if="loadError" type="error" show-icon :message="loadError" />
    <div class="al-table paged-scroll">
      <a-table size="small" row-key="id" :columns="columns" :loading="loading" :data-source="res.items" :pagination="false">
        <template #bodyCell="{ column, record }">
          <span v-if="column.key === 'result'" :class="record.result === '成功' ? 'c-2' : 'c-error'">{{ record.result }}</span>
          <span v-else-if="column.key === 'time' || column.key === 'ip'" class="num">{{ record[column.dataIndex as 'time'] }}</span>
        </template>
      </a-table>
    </div>
    <AppPagination class="al-pager" variant="cursor" :has-prev="!loading && cq.hasPrev.value" :has-next="!loading && !!res.nextCursor" @prev="cq.prev()" @next="cq.next(res.nextCursor)">
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
