<script setup lang="ts">
/**
 * 对象类型 → 使用与治理 → 指标：基础对象或维度涉及这个对象类型的指标。
 * 状态从变更单推：审核中 → 橙色；有草稿 → 灰色“草稿中”；其余按指标自身状态，一律灰色。点指标名进指标口径页。
 */
import InfoTip from '@/components/InfoTip.vue';
import { computed, ref, watch } from 'vue';
import AppPagination from '@/components/AppPagination.vue';
import { paginate } from '@/mock/paginate';
import { CHANGE_REQUESTS, crDetail } from '@/mock/changes';
import { draftOf } from '../useDraftView';
import { metricsOf, objLabel, type MetricUse } from './objectGov';
import { isStaticReal } from '@/api/static';
import { useStaticPage } from '../useStaticPage';

const props = defineProps<{ objectId: string; readonly: boolean }>();

const reviewCr = (id: string) => CHANGE_REQUESTS.find((c) => c.status === '审核中' && crDetail(c.id).touches.includes(`metric:${id}`))?.id;
const shown = (u: MetricUse) => {
  if (isStaticReal()) return { text: u.m.status, cr: '' };
  if (u.m.status !== '已发布') return { text: u.m.status, cr: '' };
  const r = reviewCr(u.m.id);
  if (r) return { text: '审核中', cr: r };
  const d = draftOf('metric', u.m.id);
  return d ? { text: '草稿中', cr: d.id } : { text: '已发布', cr: '' };
};
const via = (u: MetricUse) => [
  ...(u.base.includes(props.objectId) ? ['基础对象'] : []),
  ...u.via.map((d) => `维度：${d}`),
].join('，');

const kind = ref<'all' | 'base' | 'dim'>('all');
const page = ref(1), size = ref(isStaticReal() ? 10 : 12);
watch([kind, () => props.objectId, size], () => { page.value = 1; });
const remote = useStaticPage<MetricUse['m'] & { base: string[]; via: string[] }>(
  () => props.objectId ? `/object-types/${encodeURIComponent(props.objectId)}/usage` : '',
  () => ({ kind: 'metric', filter: kind.value }), page, size,
);
const loadMetrics = remote.reload, loadError = remote.error;
const all = computed<MetricUse[]>(() => isStaticReal() ? remote.items.value.map(({ base, via, ...m }) => ({ m, base, via })) : metricsOf(props.objectId));
const rows = computed(() => isStaticReal() ? all.value : all.value.filter((u) => kind.value === 'all' || (kind.value === 'base' ? u.base.includes(props.objectId) : u.via.length && !u.base.includes(props.objectId))));
const pageRows = computed(() => isStaticReal() ? rows.value : paginate(rows.value, { page: page.value, size: size.value }).items);
const total = computed(() => isStaticReal() ? remote.total.value : rows.value.length);
const name = computed(() => objLabel(props.objectId));

const cols = [
  { title: '指标', key: 'name', width: 220 },
  { title: '关联方式', key: 'via', width: 220 },
  { title: '公式', key: 'formula', ellipsis: true },
  { title: '版本', key: 'version', width: 80 },
  { title: '状态', key: 'status', width: 170 },
  { title: '负责人', key: 'owner', width: 100 },
  { title: '更新时间', key: 'updatedAt', width: 160 },
];
</script>

<template>
  <div class="ot-body">
    <a-alert v-if="loadError" type="error" :message="loadError" show-icon><template #action><a-button size="small" @click="loadMetrics">重试</a-button></template></a-alert>
    <div class="mt-tool">
      <a-segmented v-model:value="kind" :options="[{ label: '全部', value: 'all' }, { label: '作为基础对象', value: 'base' }, { label: '仅作为维度', value: 'dim' }]" @change="page = 1" />
      <InfoTip v-if="!isStaticReal()" :text="`以${name}为基础对象 ${all.filter((u) => u.base.includes(objectId)).length} 个 · 按${name}拆分 ${all.filter((u) => u.via.length).length} 个。指标口径改动在“指标口径”页发起变更`" />
    </div>
    <a-table :columns="cols" :loading="remote.loading.value" :data-source="pageRows" :pagination="false" size="small" :row-key="(u: MetricUse) => u.m.id">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'name'">
          <router-link :to="`/build/ontology/metrics?metric=${record.m.id}`" :title="`${record.m.name} · ${record.m.id}`">{{ record.m.name }}</router-link>
        </template>
        <template v-else-if="column.key === 'via'">{{ via(record) }}</template>
        <template v-else-if="column.key === 'formula'"><span class="num" :title="record.m.formula">{{ record.m.formula || '—' }}</span></template>
        <template v-else-if="column.key === 'version'"><span class="num">{{ record.m.version }}</span></template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="shown(record).text === '审核中' ? 'warning' : 'default'">{{ shown(record).text }}</a-tag>
          <router-link v-if="shown(record).cr" class="t-12 num" :to="`/build/ontology/releases?cr=${shown(record).cr}`">{{ shown(record).cr }}</router-link>
        </template>
        <template v-else-if="column.key === 'owner'">{{ record.m.owner }}</template>
        <template v-else-if="column.key === 'updatedAt'"><span class="num c-2">{{ record.m.updatedAt || '—' }}</span></template>
      </template>
      <template #emptyText><span class="t-14 c-3">没有指标用到{{ name }}</span></template>
    </a-table>
    <AppPagination v-model:page="page" v-model:size="size" :variant="isStaticReal() ? 'full' : 'simple'" :total="total" unit="个指标" :empty="false" class="mt-pager" />
  </div>
</template>

<style scoped>
.ot-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 12px; }
.mt-tool { display: flex; align-items: center; gap: 12px; flex: none; }
.mt-api { margin-left: 8px; }
.mt-pager { justify-content: flex-end; }
</style>
