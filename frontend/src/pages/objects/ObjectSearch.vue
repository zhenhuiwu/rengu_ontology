<script setup lang="ts">
/**
 * 对象搜索（/use/objects/search）。参考 Object Explorer：类型 + 关键词，左侧按维度属性分面（带计数），右侧结果表。
 * URL：?type=store&kw=&region=华东一区,华东二区&city=&format=&status=&page=&size=&ask=1
 * 行点击打开公共详情抽屉（?drawer=kind:id）；“完整视图”进对象详情；“用这批对象提问”在当前页打开提问浮层。
 * 以此用户预览（?previewAs=U010231）：行和分面计数按 usePreview().scopeOf 过滤，左侧显示被预览用户的数据范围；导出只读，提问照常（回答只含范围内数据）。
 * 真接口（开关 qa 且后台可用，qaLive）：结果 / 分面计数 / 范围说明走 O2，导出走 O3，提问走 O4（src/api/qaObjects.ts）；
 * 后台没起来回退 mock 渲染（qaFallBackIfDown 只提示一次），其余错误照旧提示。
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
import { OBJ_TYPES, OBJ_SCOPE, objType, inScope, previewDenied, KIND_LABEL, type AskScope } from '@/mock/objects-qa';
import ObjAskModal from './parts/ObjAskModal.vue';
import InfoTip from '../qa/InfoTip.vue';
import * as objApi from '@/api/qaObjects';
import { qaLive, qaFallBackIfDown } from '../qa/qaFallback';
import { errText } from '../qa/useQaSession';

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

/* 真接口：当前页结果 + 分面计数 + 范围说明（O2） */
const live = ref<objApi.ObjSearchResult | null>(null);
let lseq = 0;
async function loadLive() {
  if (!qaLive.value) { live.value = null; return; }
  const my = ++lseq;
  try {
    const r = await objApi.searchObjects(type.value.kind, { kw: kw.value, facets: selected.value, page: pq.page.value, size: pq.size.value });
    if (my === lseq) live.value = r;
  } catch (e) { if (my === lseq) { live.value = null; if (!qaFallBackIfDown(e)) message.error(`对象搜索失败：${errText(e)}`); } }
}

/* 预览：被预览用户的行级范围 */
const pv = usePreview();
/* 预览：无权对象的侧抽屉（含 URL 直接打开）一律拦下 */
watch(() => [route.query.drawer, route.query.drawer2, pv.active.value], () => {
  for (const k of ['drawer2', 'drawer']) {
    const v = qs(k); const i = v.indexOf(':');
    if (i > 0 && previewDenied(v.slice(0, i), v.slice(i + 1), pv.scopeOf)) {
      message.warning(`${pv.userName.value} 无权查看这个${KIND_LABEL[v.slice(0, i)]}，预览中不能打开`);
      patch(k === 'drawer' ? { drawer: null, drawer2: null } : { drawer2: null });
      return;
    }
  }
}, { immediate: true });
watch([qaLive, filterSig, pq.page, pq.size, () => pv.userName.value], loadLive, { immediate: true });
const pvScope = computed(() => pv.scopeOf(type.value.label));
const scopedRows = computed(() => type.value.rows().filter((r) => inScope(type.value.kind, r, pvScope.value)));
const pvNoAccess = computed(() => pvScope.value === '无权限');
/** 可见范围说明放在“筛选”旁的 ⓘ 里，不在标题下另起一行 */
const scopeText = computed(() => (live.value ? live.value.scopeText : pv.active.value && pvNoAccess.value
  ? `${pv.userName.value} ${pv.scopeLabel(type.value.label)}，结果和计数都为空。`
  : pv.active.value
    ? `${pv.userName.value} 的${pv.scopeLabel(type.value.label)}。范围外的${type.value.label}结果和计数都不含。`
    : `可见范围：${OBJ_SCOPE.visible}。${OBJ_SCOPE.hidden}无权限，结果和计数都不含。`));

const byKw = computed(() => scopedRows.value.filter((r) => !kw.value || type.value.kw(r).toLowerCase().includes(kw.value)));
const match = (r: any, skip?: string) => type.value.facets.every((f) => f.key === skip || !selected.value[f.key].length || selected.value[f.key].includes(f.get(r)));
const rows = computed(() => byKw.value.filter((r) => match(r)));
/** 分面计数：其他分面 + 关键词生效后的数量（本分面自己不参与） */
const facetValues = computed(() => (live.value ? live.value.facets : (pvNoAccess.value ? [] : type.value.facets).map((f) => {
  const base = byKw.value.filter((r) => match(r, f.key));
  const cnt = new Map<string, number>();
  base.forEach((r) => cnt.set(f.get(r), (cnt.get(f.get(r)) ?? 0) + 1));
  const all = new Set<string>(f.order ?? []);
  scopedRows.value.forEach((r) => all.add(f.get(r)));
  const vals = [...all].map((v) => ({ v, n: cnt.get(v) ?? 0 }));
  if (!f.order) vals.sort((a, b) => b.n - a.n || a.v.localeCompare(b.v, 'zh'));
  return { key: f.key, label: f.label, vals };
})));
function toggle(key: string, v: string, on: boolean) {
  const cur = new Set(selected.value[key]);
  if (on) cur.add(v); else cur.delete(v);
  patch({ [key]: [...cur].join(',') || null, page: null });
}
const conds = computed(() => type.value.facets.filter((f) => selected.value[f.key].length).map((f) => `${f.label} = ${selected.value[f.key].join(' / ')}`)
  .concat(kw.value ? [`关键词“${qs('kw')}”`] : []));
function clearAll() { const q: LocationQueryRaw = {}; if (type.value.kind !== 'store') q.type = type.value.kind; router.replace({ query: q }); }
function doSearch() { patch({ kw: kwInput.value.trim() || null, page: null }); }

const paged = computed(() => (live.value
  ? { items: live.value.items.map((x) => ({ ...x.cells, __id: x.id, __title: x.title })) as any[] }
  : paginate(rows.value, { page: pq.page.value, size: pq.size.value })));
/** 结果总数：真接口取 O2 total，mock 取筛选后的行数 */
const total = computed(() => (live.value ? live.value.total : rows.value.length));
/** 行 id / 标题：真接口行带 __id / __title，列值直接在行上 */
const rid = (r: any): string => (r.__id ?? type.value.id(r));
const rtitle = (r: any): string => (r.__title ?? type.value.title(r));
const columns = computed(() => [
  { title: `${type.value.label}${type.value.kind === 'order' ? '号' : '名称'}`, key: '__title', ellipsis: true },
  ...type.value.columns.map((c) => ({ title: c.title, key: c.key, width: c.width, align: c.num ? ('right' as const) : undefined })),
  { title: '', key: '__op', width: 88 },
]);
const cell = (r: any, key: string) => ('__id' in r ? r[key] : type.value.columns.find((c) => c.key === key)!.get(r));
const fmtCell = (r: any, key: string) => {
  const v = cell(r, key);
  if (v == null) return '--';
  if (typeof v === 'number') return key === 'area' || key === 'sales' ? fmtMoney(v, 1) : fmtMoney(v);
  return v;
};
const tone = (v: string) => (['已闭店', '已取消'].includes(v) ? 'c-3' : ['装修中', '待支付'].includes(v) ? 'c-2' : '');

/* 多选与导出 */
const selKeys = ref<string[]>([]);
watch(() => type.value.kind, () => { selKeys.value = []; });
async function exportCsv() {
  if (qaLive.value) {
    try {
      await objApi.exportObjects(type.value.kind, { kw: kw.value, facets: selected.value, ids: selKeys.value.length ? selKeys.value : undefined });
      message.success(`已导出 ${fmtInt(selKeys.value.length || total.value)} 条${selKeys.value.length ? '（已选）' : '（当前筛选）'}`);
      return;
    } catch (e) { if (!qaFallBackIfDown(e)) { message.error(`导出失败：${errText(e)}`); return; } }
  }
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
const askOpen = computed({ get: () => qs('ask') === '1', set: (v: boolean) => patch({ ask: v ? '1' : null }) });
const askScope = computed<AskScope>(() => {
  const ids = selKeys.value.length ? selKeys.value : rows.value.map((r) => type.value.id(r));
  return { kind: type.value.kind, label: type.value.label, conds: selKeys.value.length ? [`已选 ${selKeys.value.length} 个`] : conds.value, ids };
});
/** 真接口：没勾选时按筛选条件由后台重算范围（不传全部 id） */
const liveAsk = computed<objApi.ScopedAskIn | undefined>(() => (live.value
  ? { kind: type.value.kind, label: type.value.label, conds: askScope.value.conds, ...(selKeys.value.length ? { ids: selKeys.value } : { filter: { kw: kw.value, facets: selected.value } }) }
  : undefined));
const askCount = computed(() => (live.value ? selKeys.value.length || total.value : undefined));
const askSugs = computed(() => (type.value.kind === 'store'
  ? ['这批门店 9 月销售额合计和环比', '哪几家门店销售额最高', '按业态对比客单价']
  : type.value.kind === 'order' ? ['这批订单实付金额合计', '未出库订单分布在哪些门店'] : ['这批商品的品类分布', '近 30 天销量前 5']));

/* 记住当前搜索条件，详情页面包屑用来返回 */
watch(() => route.fullPath, () => {
  const q = { ...route.query }; delete q.drawer; delete q.drawer2; delete q.ask;
  sessionStorage.setItem('obj-search:last', JSON.stringify(q));
}, { immediate: true });
function openDetail(r: any) {
  router.push({ path: '/use/objects/detail', query: { kind: type.value.kind, id: rid(r) } });
}
</script>

<template>
  <div class="os">
    <div class="panel os-side">
      <div class="panel-head"><span class="os-side-title"><span class="t-16 fw-600">筛选</span><LockOutlined class="c-3" /><InfoTip :title="scopeText" /></span>
        <a v-if="conds.length" class="t-12" @click="clearAll">清除全部</a></div>
      <div class="os-side-body">
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
          <a-tooltip :title="pv.reason.value || undefined"><a-button :disabled="pv.active.value || !total" @click="exportCsv"><template #icon><DownloadOutlined /></template>{{ selKeys.length ? '导出所选' : '导出' }}</a-button></a-tooltip>
          <a-button type="primary" :disabled="!total" @click="askOpen = true"><template #icon><MessageOutlined /></template>用这批对象提问</a-button>
        </a-space>
      </div>
      <div class="os-body">
        <template v-if="total">
          <div class="paged-scroll os-tbl">
            <a-table :columns="columns" :data-source="paged.items" :row-key="rid" size="middle" :pagination="false"
              :row-selection="{ selectedRowKeys: selKeys, preserveSelectedRowKeys: true, onChange: (k: (string | number)[]) => (selKeys = k as string[]) }"
              :custom-row="(r: any) => ({ onClick: () => drawer.open(type.kind, rid(r)), class: 'os-row' })">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === '__title'"><span class="c-1">{{ rtitle(record) }}</span></template>
                <template v-else-if="column.key === '__op'"><a @click.stop="openDetail(record)">完整视图</a></template>
                <template v-else-if="column.key === 'status' || column.key === 'ostatus'"><span :class="tone(String(cell(record, column.key)))">{{ cell(record, column.key) }}</span></template>
                <template v-else-if="column.align === 'right'"><span class="num">{{ fmtCell(record, column.key) }}</span></template>
                <template v-else><span :class="{ num: column.key === 'code' || column.key === 'sku' || column.key === 'createdAt' || column.key === 'openedAt' }">{{ fmtCell(record, column.key) }}</span></template>
              </template>
            </a-table>
          </div>
          <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="total" :default-size="pq.defaultSize" :unit="'条'" />
        </template>
        <a-empty v-else-if="pvNoAccess && !live" class="os-empty" :description="`${pv.userName.value} ${pv.scopeLabel(type.label)}，预览中不显示任何${type.label}`" />
        <a-empty v-else class="os-empty" description="没有符合条件的对象">
          <a-button @click="clearAll">清除筛选</a-button>
        </a-empty>
      </div>
    </div>

    <ObjAskModal v-model:open="askOpen" :scope="askScope" :live-scope="liveAsk" :count="askCount" :suggestions="askSugs" />
  </div>
</template>

<style scoped>
.os { display: flex; gap: var(--gap); height: 100%; min-height: 0; }
.os-side { width: 248px; flex: none; }
.os-side-body { padding: 12px 16px; display: flex; flex-direction: column; gap: 12px; min-height: 0; }
.os-side-title { display: inline-flex; align-items: center; gap: 6px; }
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
