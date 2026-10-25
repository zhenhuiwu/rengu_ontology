<script setup lang="ts">
/**
 * 对象搜索（/use/objects/search）。参考 Object Explorer：类型 + 关键词，左侧按维度属性分面（带计数），右侧结果表。
 * URL：?type=store&kw=&region=华东一区,华东二区&city=&format=&status=&page=&size=&ask=1
 * 行点击打开公共详情抽屉（?drawer=kind:id）；“完整视图”进对象详情；“用这批对象提问”在当前页打开提问浮层。
 * 以此用户预览（?previewAs=U010231）：行和分面计数按 usePreview().scopeOf 过滤，左侧显示被预览用户的数据范围；导出、提问只读。
 */
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router';
import { message } from 'ant-design-vue';
import { DownloadOutlined, MessageOutlined, LockOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import { useDrawer } from '@/composables/useDrawer';
import { usePreview } from '@/composables/usePreview';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { paginate } from '@/mock/paginate';
import { fmtInt, fmtMoney } from '@/mock/format';
import { OBJ_TYPES, OBJ_SCOPE, objType, inScope, type AskScope } from '@/mock/objects-qa';
import ObjAskModal from './parts/ObjAskModal.vue';

const route = useRoute();
const router = useRouter();
const drawer = useDrawer();
const qs = (k: string) => { const v = route.query[k]; return String(Array.isArray(v) ? v[0] ?? '' : v ?? ''); };
const patch = (p: Record<string, string | null>) => {
  const q: LocationQueryRaw = { ...route.query };
  Object.entries(p).forEach(([k, v]) => { if (v == null || v === '') delete q[k]; else q[k] = v; });
  router.replace({ query: q });
};

const type = computed(() => objType(qs('type') || 'store'));
const typeKey = computed({
  get: () => type.value.kind,
  // 换类型：清掉关键词、分面和页码
  set: (v: string) => {
    const q: LocationQueryRaw = {};
    if (v !== 'store') q.type = v;
    router.replace({ query: q });
  },
});
const kwInput = ref(qs('kw'));
watch(() => route.query.kw, () => { kwInput.value = qs('kw'); });
const kw = computed(() => qs('kw').trim().toLowerCase());
const sel = (key: string) => qs(key).split(',').filter(Boolean);
const selected = computed(() => Object.fromEntries(type.value.facets.map((f) => [f.key, sel(f.key)])) as Record<string, string[]>);
const filterSig = computed(() => JSON.stringify([type.value.kind, kw.value, selected.value]));

const pq = usePagedQuery({ defaultSize: 16, filters: filterSig });

/* 预览：被预览用户的行级范围 */
const pv = usePreview();
const pvScope = computed(() => pv.scopeOf(type.value.label));
const scopedRows = computed(() => type.value.rows().filter((r) => inScope(type.value.kind, r, pvScope.value)));
const pvNoAccess = computed(() => pvScope.value === '无权限');

const byKw = computed(() => scopedRows.value.filter((r) => !kw.value || type.value.kw(r).toLowerCase().includes(kw.value)));
const match = (r: any, skip?: string) => type.value.facets.every((f) => f.key === skip || !selected.value[f.key].length || selected.value[f.key].includes(f.get(r)));
const rows = computed(() => byKw.value.filter((r) => match(r)));
/** 分面计数：其他分面 + 关键词生效后的数量（本分面自己不参与） */
const facetValues = computed(() => (pvNoAccess.value ? [] : type.value.facets).map((f) => {
  const base = byKw.value.filter((r) => match(r, f.key));
  const cnt = new Map<string, number>();
  base.forEach((r) => cnt.set(f.get(r), (cnt.get(f.get(r)) ?? 0) + 1));
  const all = new Set<string>(f.order ?? []);
  scopedRows.value.forEach((r) => all.add(f.get(r)));
  const vals = [...all].map((v) => ({ v, n: cnt.get(v) ?? 0 }));
  if (!f.order) vals.sort((a, b) => b.n - a.n || a.v.localeCompare(b.v, 'zh'));
  return { ...f, vals };
}));
function toggle(key: string, v: string, on: boolean) {
  const cur = new Set(selected.value[key]);
  if (on) cur.add(v); else cur.delete(v);
  patch({ [key]: [...cur].join(',') || null, page: null });
}
const conds = computed(() => type.value.facets.filter((f) => selected.value[f.key].length).map((f) => `${f.label} = ${selected.value[f.key].join(' / ')}`)
  .concat(kw.value ? [`关键词“${qs('kw')}”`] : []));
function clearAll() { const q: LocationQueryRaw = {}; if (type.value.kind !== 'store') q.type = type.value.kind; router.replace({ query: q }); }
function doSearch() { patch({ kw: kwInput.value.trim() || null, page: null }); }

const paged = computed(() => paginate(rows.value, { page: pq.page.value, size: pq.size.value }));
const columns = computed(() => [
  { title: `${type.value.label}${type.value.kind === 'order' ? '号' : '名称'}`, key: '__title', ellipsis: true },
  ...type.value.columns.map((c) => ({ title: c.title, key: c.key, width: c.width, align: c.num ? ('right' as const) : undefined })),
  { title: '', key: '__op', width: 88 },
]);
const cell = (r: any, key: string) => type.value.columns.find((c) => c.key === key)!.get(r);
const fmtCell = (r: any, key: string) => {
  const v = cell(r, key);
  if (v == null) return '--';
  if (typeof v === 'number') return key === 'area' || key === 'sales' ? fmtMoney(v, 1) : fmtMoney(v);
  return v;
};
const tone = (v: string) => (['装修中', '待支付'].includes(v) ? 'c-warning' : ['已闭店', '已取消'].includes(v) ? 'c-3' : '');

/* 多选与导出 */
const selKeys = ref<string[]>([]);
watch(() => type.value.kind, () => { selKeys.value = []; });
function exportCsv() {
  const list = selKeys.value.length ? rows.value.filter((r) => selKeys.value.includes(type.value.id(r))) : rows.value;
  const head = [columns.value[0].title, ...type.value.columns.map((c) => c.title)];
  const body = list.map((r) => [type.value.title(r), ...type.value.columns.map((c) => cell(r, c.key) ?? '')]);
  const csv = [head, ...body].map((l) => l.map((x) => `"${String(x).replace(/"/g, '""')}"`).join(',')).join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' }));
  a.download = `${type.value.label}_${list.length}.csv`; a.click();
  URL.revokeObjectURL(a.href);
  message.success(`已导出 ${fmtInt(list.length)} 条${selKeys.value.length ? '（已选）' : '（当前筛选）'}`);
}

/* 用这批对象提问：浮层，不跳转；?ask=1 可复现 */
const askOpen = computed({ get: () => qs('ask') === '1' && !pv.active.value, set: (v: boolean) => patch({ ask: v ? '1' : null }) });
const askScope = computed<AskScope>(() => {
  const ids = selKeys.value.length ? selKeys.value : rows.value.map((r) => type.value.id(r));
  return { kind: type.value.kind, label: type.value.label, conds: selKeys.value.length ? [`已选 ${selKeys.value.length} 个`] : conds.value, ids };
});
const askSugs = computed(() => (type.value.kind === 'store'
  ? ['这批门店 9 月销售额合计和环比', '哪几家门店销售额最高', '按业态对比客单价']
  : type.value.kind === 'order' ? ['这批订单实付金额合计', '未出库订单分布在哪些门店'] : ['这批商品的品类分布', '近 30 天销量前 5']));

/* 记住当前搜索条件，详情页面包屑用来返回 */
watch(() => route.fullPath, () => {
  const q = { ...route.query }; delete q.drawer; delete q.drawer2; delete q.ask;
  sessionStorage.setItem('obj-search:last', JSON.stringify(q));
}, { immediate: true });
function openDetail(r: any) {
  router.push({ path: '/use/objects/detail', query: { kind: type.value.kind, id: type.value.id(r) } });
}
</script>

<template>
  <div class="os">
    <div class="panel os-side">
      <div class="panel-head"><span class="t-16 fw-600">筛选</span>
        <a v-if="conds.length" class="t-12" @click="clearAll">清除全部</a></div>
      <div class="os-side-body">
        <div class="os-scope t-12 c-3">
          <template v-if="pv.active.value && pvNoAccess"><LockOutlined /> {{ pv.userName.value }} {{ pv.scopeLabel(type.label) }}，结果和计数都为空。</template>
          <template v-else-if="pv.active.value"><LockOutlined /> {{ pv.userName.value }} 的{{ pv.scopeLabel(type.label) }}。范围外的{{ type.label }}结果和计数都不含。</template>
          <template v-else><LockOutlined /> 可见范围：{{ OBJ_SCOPE.visible }}。{{ OBJ_SCOPE.hidden }}无权限，结果和计数都不含。</template>
        </div>
        <div v-for="f in facetValues" :key="f.key" class="os-facet">
          <div class="os-facet-head"><span class="fw-600">{{ f.label }}</span>
            <a v-if="selected[f.key].length" class="t-12" @click="patch({ [f.key]: null, page: null })">清除</a></div>
          <label v-for="x in f.vals" :key="x.v" class="os-opt" :class="{ off: !x.n && !selected[f.key].includes(x.v) }">
            <a-checkbox :checked="selected[f.key].includes(x.v)" :disabled="!x.n && !selected[f.key].includes(x.v)"
              @change="(e: any) => toggle(f.key, x.v, e.target.checked)">{{ x.v }}</a-checkbox>
            <span class="num c-3">{{ fmtInt(x.n) }}</span>
          </label>
        </div>
      </div>
    </div>

    <div class="panel os-main">
      <div class="panel-head">
        <div class="os-q">
          <a-select v-model:value="typeKey" style="width: 96px" :options="OBJ_TYPES.map((t) => ({ value: t.kind, label: t.label }))" />
          <a-input-search v-model:value="kwInput" allow-clear :placeholder="type.kwPlaceholder" style="width: 280px" @search="doSearch" />
          <span v-if="selKeys.length" class="c-2">已选 {{ selKeys.length }} 条 <a class="os-unsel" @click="selKeys = []">取消选择</a></span>
          <span v-else-if="conds.length" class="c-3 os-conds">{{ conds.join('、') }}</span>
        </div>
        <a-space :size="8">
          <a-tooltip :title="pv.reason.value || undefined"><a-button :disabled="pv.active.value || !rows.length" @click="exportCsv"><template #icon><DownloadOutlined /></template>{{ selKeys.length ? '导出所选' : '导出' }}</a-button></a-tooltip>
          <a-tooltip :title="pv.reason.value || undefined"><a-button type="primary" :disabled="pv.active.value || !rows.length" @click="askOpen = true"><template #icon><MessageOutlined /></template>用这批对象提问</a-button></a-tooltip>
        </a-space>
      </div>
      <div class="os-body">
        <template v-if="rows.length">
          <div class="paged-scroll os-tbl">
            <a-table :columns="columns" :data-source="paged.items" :row-key="type.id" size="middle" :pagination="false"
              :row-selection="{ selectedRowKeys: selKeys, preserveSelectedRowKeys: true, onChange: (k: (string | number)[]) => (selKeys = k as string[]) }"
              :custom-row="(r: any) => ({ onClick: () => drawer.open(type.kind, type.id(r)), class: 'os-row' })">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === '__title'"><span class="c-1">{{ type.title(record) }}</span></template>
                <template v-else-if="column.key === '__op'"><a @click.stop="openDetail(record)">完整视图</a></template>
                <template v-else-if="column.key === 'status' || column.key === 'ostatus'"><span :class="tone(String(cell(record, column.key)))">{{ cell(record, column.key) }}</span></template>
                <template v-else-if="column.align === 'right'"><span class="num">{{ fmtCell(record, column.key) }}</span></template>
                <template v-else><span :class="{ num: column.key === 'code' || column.key === 'sku' || column.key === 'createdAt' || column.key === 'openedAt' }">{{ fmtCell(record, column.key) }}</span></template>
              </template>
            </a-table>
          </div>
          <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="rows.length" :default-size="pq.defaultSize" :unit="'条'" />
        </template>
        <a-empty v-else-if="pvNoAccess" class="os-empty" :description="`${pv.userName.value} ${pv.scopeLabel(type.label)}，预览中不显示任何${type.label}`" />
        <a-empty v-else class="os-empty" description="没有符合条件的对象">
          <a-button @click="clearAll">清除筛选</a-button>
        </a-empty>
      </div>
    </div>

    <ObjAskModal v-model:open="askOpen" :scope="askScope" :suggestions="askSugs" />
  </div>
</template>

<style scoped>
.os { display: flex; gap: var(--gap); height: 100%; min-height: 0; }
.os-side { width: 248px; flex: none; }
.os-side-body { padding: 12px 16px; display: flex; flex-direction: column; gap: 12px; min-height: 0; }
.os-scope { padding: 8px; background: var(--g2); border-radius: var(--radius); }
.os-facet-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px; }
.os-opt { display: flex; justify-content: space-between; align-items: center; height: 28px; cursor: pointer; }
.os-opt.off { cursor: default; }
.os-main { flex: 1; }
.os-q { display: flex; align-items: center; gap: 8px; min-width: 0; }
.os-conds { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 560px; }
.os-unsel { margin-left: 8px; }
.os-body { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0 0 12px; }
.os-tbl { min-height: 0; }
.os-body :deep(.pg) { padding: 12px 16px 0; }
.os-body :deep(.os-row) { cursor: pointer; }
.os-empty { margin: auto; }
</style>
