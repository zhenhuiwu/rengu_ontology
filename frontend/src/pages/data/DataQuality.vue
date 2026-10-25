<script setup lang="ts">
/**
 * 数据质量：按对象类型的检查项（新鲜度对照“数据截至”、空值率、唯一性、引用完整性）。
 * 左侧检查项表（full 分页，筛选写进 URL），右侧所选检查项的运行历史（simple 分页）。
 * 失败的检查点对象类型名，打开对象类型抽屉（?drawer=object-type:inventory），不跳转页面。
 * URL：?type=inventory&kind=新鲜度&result=失败&check=QC-045
 */
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import InfoTip from '@/components/InfoTip.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { useDrawer } from '@/composables/useDrawer';
import { useReadonly } from '@/composables/useReadonly';
import { paginate } from '@/mock';
import { DATA_CUTOFF, QUALITY_CHECKS, Q_OBJECT_TYPES, type CheckKind, type QCheck } from '@/mock/quality';

const route = useRoute();
const router = useRouter();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const drawer = useDrawer();
const { readonly, reason } = useReadonly();

const KINDS: CheckKind[] = ['新鲜度', '空值率', '唯一性', '引用完整性'];
const typeF = computed({ get: () => q('type') || undefined, set: (v?: string) => setQuery({ type: v || undefined }) });
const kindF = computed({ get: () => q('kind') || undefined, set: (v?: string) => setQuery({ kind: v || undefined }) });
const resultF = computed({ get: () => q('result') || '全部', set: (v: string) => setQuery({ result: v === '全部' ? undefined : v }) });

const failed = QUALITY_CHECKS.filter((c) => c.result === '失败');
const failedTypes = new Set(failed.map((c) => c.typeKey));
const lastRun = QUALITY_CHECKS.reduce((m, c) => (c.lastRun > m ? c.lastRun : m), '');

// 失败在前，其余按对象类型顺序
const rows = computed(() => QUALITY_CHECKS
  .filter((c) => (!typeF.value || c.typeKey === typeF.value) && (!kindF.value || c.kind === kindF.value) && (resultF.value === '全部' || c.result === resultF.value))
  .sort((a, b) => (a.result === b.result ? 0 : a.result === '失败' ? -1 : 1)));
const pq = usePagedQuery({ defaultSize: 15, filters: [typeF, kindF, resultF] });
const paged = computed(() => paginate(rows.value, { page: pq.page.value, size: pq.size.value }));

const cols = [
  { title: '编号', dataIndex: 'id', key: 'id', width: 80 },
  { title: '对象类型', key: 'type', width: 110 },
  { title: '检查', dataIndex: 'kind', key: 'kind', width: 90 },
  { title: '规则', dataIndex: 'rule', key: 'rule', ellipsis: true },
  { title: '阈值', dataIndex: 'threshold', key: 'threshold', width: 110 },
  { title: '最近结果', key: 'actual', width: 260, ellipsis: true },
  { title: '结果', key: 'result', width: 80 },
  { title: '最近运行', key: 'lastRun', width: 140 },
];

// 选中的检查项 → 右侧历史
const selected = computed<QCheck>(() => QUALITY_CHECKS.find((c) => c.id === q('check')) ?? rows.value[0] ?? QUALITY_CHECKS[0]);
const HSIZE = 15;
const hpage = ref(1);
watch(() => selected.value.id, () => { hpage.value = 1; });
const hist = computed(() => paginate(selected.value.history, { page: hpage.value, size: HSIZE }));
const histFails = computed(() => selected.value.history.filter((r) => r.result === '失败').length);
const tl = (r: string) => (r === '失败' ? 'tl-failed' : 'tl-normal');
const openType = (key: string) => drawer.open('object-type', key);
const rowProps = (c: QCheck) => ({ class: c.id === selected.value.id ? 'dq-row-on' : '', onClick: () => setQuery({ check: c.id }) });
const runNow = () => message.success(`已提交运行：${selected.value.id} ${selected.value.typeName} · ${selected.value.kind}`);
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-space>
      <span class="t-12 c-3">数据截至 <span class="num">{{ DATA_CUTOFF }}</span></span>
      <a-tooltip :title="reason"><a-button :disabled="readonly">新建检查</a-button></a-tooltip>
      <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly" @click="message.success('已提交全部检查，预计 3 分钟完成')">全部运行</a-button></a-tooltip>
    </a-space>
  </Teleport>
  <div class="dq">
    <div class="dq-sum">
      <div class="panel dq-kpi"><div class="t-14 c-2">检查项<InfoTip :text="`覆盖 ${Q_OBJECT_TYPES.length} 个对象类型`" /></div><div class="t-24 fw-600 num c-1">{{ QUALITY_CHECKS.length }}</div></div>
      <div class="panel dq-kpi"><div class="t-14 c-2">通过</div><div class="dq-kv"><span class="t-24 fw-600 num c-1">{{ QUALITY_CHECKS.length - failed.length }}</span><span class="t-14 c-2 num">{{ (((QUALITY_CHECKS.length - failed.length) / QUALITY_CHECKS.length) * 100).toFixed(1) }}%</span></div></div>
      <div class="panel dq-kpi"><div class="t-14 c-2">失败<InfoTip :text="`涉及 ${failedTypes.size} 个对象类型`" /></div><div class="t-24 fw-600 num tl-failed">{{ failed.length }}</div></div>
      <div class="panel dq-kpi"><div class="t-14 c-2">数据截至<InfoTip text="新鲜度检查以此为准" /></div><div class="t-24 fw-600 num c-1">{{ DATA_CUTOFF.slice(5) }}</div></div>
      <div class="panel dq-kpi"><div class="t-14 c-2">最近运行<InfoTip text="新鲜度检查每 2 小时运行一次，其余检查每天 23:30" /></div><div class="t-24 fw-600 num c-1">{{ lastRun.slice(5) }}</div></div>
    </div>

    <div class="dq-main">
      <div class="panel dq-left">
        <div class="dq-filters">
          <a-select v-model:value="typeF" allow-clear placeholder="对象类型" class="dq-sel" :options="Q_OBJECT_TYPES.map((t) => ({ value: t.key, label: t.name }))" />
          <a-select v-model:value="kindF" allow-clear placeholder="检查类型" class="dq-sel" :options="KINDS.map((k) => ({ value: k, label: k }))" />
          <a-radio-group v-model:value="resultF" button-style="solid">
            <a-radio-button v-for="k in ['全部', '通过', '失败']" :key="k" :value="k">{{ k }}</a-radio-button>
          </a-radio-group>
        </div>
        <div class="dq-table" :class="{ 'paged-scroll': pq.overflow.value }">
          <a-table size="small" row-key="id" :columns="cols" :data-source="paged.items" :pagination="false" :custom-row="rowProps">
            <template #bodyCell="{ column, record }">
              <span v-if="column.key === 'type'">
                <a v-if="record.result === '失败'" @click.stop="openType(record.typeKey)">{{ record.typeName }}</a>
                <span v-else class="c-1">{{ record.typeName }}</span>
              </span>
              <span v-else-if="column.key === 'actual'" :class="record.result === '失败' ? 'tl-failed' : 'c-2'">{{ record.actual }}</span>
              <span v-else-if="column.key === 'result'" class="dq-res" :class="tl(record.result)"><span class="dq-dot" />{{ record.result }}</span>
              <span v-else-if="column.key === 'lastRun'" class="num c-2">{{ record.lastRun.slice(5) }}</span>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="paged.total" :default-size="pq.defaultSize" unit="项" class="dq-pager" />
      </div>

      <div class="panel dq-right">
        <div class="panel-head">
          <span class="t-16 fw-600">运行历史</span>
          <a-tooltip :title="reason"><a-button size="small" :disabled="readonly" @click="runNow">立即运行</a-button></a-tooltip>
        </div>
        <div class="dq-hhead">
          <div class="dq-htitle">
            <span class="num c-3 t-12">{{ selected.id }}</span>
            <a v-if="selected.result === '失败'" class="t-14 fw-600" @click="openType(selected.typeKey)">{{ selected.typeName }}</a>
            <span v-else class="t-14 fw-600 c-1">{{ selected.typeName }}</span>
            <span class="t-14 fw-600 c-1">· {{ selected.kind }}</span>
            <span class="dq-res t-12" :class="tl(selected.result)"><span class="dq-dot" />{{ selected.result }}</span>
            <InfoTip :text="`规则：${selected.rule} · 阈值 ${selected.threshold}${selected.note ? '。说明：' + selected.note : ''}。近 ${selected.history.length} 次运行失败 ${histFails} 次${selected.failSince ? '，自 ' + selected.failSince + ' 起持续失败' : ''}`" />
            <span class="t-14 c-2 dq-hfail">失败 <span class="num">{{ histFails }}/{{ selected.history.length }}</span> 次</span>
          </div>
        </div>
        <div class="dq-hist">
          <div v-for="r in hist.items" :key="r.time" class="dq-hrow">
            <span class="num c-2 dq-htime">{{ r.time }}</span>
            <span class="dq-res dq-hres" :class="tl(r.result)"><span class="dq-dot" />{{ r.result }}</span>
            <span class="dq-ellip" :class="r.result === '失败' ? 'tl-failed' : 'c-2'">{{ r.actual }}</span>
            <span class="num c-3 dq-hms">{{ (r.ms / 1000).toFixed(1) }} 秒</span>
          </div>
        </div>
        <AppPagination v-model:page="hpage" variant="simple" :total="selected.history.length" :size="HSIZE" unit="次运行" class="dq-pager" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.dq { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: var(--gap); }
.dq-sum { display: grid; grid-template-columns: repeat(5, 1fr); gap: var(--gap); flex: none; }
.dq-kpi { padding: 12px 16px; flex-direction: row; align-items: center; justify-content: space-between; gap: 8px; }
.dq-kv { display: flex; align-items: baseline; gap: 8px; }
.dq-hfail { margin-left: auto; }
.dq-main { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.dq-left { flex: 1; min-width: 0; }
.dq-right { width: 460px; flex: none; }
.dq-filters { display: flex; gap: 8px; align-items: center; padding: 12px 16px; flex: none; }
.dq-sel { width: 160px; }
.dq-table { flex: 1; min-height: 0; padding: 0 16px; }
.dq-table :deep(.ant-table-row) { cursor: pointer; }
.dq-table :deep(.dq-row-on > td) { background: var(--brand-bg) !important; }
.dq-pager { padding: 6px 16px 8px; }
.dq-res { display: inline-flex; align-items: center; gap: 6px; }
.dq-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.dq-hhead { padding: 0 16px 10px; border-bottom: var(--border); display: flex; flex-direction: column; gap: 4px; flex: none; }
.dq-htitle { display: flex; align-items: center; gap: 8px; }
.dq-hist { flex: 1; min-height: 0; overflow: hidden; }
.dq-hrow { display: flex; align-items: center; gap: 12px; padding: 0 16px; height: 36px; border-bottom: var(--border); }
.dq-htime { flex: none; width: 128px; }
.dq-hres { flex: none; width: 52px; }
.dq-hms { flex: none; margin-left: auto; }
.dq-ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
</style>
