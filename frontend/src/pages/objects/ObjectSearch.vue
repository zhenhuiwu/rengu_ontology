<script setup lang="ts">
/**
 * 对象搜索（/use/objects/search），元数据驱动（backend/docs/backend/generic-object-explorer.md）：
 * 类型下拉按分组列出已发布的对象类型；关键词只查可搜索属性；左侧分面 = 可筛选属性（枚举带计数 / 时间和数值给区间）；
 * 结果表 = 标题列 + listed 属性列，只有 sortable 属性能排序。页面里不写任何具体对象类型、字段或筛选项。
 * URL：?type=&kw=&f=<筛选 JSON>&sort=&page=&size=&ask=1；行点击进对象详情（?type=&id=）。
 * 取数见 objMeta.ts（元数据 / 分面）和 objRows.ts（行：真接口 X1，后台没起来回退演示数据）。
 * 预览（?previewAs=）：行和计数只含被预览用户范围内的数据，导出只读，提问照常。
 */
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router';
import { message } from 'ant-design-vue';
import { DownloadOutlined, MessageOutlined, LockOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import { usePreview } from '@/composables/usePreview';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { fmtInt } from '@/mock/format';
import type { AskScope } from '@/mock/objects-qa';
import ObjAskModal from './parts/ObjAskModal.vue';
import InfoTip from '../qa/InfoTip.vue';
import * as objApi from '@/api/qaObjects';
import { qaLive, qaFallBackIfDown } from '../qa/qaFallback';
import { errText } from '../qa/useQaSession';
import { listTypes, groupTypes, loadMeta, fmtVal, propLabel, isNumeric, type TypeBrief, type TypeMeta, type Filters, type FacetMeta, type PropMeta } from './objMeta';
import { searchRows, mockMatched, type RowsResult } from './objRows';

const route = useRoute();
const router = useRouter();
const pv = usePreview();
const qs = (k: string) => { const v = route.query[k]; return String(Array.isArray(v) ? v[0] ?? '' : v ?? ''); };
const patch = (p: Record<string, string | null>) => {
  const q: LocationQueryRaw = { ...route.query };
  Object.entries(p).forEach(([k, v]) => { if (v == null || v === '') delete q[k]; else q[k] = v; });
  router.replace({ query: q });
};

/* 类型与元数据 */
const types = ref<TypeBrief[]>([]);
listTypes().then((t) => { types.value = t; });
const typeId = computed(() => qs('type') || types.value[0]?.id || '');
const typeKey = computed({ get: () => typeId.value || undefined, set: (v?: string) => router.replace({ query: v ? { type: v } : {} }) }); // 换类型清空条件
const meta = ref<TypeMeta | null>(null);
watch(typeId, async (id) => {
  meta.value = null;
  if (!id) return;
  const m = await loadMeta(id);
  if (id === typeId.value) meta.value = m;
}, { immediate: true });
const pmap = computed(() => new Map((meta.value?.properties ?? []).map((p) => [p.key, p])));
const prop = (k: string) => pmap.value.get(k);
const kwPlaceholder = computed(() => meta.value?.kwPlaceholder || meta.value?.properties.filter((p) => p.searchable).slice(0, 3).map((p) => p.name).join(' / ') || '关键词');

/* 条件：关键词 + 筛选（JSON 放 URL 的 f）+ 排序 */
const kwInput = ref(qs('kw'));
watch(() => route.query.kw, () => { kwInput.value = qs('kw'); });
const kw = computed(() => qs('kw').trim());
const filters = computed<Filters>(() => { try { const f = JSON.parse(qs('f') || '{}'); return f && typeof f === 'object' ? f : {}; } catch { return {}; } });
const setFilter = (k: string, v: Filters[string] | null) => {
  const f = { ...filters.value };
  const empty = v == null || (Array.isArray(v) ? !v.length : v.min == null && v.max == null);
  if (empty) delete f[k]; else f[k] = v;
  patch({ f: Object.keys(f).length ? JSON.stringify(f) : null, page: null });
};
const sort = computed(() => qs('sort'));
const filterSig = computed(() => JSON.stringify([typeId.value, kw.value, filters.value, sort.value]));
const pq = usePagedQuery({ defaultSize: 16, filters: filterSig });

/* 结果 */
const res = ref<RowsResult | null>(null);
const loading = ref(false);
let seq = 0;
async function load() {
  const m = meta.value;
  if (!m) { res.value = null; return; }
  const my = ++seq; loading.value = true;
  const q = { kw: kw.value, filters: filters.value, sort: sort.value, page: pq.page.value, size: pq.size.value };
  try {
    let r: RowsResult;
    try { r = await searchRows(m, q, qaLive.value, pv.scopeOf); } catch (e) {
      if (!qaLive.value || !qaFallBackIfDown(e)) throw e;
      r = await searchRows(m, q, false, pv.scopeOf);
    }
    if (my === seq) res.value = r;
  } catch (e) { if (my === seq) { res.value = null; message.error(`对象搜索失败：${errText(e)}`); } } finally { if (my === seq) loading.value = false; }
}
watch([meta, qaLive, filterSig, pq.page, pq.size, () => pv.userName.value], load, { immediate: true });
const total = computed(() => res.value?.total ?? 0);
const bound = computed(() => res.value?.bound ?? true);
const facets = computed(() => (res.value?.facets ?? []).filter((f) => prop(f.key)));
const enumSel = (k: string) => { const f = filters.value[k]; return Array.isArray(f) ? f : []; };
const rangeSel = (k: string) => { const f = filters.value[k]; return f && !Array.isArray(f) ? f : {}; };
function toggle(k: string, v: string, on: boolean) {
  const cur = new Set(enumSel(k));
  if (on) cur.add(v); else cur.delete(v);
  setFilter(k, [...cur]);
}
const showAll = ref<Record<string, boolean>>({});
const FACET_TOP = 8;
const facetVals = (f: FacetMeta) => (f.kind === 'enum' ? (showAll.value[f.key] ? f.values : f.values.slice(0, FACET_TOP)) : []);
const rangeHint = (f: FacetMeta) => (f.kind === 'range' && f.min != null ? `${fmtVal(prop(f.key), f.min)} ~ ${fmtVal(prop(f.key), f.max)}` : '');
const dateRange = (k: string) => { const r = rangeSel(k); return r.min || r.max ? [r.min ?? null, r.max ?? null] : null; };
const onDate = (k: string, v: [string, string] | null) => setFilter(k, v ? { min: v[0] || null, max: v[1] || null } : null);
const onNum = (k: string, side: 'min' | 'max', v: number | null) => setFilter(k, { ...rangeSel(k), [side]: v ?? null });

const conds = computed(() => Object.entries(filters.value).filter(([k]) => prop(k)).map(([k, f]) => {
  const p = prop(k)!;
  return Array.isArray(f) ? `${p.name} = ${f.join(' / ')}` : `${p.name} ${f.min != null ? fmtVal(p, f.min) : '…'} ~ ${f.max != null ? fmtVal(p, f.max) : '…'}`;
}).concat(kw.value ? [`关键词“${kw.value}”`] : []));
function clearAll() { router.replace({ query: typeId.value ? { type: typeId.value } : {} }); }
function doSearch() { patch({ kw: kwInput.value.trim() || null, page: null }); }
const scopeText = computed(() => (pv.active.value
  ? `${pv.userName.value} 的${pv.scopeLabel(meta.value?.name ?? '')}。范围外的对象结果和计数都不含。`
  : '结果和分面计数只含你有权限看到的数据。'));

/* 表格：标题列 + listed 属性；只有 sortable 能排序 */
const listed = computed<PropMeta[]>(() => (meta.value?.properties ?? []).filter((p) => p.listed && p.key !== meta.value?.titleProp && !p.sensitive));
const sortOrder = (k: string) => (sort.value === k ? 'ascend' : sort.value === `-${k}` ? 'descend' : null);
const columns = computed(() => [
  { title: prop(meta.value?.titleProp ?? '')?.name ?? '名称', key: '__title', ellipsis: true },
  ...listed.value.map((p) => ({
    title: propLabel(p), key: p.key, width: Math.max(96, propLabel(p).length * 14 + 32), align: isNumeric(p) ? ('right' as const) : undefined,
    sorter: p.sortable, sortOrder: p.sortable ? sortOrder(p.key) : undefined,
  })),
]);
function onTable(_p: unknown, _f: unknown, s: any) {
  const one = Array.isArray(s) ? s[0] : s;
  patch({ sort: one?.order ? `${one.order === 'descend' ? '-' : ''}${one.columnKey}` : null, page: null });
}
const isUnbound = (k: string) => !!res.value && !res.value.boundProps.includes(k);

/* 多选、导出、提问 */
const selKeys = ref<string[]>([]);
watch(typeId, () => { selKeys.value = []; });
const query = computed(() => ({ kw: kw.value, filters: filters.value }));
async function exportCsv() {
  const m = meta.value; if (!m) return;
  const n = selKeys.value.length || total.value;
  const done = () => message.success(`已导出 ${fmtInt(n)} 条${selKeys.value.length ? '（已选）' : '（当前筛选）'}`);
  // 只走后台导出（带行级范围、去敏感 / 隐藏属性）；后台不通时报错，不导出演示数据
  if (!qaLive.value) { message.error('导出失败：后台未连接，暂不能导出'); return; }
  try {
    await objApi.exploreExport(m.id, { ...query.value, ids: selKeys.value.length ? selKeys.value : undefined });
    done();
  } catch (e) {
    message.error(qaFallBackIfDown(e) ? '导出失败：后台未连接，暂不能导出' : `导出失败：${errText(e)}`);
  }
}
const askOpen = computed({ get: () => qs('ask') === '1', set: (v: boolean) => patch({ ask: v ? '1' : null }) });
const askConds = computed(() => (selKeys.value.length ? [`已选 ${selKeys.value.length} 个`] : conds.value));
const askScope = computed<AskScope>(() => {
  const m = meta.value;
  const ids = selKeys.value.length || !m || !askOpen.value ? selKeys.value : mockMatched(m, query.value, pv.scopeOf).map((r) => r.id);
  return { kind: m?.id ?? '', label: m?.name ?? '', conds: askConds.value, ids };
});
/** 真接口：没勾选时把筛选交给后台重算范围（O4 query），不传全部 id */
const liveAsk = computed<objApi.ScopedAskIn | undefined>(() => (qaLive.value && meta.value
  ? { kind: meta.value.id, label: meta.value.name, conds: askConds.value, ...(selKeys.value.length ? { ids: selKeys.value } : { query: query.value }) }
  : undefined));

/* 记住当前搜索条件，详情页面包屑用来返回 */
watch(() => route.fullPath, () => {
  const q = { ...route.query }; delete q.drawer; delete q.drawer2; delete q.ask;
  sessionStorage.setItem('obj-search:last', JSON.stringify(q));
}, { immediate: true });
const openDetail = (r: objApi.ExploreRow) => router.push({ path: '/use/objects/detail', query: { type: typeId.value, id: r.id } });
</script>

<template>
  <div class="os">
    <div class="panel os-side">
      <div class="panel-head"><span class="os-side-title"><span class="t-16 fw-600">筛选</span><LockOutlined class="c-3" /><InfoTip :title="scopeText" /></span>
        <a v-if="conds.length" class="t-12" @click="clearAll">清除全部</a></div>
      <div class="os-side-body">
        <div v-for="f in facets" :key="f.key" class="os-facet">
          <div class="os-facet-head"><span class="fw-600">{{ prop(f.key)!.name }}</span>
            <a v-if="filters[f.key]" class="t-12" @click="setFilter(f.key, null)">清除</a></div>
          <template v-if="f.kind === 'enum'">
            <label v-for="x in facetVals(f)" :key="x.value" class="os-opt" :class="{ off: !x.count && !enumSel(f.key).includes(x.value) }">
              <a-checkbox :checked="enumSel(f.key).includes(x.value)" :disabled="!x.count && !enumSel(f.key).includes(x.value)"
                @change="(e: any) => toggle(f.key, x.value, e.target.checked)">{{ x.value }}</a-checkbox>
              <span class="num c-3">{{ fmtInt(x.count) }}</span>
            </label>
            <a v-if="f.values.length > FACET_TOP" class="t-12" @click="showAll[f.key] = !showAll[f.key]">{{ showAll[f.key] ? '收起' : `更多（${f.values.length}）` }}</a>
            <div v-if="!f.values.length" class="c-3 t-12">没有可选值</div>
          </template>
          <template v-else>
            <a-range-picker v-if="!isNumeric(prop(f.key)!)" size="small" value-format="YYYY-MM-DD" :value="dateRange(f.key) as any"
              :placeholder="['开始', '结束']" @change="(v: any) => onDate(f.key, v)" />
            <div v-else class="os-num">
              <a-input-number size="small" :value="rangeSel(f.key).min as any" placeholder="最小" @change="(v: any) => onNum(f.key, 'min', v)" />
              <span class="c-3">~</span>
              <a-input-number size="small" :value="rangeSel(f.key).max as any" placeholder="最大" @change="(v: any) => onNum(f.key, 'max', v)" />
            </div>
            <div v-if="rangeHint(f)" class="c-3 t-12 num">{{ rangeHint(f) }}</div>
          </template>
        </div>
        <div v-if="meta && bound && !facets.length" class="c-3 t-12">这个类型没有可筛选的属性</div>
      </div>
    </div>

    <div class="panel os-main">
      <div class="panel-head">
        <div class="os-q">
          <a-select v-model:value="typeKey" style="width: 128px" :options="groupTypes(types)" placeholder="对象类型" show-search option-filter-prop="label" />
          <a-input-search v-model:value="kwInput" allow-clear :placeholder="kwPlaceholder" style="width: 280px" @search="doSearch" />
          <span v-if="selKeys.length" class="c-2">已选 {{ selKeys.length }} 条 <a class="os-unsel" @click="selKeys = []">取消选择</a></span>
          <span v-else-if="conds.length" class="c-3 os-conds">{{ conds.join('、') }}</span>
        </div>
        <a-space :size="8">
          <a-tooltip :title="pv.reason.value || undefined"><a-button :disabled="pv.active.value || !total" @click="exportCsv"><template #icon><DownloadOutlined /></template>{{ selKeys.length ? '导出所选' : '导出' }}</a-button></a-tooltip>
          <a-button type="primary" :disabled="!total" @click="askOpen = true"><template #icon><MessageOutlined /></template>用这批对象提问</a-button>
        </a-space>
      </div>
      <div class="os-body">
        <template v-if="total">
          <div class="paged-scroll os-tbl">
            <a-table :columns="columns" :data-source="res!.items" row-key="id" size="middle" :pagination="false" :loading="loading"
              :row-selection="{ selectedRowKeys: selKeys, preserveSelectedRowKeys: true, onChange: (k: (string | number)[]) => (selKeys = k as string[]) }"
              :custom-row="(r: any) => ({ onClick: () => openDetail(r), class: 'os-row' })" @change="onTable">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === '__title'"><span class="c-1">{{ record.title }}</span></template>
                <template v-else-if="isUnbound(String(column.key))"><a-tooltip title="这个属性还没接入数据"><span class="c-3">--</span></a-tooltip></template>
                <template v-else><span :class="{ num: column.align === 'right' }">{{ fmtVal(prop(String(column.key)), record.values[column.key]) }}</span></template>
              </template>
            </a-table>
          </div>
          <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="total" :default-size="pq.defaultSize" :unit="'条'" />
        </template>
        <a-spin v-else-if="loading || !meta" class="os-empty" />
        <a-empty v-else-if="!bound" class="os-empty" :description="`未接入数据：「${meta.name}」还没有绑定数据表，暂时搜不到对象`" />
        <a-empty v-else-if="pv.active.value && pv.scopeOf(meta.name) === '无权限'" class="os-empty" :description="`${pv.userName.value} ${pv.scopeLabel(meta.name)}，预览中不显示任何${meta.name}`" />
        <a-empty v-else class="os-empty" description="没有符合条件的对象">
          <a-button v-if="conds.length" @click="clearAll">清除筛选</a-button>
        </a-empty>
      </div>
    </div>

    <ObjAskModal v-if="meta" v-model:open="askOpen" :scope="askScope" :live-scope="liveAsk" :count="selKeys.length || total" :suggestions="meta.sampleQuestions" />
  </div>
</template>

<style scoped>
.os { display: flex; gap: var(--gap); height: 100%; min-height: 0; }
.os-side { width: 264px; flex: none; }
.os-side-body { padding: 12px 16px; display: flex; flex-direction: column; gap: 12px; min-height: 0; overflow: auto; }
.os-side-title { display: inline-flex; align-items: center; gap: 6px; }
.os-facet { display: flex; flex-direction: column; gap: 4px; }
.os-facet-head { display: flex; justify-content: space-between; align-items: center; }
.os-opt { display: flex; justify-content: space-between; align-items: center; height: 28px; cursor: pointer; }
.os-opt.off { cursor: default; }
.os-num { display: flex; align-items: center; gap: 6px; }
.os-num :deep(.ant-input-number) { flex: 1; min-width: 0; }
.os-main { flex: 1; min-width: 0; }
.os-q { display: flex; align-items: center; gap: 8px; min-width: 0; }
.os-conds { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 560px; }
.os-unsel { margin-left: 8px; }
.os-body { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0 0 12px; }
.os-tbl { min-height: 0; }
.os-body :deep(.pg) { padding: 12px 16px 0; }
.os-body :deep(.os-row) { cursor: pointer; }
.os-empty { margin: auto; }
</style>
