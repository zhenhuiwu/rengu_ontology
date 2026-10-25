<script setup lang="ts">
/**
 * 对象类型 → 使用与治理 → 版本历史：改到这个对象类型的变更单（按 changes.ts 的 touches / diffs 认），
 * 后面接变更单流程上线前的修订记录（src/mock/objectHistory.ts，无单号、不能跳转）。
 * 状态颜色：审核中橙色，其余（含已驳回）灰色。点单号进“变更与发布”；已发布的历史版本可切到该版本只读查看。
 */
import InfoTip from '@/components/InfoTip.vue';
import { computed, ref, watch } from 'vue';
import AppPagination from '@/components/AppPagination.vue';
import { paginate } from '@/mock/paginate';
import { CURRENT_VERSION, NEXT_VERSION } from '@/mock/changes';
import { RELEASES } from '@/api/releases';
import { legacyRevs } from '@/mock/objectHistory';
import { useVersion } from '@/composables/useVersion';
import { crsOf, objLabel } from './objectGov';

const props = defineProps<{ objectId: string; readonly: boolean }>();
const name = computed(() => objLabel(props.objectId));
const { view } = useVersion();

interface Row { key: string; version: string; pending: boolean; title: string; status: string; author: string; reviewer: string; time: string; cr?: string; parts: string[] }
const all = computed<Row[]>(() => [
  ...crsOf(props.objectId).map(({ cr, parts }) => ({
    key: cr.id, cr: cr.id, title: cr.title, status: cr.status, author: cr.owner, reviewer: cr.reviewer, time: cr.updatedAt, parts,
    version: cr.release ?? (cr.status === '已驳回' || cr.status === '草稿' ? '--' : NEXT_VERSION), pending: !cr.release && cr.status !== '已驳回' && cr.status !== '草稿',
  })),
  ...legacyRevs(props.objectId, name.value).map((h) => ({
    key: h.version, version: h.version, pending: false, title: h.title, status: '迁移前记录', author: h.author, reviewer: '--', time: h.time, parts: [] as string[],
  })),
]);
const FILTERS = ['全部', '进行中', '已发布', '已驳回'] as const;
const filter = ref<(typeof FILTERS)[number]>('全部');
const match = (r: Row, f: (typeof FILTERS)[number]) => f === '全部'
  || (f === '进行中' && ['草稿', '审核中', '已通过'].includes(r.status))
  || (f === '已发布' && (r.status === '已发布' || r.status === '迁移前记录'))
  || (f === '已驳回' && r.status === '已驳回');
const rows = computed(() => all.value.filter((r) => match(r, filter.value)));
const count = (f: (typeof FILTERS)[number]) => all.value.filter((r) => match(r, f)).length;
const page = ref(1);
const size = ref(12);
watch(filter, () => { page.value = 1; });
const pageRows = computed(() => paginate(rows.value, { page: page.value, size: size.value }).items);
const canView = (r: Row) => !r.pending && r.version !== CURRENT_VERSION && RELEASES.some((x) => x.ver === r.version);
const tone = (s: string) => (s === '审核中' ? 'warning' : 'default');
const crCount = computed(() => all.value.filter((r) => r.cr).length);

const cols = [
  { title: '版本', key: 'version', width: 150 },
  { title: '变更单', key: 'cr', width: 100 },
  { title: '标题', key: 'title', ellipsis: true },
  { title: '改到', key: 'parts', width: 280, ellipsis: true },
  { title: '状态', key: 'status', width: 110 },
  { title: '发起人', key: 'author', width: 90 },
  { title: '审核人', key: 'reviewer', width: 90 },
  { title: '时间', key: 'time', width: 150 },
  { title: '', key: 'op', width: 100 },
];
</script>

<template>
  <div class="ot-body">
    <div class="hs-tool">
      <a-segmented :value="filter" :options="FILTERS.map((f) => ({ label: `${f} ${count(f)}`, value: f }))" @change="(v: any) => (filter = v)" />
      <InfoTip :text="`变更单 ${crCount} 张 · 当前生效版本 ${CURRENT_VERSION}，${NEXT_VERSION} 待发布。${RELEASES[RELEASES.length - 1]?.ver ?? ''} 之前的修订没有变更单`" />
    </div>
    <a-table :columns="cols" :data-source="pageRows" :pagination="false" size="small" row-key="key">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'version'">
          <span class="num" :class="record.pending || record.version === '--' ? 'c-3' : 'c-1'">{{ record.version }}</span>
          <span v-if="record.pending" class="t-12 c-3"> 待发布</span>
          <span v-else-if="record.version === CURRENT_VERSION" class="t-12 c-3"> 当前</span>
        </template>
        <template v-else-if="column.key === 'cr'">
          <router-link v-if="record.cr" class="num" :to="`/build/ontology/releases?cr=${record.cr}`">{{ record.cr }}</router-link>
          <span v-else class="c-3">--</span>
        </template>
        <template v-else-if="column.key === 'title'"><span :class="record.cr ? 'c-1' : 'c-2'">{{ record.title }}</span></template>
        <template v-else-if="column.key === 'parts'">
          <span v-if="record.parts.length" class="c-2">{{ record.parts.join('、') }}</span>
          <span v-else class="c-3">--</span>
        </template>
        <template v-else-if="column.key === 'status'">
          <a-tag v-if="record.cr" :color="tone(record.status)">{{ record.status }}</a-tag>
          <span v-else class="t-12 c-3">迁移前记录</span>
        </template>
        <template v-else-if="column.key === 'author'">{{ record.author }}</template>
        <template v-else-if="column.key === 'reviewer'"><span class="c-2">{{ record.reviewer }}</span></template>
        <template v-else-if="column.key === 'time'"><span class="num c-2">{{ record.time }}</span></template>
        <template v-else-if="column.key === 'op'">
          <a v-if="canView(record)" @click="view(record.version)">查看此版本</a>
        </template>
      </template>
      <template #emptyText><span class="t-14 c-3">没有符合条件的记录</span></template>
    </a-table>
    <AppPagination v-model:page="page" v-model:size="size" variant="simple" :total="rows.length" unit="条记录" :empty="false" />
  </div>
</template>

<style scoped>
.ot-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 12px; }
.hs-tool { display: flex; align-items: center; gap: 12px; flex: none; }
</style>
