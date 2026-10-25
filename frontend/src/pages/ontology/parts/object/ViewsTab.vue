<script setup lang="ts">
/**
 * 对象类型 → 使用与治理 → 视图：建在这个对象类型上的我的页面卡片、保存的对象列表、工作台视图（src/mock/objectViews.ts）。
 * 草稿里改动的属性如果被视图用到，标橙色“受草稿影响”，发布前提醒负责人。只读列表，没有写操作。
 */
import InfoTip from '@/components/InfoTip.vue';
import { computed, ref, watch } from 'vue';
import AppPagination from '@/components/AppPagination.vue';
import { paginate } from '@/mock/paginate';
import { fmtInt } from '@/mock/format';
import { OBJECT_PROPS, draftPropDiff } from '@/mock/objectProps';
import { viewsOf, type ObjectView, type ViewKind } from '@/mock/objectViews';
import { draftOf } from '../useDraftView';
import { objLabel } from './objectGov';

const props = defineProps<{ objectId: string; readonly: boolean }>();
const name = computed(() => objLabel(props.objectId));
const all = computed(() => viewsOf(props.objectId, name.value));

// 草稿改了哪些属性（显示名），用来标出受影响的视图
const draft = computed(() => draftOf('object', props.objectId));
const changed = computed(() => {
  if (!draft.value) return new Set<string>();
  const ps = OBJECT_PROPS[props.objectId] ?? [];
  const { map } = draftPropDiff(draft.value.id, name.value, ps);
  return new Set(Object.keys(map).map((api) => ps.find((p) => p.api === api)?.name ?? api));
});
const hit = (v: ObjectView) => v.props.filter((p) => changed.value.has(p));

const kind = ref<'全部' | ViewKind>('全部');
const kw = ref('');
const onlyHit = ref(false);
const rows = computed(() => all.value.filter((v) =>
  (kind.value === '全部' || v.kind === kind.value)
  && (!kw.value || v.name.includes(kw.value) || v.owner.includes(kw.value))
  && (!onlyHit.value || hit(v).length)));
const page = ref(1);
const size = ref(12);
watch([kind, kw, onlyHit], () => { page.value = 1; });
const pageRows = computed(() => paginate(rows.value, { page: page.value, size: size.value }).items);
const count = (k: ViewKind) => all.value.filter((v) => v.kind === k).length;
const hitCount = computed(() => all.value.filter((v) => hit(v).length).length);

const openTo = (v: ObjectView) => (v.kind === '对象列表' ? `/use/objects/search?type=${props.objectId}` : v.kind === '我的页面' ? '/use/qa/pages' : '');

const cols = [
  { title: '视图名称', key: 'name', width: 280 },
  { title: '类型', key: 'kind', width: 110 },
  { title: '条件', key: 'filter', ellipsis: true },
  { title: '所有者', key: 'owner', width: 90 },
  { title: '可见范围', key: 'vis', width: 220 },
  { title: '近 30 天使用', key: 'uses', width: 110, align: 'right' as const },
  { title: '最近使用', key: 'lastUsed', width: 150 },
  { title: '', key: 'op', width: 64 },
];
</script>

<template>
  <div class="ot-body">
    <div class="vw-tool">
      <a-segmented v-model:value="kind" :options="[{ label: `全部 ${all.length}`, value: '全部' }, { label: `对象列表 ${count('对象列表')}`, value: '对象列表' }, { label: `我的页面 ${count('我的页面')}`, value: '我的页面' }, { label: `工作台视图 ${count('工作台视图')}`, value: '工作台视图' }]" />
      <a-input-search v-model:value="kw" placeholder="搜索视图名称或所有者" class="vw-search" allow-clear />
      <a-checkbox v-if="draft" v-model:checked="onlyHit">只看受草稿 {{ draft.id }} 影响的（{{ hitCount }}）</a-checkbox>
      <InfoTip text="视图由使用者在对象搜索、我的页面里保存，这里只读" />
      <span class="vw-sp" />
    </div>
    <a-table :columns="cols" :data-source="pageRows" :pagination="false" size="small" row-key="id">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'name'">
          <span class="vw-name">
            <span class="t-14 c-1 vw-ell">{{ record.name }}</span>
            <a-tooltip v-if="hit(record).length" :title="`用到了草稿 ${draft?.id} 改动的属性：${hit(record).join('、')}`">
              <a-tag color="warning" class="vw-tag">受草稿影响</a-tag>
            </a-tooltip>
          </span>
        </template>
        <template v-else-if="column.key === 'kind'"><span class="c-2">{{ record.kind }}</span></template>
        <template v-else-if="column.key === 'filter'"><span class="c-2">{{ record.filter }}</span></template>
        <template v-else-if="column.key === 'owner'">{{ record.owner }}</template>
        <template v-else-if="column.key === 'vis'">
          {{ record.visibility }}<span v-if="record.visibility !== '仅自己' && record.visibility !== '全员'" class="c-3"> · {{ record.visibleTo }}</span>
        </template>
        <template v-else-if="column.key === 'uses'"><span class="num">{{ fmtInt(record.uses30d) }}</span></template>
        <template v-else-if="column.key === 'lastUsed'"><span class="num c-2">{{ record.lastUsed }}</span></template>
        <template v-else-if="column.key === 'op'">
          <router-link v-if="openTo(record)" :to="openTo(record)">打开</router-link>
          <a-tooltip v-else title="暂未开放"><span class="c-3">打开</span></a-tooltip>
        </template>
      </template>
      <template #emptyText><span class="t-14 c-3">没有符合条件的视图</span></template>
    </a-table>
    <AppPagination v-model:page="page" v-model:size="size" variant="simple" :total="rows.length" unit="个视图" :empty="false" />
  </div>
</template>

<style scoped>
.ot-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 12px; }
.vw-tool { display: flex; align-items: center; gap: 12px; flex: none; }
.vw-search { width: 240px; }
.vw-sp { flex: 1; }
.vw-name { display: flex; align-items: center; gap: 8px; min-width: 0; }
.vw-ell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.vw-tag { margin-inline-end: 0; flex: none; }
</style>
