<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue';
import { dynamicContextKey, listRuleScheduleRuns, type RuleScheduleRun } from '@/api/dynamic';
import AppPagination from '@/components/AppPagination.vue';

const props = defineProps<{ ruleId: string }>();
const rows = ref<RuleScheduleRun[]>([]), total = ref(0), page = ref(1), loading = ref(false), error = ref('');
const expanded = ref<string[]>([]);
let seq = 0;
onScopeDispose(() => { ++seq; });
watch([() => props.ruleId, dynamicContextKey], () => { ++seq; rows.value = []; total.value = 0; page.value = 1; expanded.value = []; error.value = ''; }, { flush: 'sync' });
async function load() {
  const n = ++seq, id = props.ruleId, key = dynamicContextKey();
  rows.value = []; total.value = 0; error.value = ''; loading.value = true;
  if (!id) { loading.value = false; return; }
  try {
    const result = await listRuleScheduleRuns(id, page.value);
    if (n === seq && key === dynamicContextKey() && id === props.ruleId) { rows.value = result.items; total.value = result.total; page.value = result.page; }
  } catch (e) { if (n === seq) error.value = e instanceof Error ? e.message : '读取运行记录失败'; }
  finally { if (n === seq) loading.value = false; }
}
watch([() => props.ruleId, dynamicContextKey, page], load, { immediate: true });
const labels: Record<string, string> = { pending: '待运行', done: '已完成', partial: '部分完成', failed: '运行失败', skipped: '已跳过' };
const cols = [{ title: '计划时间', dataIndex: 'scheduledAt', key: 'scheduledAt', width: 170 },
  { title: '版本', key: 'version', width: 72 }, { title: '状态', key: 'state', width: 110 },
  { title: '计算结果', key: 'result' }, { title: '详情', key: 'detail', width: 110 }];
</script>

<template>
  <div class="schedule-runs">
    <div class="schedule-tools"><span class="fw-600">定时运行记录</span><a-button :loading="loading" size="small" @click="load">刷新</a-button></div>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <div class="paged-scroll">
      <a-table :columns="cols" :data-source="rows" :pagination="false" :loading="loading" :expanded-row-keys="expanded" :show-expand-column="false" row-key="id" size="middle" :locale="{emptyText:'暂无定时运行记录'}">
        <template #bodyCell="{column,record}">
          <template v-if="column.key === 'version'">v{{ record.ruleVersion }}</template>
          <template v-else-if="column.key === 'state'"><a-tag :color="record.state === 'failed' ? 'error' : record.state === 'partial' ? 'warning' : 'default'">{{ labels[record.state] ?? record.state }}</a-tag></template>
          <template v-else-if="column.key === 'result'"><span v-if="record.result" class="t-12">候选 {{ record.result.candidates ?? '—' }} · 已计算 {{ record.result.evaluated ?? '—' }} · 无法计算 {{ record.result.unknown ?? '—' }} · 告警 {{ record.result.alerts ?? '—' }} · 去重 {{ record.result.deduped ?? '—' }}</span><span v-else class="c-3">{{ record.error ?? '待产生结果' }}</span></template>
          <template v-else-if="column.key === 'detail'"><a-button size="small" type="link" @click="expanded = expanded.includes(record.id) ? expanded.filter(id => id !== record.id) : [...expanded, record.id]">{{ expanded.includes(record.id) ? '收起' : '展开' }}</a-button></template>
        </template>
        <template #expandedRowRender="{record}"><a-descriptions :column="2" size="small"><a-descriptions-item label="开始">{{ record.startedAt ?? '—' }}</a-descriptions-item><a-descriptions-item label="结束">{{ record.finishedAt ?? '—' }}</a-descriptions-item><a-descriptions-item label="原因">{{ record.error ?? '—' }}</a-descriptions-item><a-descriptions-item label="动作">{{ record.result?.actionStatus ?? '—' }}</a-descriptions-item></a-descriptions></template>
      </a-table>
    </div>
    <AppPagination v-model:page="page" variant="simple" :total="total" :size="10" :empty="false" />
  </div>
</template>

<style scoped>
.schedule-runs { display: flex; flex-direction: column; flex: 1; min-height: 0; gap: 12px; }
.schedule-tools { display: flex; align-items: center; justify-content: space-between; }
</style>
