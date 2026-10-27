<script setup lang="ts">
/**
 * 对象类型：左侧对象类型列表（简洁分页），右侧详情按 v5 分三组页签（定义 / 行为 / 使用与治理）。
 * 选中类型写进 ?type=，页签写进 ?tab=。属性、概览、映射都按对象类型各自取数（src/mock/objectProps.ts 的 OBJECT_PROPS / objectMeta，
 * 负责人、数据源、数据质量取 src/mock/quality.ts）。状态从变更单推：审核中 → 橙色；只有草稿 → 灰色“草稿中”；其余灰色。
 */
import InfoTip from '@/components/InfoTip.vue';
import { useAppStore } from '@/stores/app';
import { currentApiContext } from '@/api/http';
import { computed, reactive, ref, watch, onBeforeUnmount, type Component } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import GroupedTabs from '@/components/GroupedTabs.vue';
import HoverCard from '@/components/HoverCard.vue';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import DefinitionEditor from './parts/DefinitionEditor.vue';
import ObjectImportDrawer from './parts/ObjectImportDrawer.vue';
import { recollectObjectMetadata, type MetadataDiff } from '@/api/objectImport';
import { loadDraftState } from './parts/useDraftView';
import { useDraftView, draftOf } from './parts/useDraftView';
import { useStaticCr, useRealCrRecords, draftRows, draftFields } from './parts/useStaticCr';
import { message } from 'ant-design-vue';
import { useConfirmStore } from '@/stores/confirm';
import { LINKS as ALL_LINKS, LINK_OBJECTS, PATH_GROUPS, defaultLabel, objName } from '@/mock/links';
import { OBJECT_PROPS, draftPropDiff, objectMeta, dbOf, ORDER_MAP_ROWS as MAP_ROWS, type ObjProp, type PropDiff } from '@/mock/objectProps';
import { qObjType, QUALITY_CHECKS } from '@/mock/quality';
import { PERM_OBJECTS } from '@/mock/roles';
import { RULE_DEFS } from '@/mock/rules';
import { legacyRevs } from '@/mock/objectHistory';
import { fetchObjectOverviewRuntime, type ObjectOverviewRuntime } from '@/api/objectOverview';
import { definitionDetail, type DefinitionValues } from '@/api/definitionForms';
import { staticContextKey, isStaticReal, fetchStaticDetail } from '@/api/static';
import { useStaticPage } from './parts/useStaticPage';
import MetricsTab from './parts/object/MetricsTab.vue';
import ViewsTab from './parts/object/ViewsTab.vue';
import PermissionsTab from './parts/object/PermissionsTab.vue';
import HistoryTab from './parts/object/HistoryTab.vue';
import AppPagination from '@/components/AppPagination.vue';
import { crsOf, metricsOf } from './parts/object/objectGov';
import ObjBehaviorLifecycle from './parts/object/ObjBehaviorLifecycle.vue';
import ObjBehaviorActions from './parts/object/ObjBehaviorActions.vue';
import ObjBehaviorAutomation from './parts/object/ObjBehaviorAutomation.vue';

/**
 * 页签配置。新增页签只需在这里加一项，页面其他地方不用动。
 *   { key, label, phase, component? }
 *   - key：写进 ?tab= 的值，全页唯一
 *   - label：页签上显示的文字
 *   - phase：所在分组，'def' 定义 / 'behavior' 行为 / 'governance' 使用与治理（分组顺序固定，组内按数组顺序）
 *   - component：可选。有 component 的页签由组件自己渲染，统一接收两个 props：
 *       objectId: string   当前对象类型 id（如 'order'、'store'，与 src/mock/links.ts 的 LINK_OBJECTS.id 一致）
 *       readonly: boolean  true = 不可编辑（看已发布版、历史版本或以此用户预览时）；只有在草稿视图里才是 false
 *     组件放在 src/pages/ontology/parts/object/ 下；草稿单本身用 useDraftView('object', () => props.objectId) 取。
 *     组件根节点请用 class="ot-body"（或自带 flex: 1; min-height: 0），保证一屏不出滚动条。
 *   - 没有 component 的页签：概览 / 属性 / 关系 / 数据源与映射 在本页模板里内置渲染，其余显示“待设计”。
 */
interface ObjTab { key: string; label: string; phase: 'def' | 'behavior' | 'governance'; component?: Component }
const TABS: ObjTab[] = [
  { key: 'overview', label: '概览', phase: 'def' },
  { key: 'properties', label: '属性', phase: 'def' },
  { key: 'links', label: '关系', phase: 'def' },
  { key: 'mapping', label: '数据源与映射', phase: 'def' },
  { key: 'lifecycle', label: '生命周期', phase: 'behavior', component: ObjBehaviorLifecycle },
  { key: 'actions', label: '动作', phase: 'behavior', component: ObjBehaviorActions },
  { key: 'automation', label: '自动化', phase: 'behavior', component: ObjBehaviorAutomation },
  { key: 'metrics', label: '指标', phase: 'governance', component: MetricsTab },
  { key: 'views', label: '视图', phase: 'governance', component: ViewsTab },
  { key: 'permissions', label: '权限', phase: 'governance', component: PermissionsTab },
  { key: 'history', label: '版本历史', phase: 'governance', component: HistoryTab },
];
const PHASES = [{ key: 'def', title: '定义' }, { key: 'behavior', title: '行为' }, { key: 'governance', title: '使用与治理' }] as const;
const TAB_GROUPS = PHASES.map((g) => ({ key: g.key, title: g.title, tabs: TABS.filter((t) => t.phase === g.key).map((t) => ({ key: t.key, title: t.label })) }))
  .filter((g) => g.tabs.length);

const MOCK_TYPES = [
  { id: 'order', name: '订单', api: 'SalesOrder', group: '交易', props: 32, count: '1,286,402', status: '已发布' },
  { id: 'order_line', name: '订单行', api: 'SalesOrderLine', group: '交易', props: 14, count: '4,917,355', status: '已发布' },
  { id: 'refund', name: '退款单', api: 'Refund', group: '交易', props: 16, count: '38,914', status: '已发布' },
  { id: 'refund_line', name: '退款行', api: 'RefundLine', group: '交易', props: 11, count: '52,307', status: '已发布' },
  { id: 'member', name: '会员', api: 'Member', group: '客户', props: 22, count: '863,027', status: '已发布' },
  { id: 'member_level', name: '会员等级', api: 'MemberLevel', group: '客户', props: 6, count: '5', status: '已发布' },
  { id: 'store', name: '门店', api: 'Store', group: '组织', props: 18, count: '486', status: '已发布' },
  { id: 'region', name: '大区', api: 'Region', group: '组织', props: 9, count: '6', status: '已发布' },
  { id: 'province', name: '省份', api: 'Province', group: '组织', props: 5, count: '31', status: '已发布' },
  { id: 'product', name: '商品', api: 'Product', group: '商品', props: 26, count: '48,213', status: '已发布' },
  { id: 'category', name: '品类', api: 'Category', group: '商品', props: 7, count: '1,204', status: '已发布' },
  { id: 'promotion', name: '促销活动', api: 'Promotion', group: '商品', props: 19, count: '2,671', status: '已发布' },
  { id: 'warehouse', name: '仓库', api: 'Warehouse', group: '供应链', props: 12, count: '4', status: '已发布' },
  { id: 'inventory', name: '库存快照', api: 'InventorySnapshot', group: '供应链', props: 10, count: '9,412,880', status: '已发布' },
  { id: 'supplier', name: '供应商', api: 'Supplier', group: '供应链', props: 17, count: '1,148', status: '已发布' },
  { id: 'purchase', name: '采购单', api: 'PurchaseOrder', group: '供应链', props: 21, count: '76,530', status: '草稿' },
  { id: 'shipment', name: '出库单', api: 'Shipment', group: '供应链', props: 15, count: '1,197,226', status: '已发布' },
  { id: 'employee', name: '员工', api: 'Employee', group: '组织', props: 13, count: '7,382', status: '已发布' },
];
/** 真接口：GET /object-types 的已访问页及所选详情缓存（不是完整目录）；mock：页内常量 */
const TYPES: typeof MOCK_TYPES = isStaticReal() ? reactive([]) : MOCK_TYPES;
const firstId = ref('');
const realProps = ref<Record<string, ObjProp[]>>({});

const app = useAppStore();
const route = useRoute();
const router = useRouter();
const selected = computed(() => (route.query.type as string) || (isStaticReal() ? firstId.value : 'order'));
const tab = computed({
  get: () => (route.query.tab as string) || 'overview',
  set: (v) => router.replace({ query: { ...route.query, tab: v, p: undefined } }),
});
const curTab = computed(() => TABS.find((t) => t.key === tab.value));
const tabTitle = computed(() => curTab.value?.label);
const publishedCurrent = computed(() => TYPES.find((t) => t.id === selected.value) ?? (isStaticReal() ? undefined : TYPES[0]) ?? { id: selected.value, name: '', api: '', group: '', props: 0, count: '—', status: '草稿' });
const definitionOpen = ref(false), newDefinition = ref(false), importOpen = ref(false);
const metadataDiff = ref<MetadataDiff>(), metadataOpen = ref(false), metadataLoading = ref(false);
async function recollectMetadata() {
  if (readonly.value || metadataLoading.value) return;
  const id = selected.value, key = staticContextKey();
  metadataLoading.value = true;
  try {
    const result = await recollectObjectMetadata(id);
    if (id !== selected.value || key !== staticContextKey()) return;
    metadataDiff.value = result; metadataOpen.value = true;
  } catch (e) { if (id === selected.value && key === staticContextKey()) message.error(e instanceof Error ? e.message : String(e)); }
  finally { metadataLoading.value = false; }
}
watch(() => `${selected.value}:${staticContextKey()}`, () => { metadataOpen.value = false; metadataDiff.value = undefined; });
function openDefinition(isNew = false) { if (readonly.value || !isStaticReal()) return; newDefinition.value = isNew; definitionOpen.value = true; }
async function definitionSaved(result: { id: string; crId: string }) { const key = staticContextKey(); await loadDraftState(true); if (key !== staticContextKey()) return; await router.replace({ query: { ...route.query, type: result.id, view: 'draft' } }); if (key === staticContextKey()) { await cr.reload(); if (key === staticContextKey()) await workflow.reload(); } }
/** 显示状态：未发布的新对象类型按自身状态；有审核中的变更单 → 审核中（橙）；只有草稿 → 草稿中（灰）；其余已发布（灰） */
const workflow = useRealCrRecords();
const shownStatus = (t: (typeof TYPES)[number]) => {
  if (t.status !== '已发布') return t.status;
  if (isStaticReal()) {
    if (currentApiContext().ver) return t.status;
    const record = crsOf(t.id, workflow.rows.value).find((u) => ['草稿','审核中','已通过','已驳回'].includes(u.cr.status))?.cr;
    return record ? record.status === '草稿' ? '草稿中' : record.status === '已通过' ? '已通过待发布' : record.status : draftOf('object', t.id) ? '草稿中' : t.status;
  }
  if (crsOf(t.id).some((u) => u.cr.status === '审核中')) return '审核中';
  return draftOf('object', t.id) ? '草稿中' : '已发布';
};
const statusColor = (t: (typeof TYPES)[number]) => (shownStatus(t) === '审核中' ? 'warning' : 'default');
const pick = (id: string) => router.replace({ query: { ...route.query, type: id, view: undefined, p: undefined, lcpage: undefined, acpage: undefined, aupage: undefined } });
const dv = useDraftView('object', () => isStaticReal() ? selected.value : publishedCurrent.value.id);
const { draft, isDraft: draftIsDraft, editTip, readonly, reason, setView } = dv;
// real 模式先写库（发起 / 加入、新增属性、提交、放弃），见 parts/useStaticCr.ts
const cr = useStaticCr('object', () => ({ id: isStaticReal() ? selected.value : publishedCurrent.value.id, name: publishedCurrent.value.name }), dv, workflow.reload);
const newTarget = computed(() => !!cr.head.value?.targets.find((t) => t.kind === 'object' && t.id === selected.value)?.isNew);
const isDraft = computed(() => draftIsDraft.value || (isStaticReal() && !readonly.value && !!draft.value && newTarget.value));
const current = computed(() => isStaticReal() && isDraft.value ? { ...publishedCurrent.value, id: selected.value, ...draftFields(cr.items.value, 'def') } as (typeof TYPES)[number] : publishedCurrent.value);
const canEdit = computed(() => isDraft.value && (!isStaticReal() || cr.head.value?.status === '草稿' && !cr.loading.value && !cr.error.value));
const start = (opts: { join?: string; title?: string }) => cr.start(opts);
const changeOpen = ref(false);
const changeTarget = ref('');
const openChange = (name: string) => { changeTarget.value = name; changeOpen.value = true; };

// 左侧列表：搜索 + 简洁分页
const kw = ref('');
const LPAGE = 22;
const lpage = ref(1);
watch(kw, () => { lpage.value = 1; });
const visibleTypes = computed(() => {
  const added = isStaticReal() ? cr.head.value?.targets.filter((t) => t.kind === 'object' && t.isNew && !TYPES.some((o) => o.id === t.id)) ?? [] : [];
  return [...TYPES, ...added.map((t) => ({ id: t.id, name: t.id === selected.value ? current.value.name || t.name || '' : t.name || t.id, api: t.id === selected.value ? current.value.api || t.id : t.id, group: t.id === selected.value ? current.value.group : '', props: t.id === selected.value ? PROPS.value.length : 0, count: '—', status: '草稿' }))];
});
const filtered = computed(() => visibleTypes.value.filter((t) => !kw.value || t.name.includes(kw.value) || t.api.toLowerCase().includes(kw.value.toLowerCase())));
const objectPage = useStaticPage<(typeof MOCK_TYPES)[number]>(() => '/object-types', () => ({ kw: kw.value }), lpage, LPAGE);
watch(objectPage.items, (rows) => {
  if (!isStaticReal()) return;
  for (const row of rows) { const i = TYPES.findIndex((t) => t.id === row.id); if (i >= 0) TYPES[i] = row; else TYPES.push(row); }
  if (!firstId.value && rows.length) firstId.value = rows[0].id;
});
const list = computed(() => isStaticReal() ? objectPage.items.value : filtered.value.slice((lpage.value - 1) * LPAGE, lpage.value * LPAGE));
const listTotal = computed(() => isStaticReal() ? objectPage.total.value : filtered.value.length);

// —— 属性（每个对象类型各一份，src/mock/objectProps.ts）——
type P = ObjProp;
/** 草稿里新增的属性（只在本页内存里，提交审核时随草稿走），不改 mock */
const ADDED = reactive<Record<string, P[]>>({});
const confirm = useConfirmStore();
async function discardDraft() {
  const d = draft.value;
  if (!d) return;
  const id = current.value.id;
  const ok = await confirm.ask({ title: '放弃草稿', target: `${d.id} · ${current.value.name}`, okText: '放弃', danger: true,
    impact: '丢弃本对象在草稿里的全部改动（含已保存的），已发布版本不受影响。' });
  if (ok && current.value.id === id && await cr.discard()) delete ADDED[id];
}
const PROPS = computed<P[]>(() => {
  const base = (isStaticReal() ? realProps.value[current.value.id] : OBJECT_PROPS[current.value.id]) ?? [];
  if (isStaticReal()) return isDraft.value ? draftRows(base, cr.items.value, 'properties', (p) => p.api, (r, p) => ({ ...p, ...(r.after as P) })) : base;
  return [...(isDraft.value ? ADDED[current.value.id] ?? [] : []), ...base];
});
const addOpen = ref(false);
const addForm = reactive({ name: '', api: '', type: 'string', required: false });
const addErr = computed(() => (!addForm.name.trim() || !addForm.api.trim() ? '请填写名称和 API 名' : !/^[a-z][a-z0-9_]*$/.test(addForm.api.trim()) ? 'API 名只能用小写字母、数字和下划线，字母开头' : PROPS.value.some((p) => p.api === addForm.api.trim()) ? 'API 名已存在' : ''));
function openAdd() { Object.assign(addForm, { name: '', api: '', type: 'string', required: false }); addOpen.value = true; }
async function saveAdd() {
  if (addErr.value) return;
  const p: P = { api: addForm.api.trim(), name: addForm.name.trim(), type: addForm.type, group: '其他', source: '--', desc: addForm.required ? '必填' : '选填' };
  // real：properties 段整段替换，本次新增的属性一行（op=add，key=API 名）
  const ok = await cr.save('properties', [{ key: p.api, op: 'add', after: { ...p, required: addForm.required }, type: '属性', field: `${p.name} ${p.api}`, afterText: `${p.name}（${p.type}）` }]);
  if (!ok) return;
  if (!isStaticReal()) (ADDED[current.value.id] ??= []).unshift(p);
  addOpen.value = false; ppage.value = 1;
  message.success(`已新增属性“${addForm.name.trim()}”，保存到草稿 ${draft.value?.id ?? ''}`);
}
/** 草稿视图里的差异：按草稿单号从 changes.ts 的 diffs 取（门店 CR-0009、会员 CR-0012） */
const draftDiff = computed(() => (isStaticReal() ? { map: {}, other: cr.items.value.filter((r) => r.section !== 'properties').length } : draft.value ? draftPropDiff(draft.value.id, current.value.name, PROPS.value) : { map: {}, other: 0 }));
const DIFF = computed<Record<string, PropDiff>>(() => (isStaticReal() ? isDraft.value ? Object.fromEntries(cr.items.value.filter((r) => r.section === 'properties').map((r) => [r.key, { kind: r.op === 'add' ? '新增' : r.op === 'delete' ? '删除' : '修改', field: r.field ?? '属性', before: r.beforeText ?? '', after: r.afterText ?? String((r.after as P)?.name ?? ''), row: '--' } as PropDiff])) : {} : isDraft.value ? { ...draftDiff.value.map, ...Object.fromEntries((ADDED[current.value.id] ?? []).map((p) => [p.api, { kind: '新增', field: '属性', before: '', after: `${p.name}（${p.type}）`, row: '--' } as PropDiff])) } : {}));
const pkw = ref('');
const ppage = ref(1);
const PPAGE = 12;
watch(pkw, () => { ppage.value = 1; });
watch(() => [current.value.id, isDraft.value], () => { ppage.value = 1; mpage.value = 1; });
// 草稿视图里有改动的属性排在最前，第一页就能看到
const srcProps = computed(() => [...PROPS.value].sort((x, y) => Number(!!DIFF.value[y.api]) - Number(!!DIFF.value[x.api])));
const pfiltered = computed(() => srcProps.value.filter((p) => !pkw.value || p.name.includes(pkw.value) || p.api.includes(pkw.value)));
const propPage = useStaticPage<P>(() => selected.value ? `/object-types/${encodeURIComponent(selected.value)}/properties` : '', () => ({ kw: pkw.value, ...(isDraft.value && draft.value ? { crId: draft.value.id } : {}) }), ppage, PPAGE, () => tab.value === 'properties');
const prows = computed(() => isStaticReal() ? propPage.items.value : pfiltered.value.slice((ppage.value - 1) * PPAGE, ppage.value * PPAGE));
// 批量编辑（仅 real 草稿可编辑时）：勾选当前页属性，统一改展示层级 / 浏览标记，整批写入 properties 段（op 由 crSave 推断）
const bulkOn = computed(() => isStaticReal() && canEdit.value);
const bulkKeys = ref<string[]>([]);
const bulkOpen = ref(false);
type Tri = '' | 'yes' | 'no';
const BULK_FLAGS = [['searchable', '可搜索'], ['filterable', '可筛选'], ['sortable', '可排序'], ['listed', '列表显示'], ['sensitive', '敏感']] as const;
const bulkForm = reactive<{ visibility: string } & Record<(typeof BULK_FLAGS)[number][0], Tri>>({ visibility: '', searchable: '', filterable: '', sortable: '', listed: '', sensitive: '' });
const bulkDirty = computed(() => !!bulkForm.visibility || BULK_FLAGS.some(([k]) => !!bulkForm[k]));
watch(() => [current.value.id, isDraft.value, ppage.value, pkw.value, bulkOn.value], () => { bulkKeys.value = []; });
function openBulk() { Object.assign(bulkForm, { visibility: '', searchable: '', filterable: '', sortable: '', listed: '', sensitive: '' }); bulkOpen.value = true; }
async function saveBulk() {
  if (!bulkDirty.value || !bulkKeys.value.length) return;
  const picked = (prows.value as (P & Record<string, unknown>)[]).filter((p) => bulkKeys.value.includes(p.api));
  const changes: Record<string, unknown> = bulkForm.visibility ? { visibility: bulkForm.visibility } : {};
  for (const [k] of BULK_FLAGS) if (bulkForm[k]) changes[k] = bulkForm[k] === 'yes';
  const rows = picked.map((p) => {
    const after: Record<string, unknown> = { ...p, ...changes };
    // 与后端写回一致：隐藏不可筛选/排序/列表；敏感不可筛选；长文本不可筛选/排序
    if (after.visibility === 'hidden') Object.assign(after, { filterable: false, sortable: false, listed: false });
    if (after.sensitive) after.filterable = false;
    if (after.type === 'long_text') Object.assign(after, { filterable: false, sortable: false });
    return { key: p.api, before: p, after, type: '属性', field: `${p.name} ${p.api}`, afterText: `批量编辑：${Object.keys(changes).join('、')}` };
  });
  if (!await cr.save('properties', rows)) return;
  bulkOpen.value = false; bulkKeys.value = [];
  message.success(`已批量修改 ${rows.length} 个属性，保存到草稿 ${draft.value?.id ?? ''}`);
}
const propCols = [
  { title: '显示名', key: 'name', width: 200 },
  { title: 'API 名', dataIndex: 'api', width: 180 },
  { title: '类型', key: 'type', width: 140 },
  { title: '分组', dataIndex: 'group', width: 72 },
  { title: '标记', key: 'flags', width: 200 },
  { title: '源字段', dataIndex: 'source', width: 150 },
  { title: '说明', key: 'desc', dataIndex: 'desc', ellipsis: true },
];

// —— 关系 ——
// 与“关系类型”页共用 src/mock/links.ts；这里列当前对象类型作为源端的已发布关系
const realLinks = ref<(typeof ALL_LINKS[number] & { toName?: string })[]>([]);
let linksSeq = 0;
watch(() => JSON.stringify([selected.value, tab.value, staticContextKey()]), async () => {
  const n = ++linksSeq, key = staticContextKey(), id = selected.value;
  if (!isStaticReal() || tab.value !== 'links' || !id) return;
  realLinks.value = [];
  try { const rows = await fetchStaticDetail<typeof realLinks.value>(`/object-types/${encodeURIComponent(id)}/links`); if (n === linksSeq && key === staticContextKey() && id === selected.value) realLinks.value = rows; }
  catch (e) { if (n === linksSeq && key === staticContextKey() && id === selected.value) message.error(e instanceof Error ? e.message : String(e)); }
}, { immediate: true });
const LINKS = computed(() => (isStaticReal() ? realLinks.value : ALL_LINKS).filter((l) => l.from === current.value.id && l.status === '已发布').map((l) => ({
  name: l.name, target: isStaticReal() ? (l as { toName?: string }).toName || l.to : objName(l.to), card: l.card, key: l.key, reverse: l.reverse, isDefault: defaultLabel(l.id) !== '需用户指明',
})));
const linkTip = computed(() => {
  const gs = PATH_GROUPS.filter((g) => g.from === current.value.id);
  if (!gs.length) return '';
  return gs.map((g) => {
    const d = g.paths.find((p) => p.isDefault)!;
    return `${objName(g.from)}到${objName(g.to)}有 ${g.paths.length} 条路径，默认按“${d.links.map((x) => ALL_LINKS.find((l) => l.id === x)?.name).join(' → ')}”`;
  }).join('；') + '，其余需在问题里指明（发布校验 V13）';
});
const linkCols = [
  { title: '关系名', dataIndex: 'name', width: 140 },
  { title: '目标对象', dataIndex: 'target', width: 120 },
  { title: '基数', dataIndex: 'card', width: 80 },
  { title: '关联条件', dataIndex: 'key' },
  { title: '反向名称', dataIndex: 'reverse', width: 120 },
  { title: '问数默认路径', key: 'def', width: 120 },
];

// —— 数据源与映射 ——
const MAP_COLS = [
  { title: '属性', dataIndex: 'prop', width: 180 },
  { title: '源字段', dataIndex: 'col', width: 170 },
  { title: '源类型', dataIndex: 'srcType', width: 140 },
  { title: '转换', dataIndex: 'transform' },
];
const SRC_TYPE: Record<string, string> = { string: 'VARCHAR2(64)', enum: 'NUMBER(2)', timestamp: 'DATE', decimal: 'NUMBER(14,2)', integer: 'NUMBER(6)', boolean: 'CHAR(1)', json: 'CLOB' };
const OVERRIDE = Object.fromEntries(MAP_ROWS.map((r) => [r.prop, r]));
const srcTable = computed(() => isStaticReal() ? String((isDraft.value ? draftFields(cr.items.value, 'def').table : undefined) ?? realDetail.value.table ?? '--') : LINK_OBJECTS.find((o) => o.id === current.value.id)?.table ?? '--');
const pkProp = computed(() => PROPS.value.find((p) => p.pk));
const MAP_ALL = computed(() => PROPS.value.map((p) => (!isStaticReal() && current.value.id === 'order' && OVERRIDE[p.name]) || { prop: p.name, col: p.source, srcType: SRC_TYPE[p.type] ?? 'VARCHAR2(64)', transform: p.sensitive ? '敏感字段：脱敏显示' : '' }));
// —— 概览（按对象类型取数）——
const realDetail = ref<Partial<DefinitionValues>>({});
const detailError = ref('');
const runtime = ref<ObjectOverviewRuntime | null>(null);
let detailSeq = 0;
watch(() => [selected.value, staticContextKey()], async () => {
  const my = ++detailSeq, id = selected.value, key = staticContextKey(); realDetail.value = {}; detailError.value = ''; runtime.value = null;
  if (!isStaticReal() || !selected.value || newTarget.value) return;
  try { const result = await definitionDetail('object', id); if (my !== detailSeq || id !== selected.value || key !== staticContextKey()) return; realDetail.value = result.fields;
    realProps.value = { ...realProps.value, [id]: result.properties };
    // Per-resource complete metadata for permission/view children; never a paged property fragment.
    OBJECT_PROPS[id] = result.properties;
    const i = TYPES.findIndex((t) => t.id === id);
    const head = { id, name: result.fields.name, api: result.fields.api, group: result.fields.group, props: result.properties.length, count: i >= 0 ? TYPES[i].count : '—', status: String(result.fields.status ?? '已发布') };
    if (i >= 0) TYPES[i] = head; else TYPES.push(head);
  }
  catch (e) { if (my === detailSeq && id === selected.value && key === staticContextKey() && !newTarget.value) detailError.value = e instanceof Error ? e.message : String(e); }
}, { immediate: true });
let runtimeSeq = 0;
watch(() => JSON.stringify([selected.value, realDetail.value.name, tab.value, staticContextKey()]), async () => {
  const n = ++runtimeSeq, id = selected.value, key = staticContextKey();
  if (!isStaticReal() || tab.value !== 'overview' || !id || !realDetail.value.name) return;
  try { const data = await fetchObjectOverviewRuntime(id, String(realDetail.value.name), String(realDetail.value.api)); if (n === runtimeSeq && key === staticContextKey() && id === selected.value) runtime.value = data; }
  catch (e) { if (n === runtimeSeq && key === staticContextKey() && id === selected.value) detailError.value = e instanceof Error ? e.message : String(e); }
}, { immediate: true });
const meta = computed(() => isStaticReal() ? { domain: '', desc: '', conflict: '', lifecycle: '', sync: '', qs: [], cards: 0, qa30: 0, unused: 0, ...realDetail.value, ...(isDraft.value ? draftFields(cr.items.value, 'def') : {}) } as ReturnType<typeof objectMeta> : objectMeta(current.value.id, current.value.name, current.value.group));
const qo = computed(() => {
  if (!isStaticReal()) return qObjType(current.value.id);
  const fields = { ...realDetail.value, ...(isDraft.value ? draftFields(cr.items.value, 'def') : {}) };
  return { owner: String(fields.owner ?? '—'), source: String(fields.source ?? '—'), cutoff: String(fields.cutoff ?? '—') };
});
const sourceDb = computed(() => isStaticReal() ? String(realDetail.value.db ?? '—') : dbOf(qo.value?.source));
const titleProp = computed(() => PROPS.value.find((p) => p.title));
/** 当前版本 / 最近修改：最近一张已发布的变更单；没有就取迁移前最后一次修订 */
const lastRev = computed(() => {
  if (isStaticReal()) return { ver: app.viewVersion ?? app.ontologyVersion, by: '未提供', time: '未提供' };
  const cr = crsOf(current.value.id).find((u) => u.cr.release)?.cr;
  if (cr) return { ver: cr.release!, by: cr.owner, time: cr.updatedAt };
  const h = legacyRevs(current.value.id, current.value.name)[0];
  return { ver: h.version, by: h.author, time: h.time };
});
const usedMetrics = computed(() => isStaticReal() ? runtime.value?.metrics ?? [] : metricsOf(current.value.id).map((u) => u.m));
const actionCount = computed(() => isStaticReal() ? runtime.value?.actions ?? null : PERM_OBJECTS.find((o) => o.name === current.value.name)?.actions.length ?? 0);
const ruleCount = computed(() => isStaticReal() ? runtime.value?.rules ?? null : RULE_DEFS.filter((r) => r.objKind === current.value.id).length);
const checks = computed(() => isStaticReal() ? runtime.value?.checks ?? [] : QUALITY_CHECKS.filter((c) => c.typeKey === current.value.id));
const fullSync = computed(() => meta.value.sync.includes('全量'));
const freshFail = computed(() => checks.value.find((c) => c.kind === '新鲜度' && c.result === '失败'));
const lastSync = computed(() => (fullSync.value ? '2026-10-25 02:00' : '2026-10-25 15:00'));
const mpage = ref(1);
const MPAGE = 12;
const mappingPage = useStaticPage<(typeof MAP_ALL.value)[number]>(() => selected.value ? `/object-types/${encodeURIComponent(selected.value)}/mappings` : '', () => isDraft.value && draft.value ? { crId: draft.value.id } : {}, mpage, MPAGE, () => tab.value === 'mapping');
const mrows = computed(() => isStaticReal() ? mappingPage.items.value : MAP_ALL.value.slice((mpage.value - 1) * MPAGE, mpage.value * MPAGE));
watch(cr.items, () => { if (isStaticReal()) { void propPage.reload(); void mappingPage.reload(); } });
onBeforeUnmount(() => { ++detailSeq; ++runtimeSeq; ++linksSeq; });
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="readonly ? reason : ''"><a-button :disabled="readonly || !isStaticReal()" @click="importOpen = true">从数据源导入</a-button></a-tooltip>
    <a-tooltip :title="readonly ? reason : ''"><a-button :disabled="readonly || !isStaticReal()" @click="openDefinition(true)">新建对象类型</a-button></a-tooltip>
  </Teleport>

  <div class="ot">
    <div class="panel ot-list">
      <div class="ot-search"><a-input-search v-model:value="kw" placeholder="搜索名称或 API 名" /></div>
      <a-alert v-if="objectPage.error.value || detailError" type="error" :message="objectPage.error.value || detailError" show-icon />
      <div v-if="objectPage.loading.value" style="text-align: center; padding: 8px"><a-spin size="small" /></div>
      <ul class="ot-items" :aria-busy="objectPage.loading.value">
        <li v-for="t in list" :key="t.id" :class="{ active: t.id === current.id }" :title="`${t.name} · ${t.api} · ${t.group}`" @click="pick(t.id)">
          <div class="ot-row">
            <span class="t-14 ot-name">{{ t.name }}</span>
            <span class="t-12 c-3 ot-grp">{{ t.group }}</span>
            <span v-if="shownStatus(t) !== '已发布'" class="t-12 num" :class="shownStatus(t) === '审核中' ? 'c-warning' : 'c-3'">{{ shownStatus(t) }}</span>
          </div>
        </li>
      </ul>
      <div class="ot-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="listTotal" :size="LPAGE" unit="条" />
      </div>
    </div>

    <div v-if="selected && (!isStaticReal() || realDetail.name || newTarget)" class="panel ot-detail">
      <DraftBanner v-if="isDraft && draft && (!isStaticReal() || cr.head.value?.status === '草稿')" :draft="draft"
        @submit="cr.submit" @discard="discardDraft" />
      <div class="ot-head">
        <span class="t-16 fw-600 c-1">{{ current.name }}</span><a-tooltip v-if="isStaticReal()" :title="editTip"><a-button size="small" :disabled="!canEdit" @click="openDefinition()">编辑定义</a-button></a-tooltip>
        <span class="t-14 c-3 num">{{ current.api }} · {{ PROPS.length }} 个属性 · {{ current.count }} 条</span>
        <a-tag v-if="!(shownStatus(current) === '草稿中' && draft && !readonly)" :color="statusColor(current)">{{ shownStatus(current) }}</a-tag>
        <a-segmented v-if="draft && !readonly && !newTarget" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => setView(v)" />
        <span class="ot-sp" />
        <span class="t-12 c-3">数据截至 {{ qo?.cutoff ?? '--' }}</span>
        <a-tooltip title="暂未开放"><a-button disabled>预览数据</a-button></a-tooltip>
        <a-tooltip v-if="readonly" :title="reason"><a-button disabled>发起变更</a-button></a-tooltip>
        <a-button v-else-if="!draft" type="primary" @click="openChange(`${current.name}（${current.api}）`)">发起变更</a-button>
        <a-button v-else-if="!isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
      </div>
      <GroupedTabs v-model="tab" :groups="TAB_GROUPS" />

      <!-- 配置了 component 的页签：统一传 objectId / readonly -->
      <component :is="curTab.component" v-if="curTab?.component" :key="`${current.id}:${curTab.key}`" :object-id="current.id" :readonly="!canEdit" />

      <!-- 概览 -->
      <div v-else-if="tab === 'overview'" class="ot-body ot-ov">
        <a-alert v-if="detailError" type="error" :message="detailError" show-icon />
        <div class="ot-ov-left">
          <a-descriptions :column="2" bordered size="small">
            <a-descriptions-item label="显示名">{{ current.name }}</a-descriptions-item>
            <a-descriptions-item label="API 名"><span class="num">{{ current.api }}</span></a-descriptions-item>
            <a-descriptions-item label="业务域">{{ meta.domain }}</a-descriptions-item>
            <a-descriptions-item label="负责人">{{ qo?.owner ?? '--' }}</a-descriptions-item>
            <a-descriptions-item label="主键">{{ pkProp ? `${pkProp.name}（${pkProp.api}）` : '--' }}</a-descriptions-item>
            <a-descriptions-item label="标题属性">{{ titleProp?.name ?? '--' }}</a-descriptions-item>
            <a-descriptions-item label="数据源">{{ qo?.source ?? '--' }}（{{ sourceDb }}）</a-descriptions-item>
            <a-descriptions-item label="同步方式">{{ meta.sync }}</a-descriptions-item>
            <a-descriptions-item label="冲突策略">{{ meta.conflict }}</a-descriptions-item>
            <a-descriptions-item label="生命周期">{{ meta.lifecycle }}</a-descriptions-item>
            <a-descriptions-item label="当前版本"><span v-if="newTarget">草稿待发布</span><template v-else>随本体发布 <span class="num">{{ lastRev.ver }}</span> 生效</template></a-descriptions-item>
            <a-descriptions-item label="最近修改"><span v-if="isStaticReal()">请查看版本历史页签</span><template v-else>{{ lastRev.by }} · <span class="num">{{ lastRev.time }}</span></template></a-descriptions-item>
            <a-descriptions-item label="描述" :span="2">{{ meta.desc }}</a-descriptions-item>
          </a-descriptions>
          <div class="t-14 fw-600">问数示例问题</div>
          <ul class="ot-qs t-14 c-2">
            <li v-for="q in meta.qs" :key="q.q">
              <span>{{ q.q }}</span>
              <a-popover v-if="q.cert" placement="right">
                <template #content><pre class="ot-sq t-12">{{ q.query }}</pre></template>
                <a-tag class="ot-cert">已认证</a-tag>
              </a-popover>
              <span v-else class="t-12 c-3">未认证</span>
            </li>
          </ul>
        </div>
        <div class="ot-ov-right">
          <a-alert v-if="runtime?.errors.length" type="warning" :message="runtime.errors.join('；')" show-icon />
          <div class="t-14 fw-600">使用情况</div>
          <dl class="ot-stat">
            <dt class="t-14 c-3">关系</dt><dd class="t-14 num">{{ LINKS.length }} 条</dd>
            <dt class="t-14 c-3">引用的指标</dt><dd class="t-14 num">
              <template v-if="usedMetrics.length">
                <template v-for="(m, i) in usedMetrics.slice(0, 2)" :key="m.id"><template v-if="i">、</template><HoverCard kind="metric" :id="m.id">{{ m.name }}</HoverCard></template>
                <template v-if="usedMetrics.length > 2"> 等 {{ usedMetrics.length }} 个</template>
              </template>
              <span v-else class="c-3">{{ isStaticReal() && !runtime?.metrics ? '未提供' : '无' }}</span></dd>
            <dt class="t-14 c-3">动作类型</dt><dd class="t-14 num">{{ actionCount ?? '—' }} 个<span v-if="!isStaticReal() && actionCount" class="c-3">（暂未启用）</span></dd>
            <dt class="t-14 c-3">规则{{ isStaticReal() && currentApiContext().ver ? '（历史未提供）' : '' }}</dt><dd class="t-14 num">{{ ruleCount ?? '—' }} 条</dd>
            <dt class="t-14 c-3">看板卡片</dt><dd class="t-14 num">{{ meta.cards }} 张</dd>
            <dt class="t-14 c-3">近 30 天问数</dt><dd class="t-14 num">{{ meta.qa30.toLocaleString('zh-CN') }} 次</dd>
          </dl>
          <div class="t-14 fw-600">数据质量</div><span v-if="isStaticReal()" class="t-12 c-3">{{ !runtime?.checks ? '尚未加载当前用户可见检查' : !runtime.checks.length ? '当前用户未配置本对象检查' : '当前用户可见检查的最新结果' }}；运行结果为当前数据。</span>
          <dl class="ot-stat">
            <template v-for="c in checks" :key="c.id">
              <dt class="t-14 c-3">{{ c.kind }}</dt>
              <dd class="t-14" :class="c.result === '失败' ? 'c-error' : 'c-2'">
                <a-tooltip :title="`${c.rule} · 阈值 ${c.threshold} · 最近检查 ${c.lastRun}`">
                  {{ c.result }}<span v-if="c.result === '失败'"> · {{ c.actual }}</span>
                </a-tooltip>
              </dd>
            </template>
            <dt class="t-14 c-3">数据截至</dt><dd class="t-14 num c-2">{{ qo?.cutoff ?? '--' }}</dd>
          </dl>
        </div>
      </div>

      <!-- 属性 -->
      <div v-else-if="tab === 'properties'" class="ot-body">
        <a-alert v-if="propPage.error.value" type="error" :message="propPage.error.value" show-icon />
        <div class="ot-tool">
          <a-input-search v-model:value="pkw" placeholder="搜索属性名或 API 名" class="ot-tool-search" />
          <InfoTip :text="`主键 1 · 维度 ${srcProps.filter((p) => p.dim).length} · 敏感 ${srcProps.filter((p) => p.sensitive).length}`" />
          <span v-if="isDraft && draft && (!isStaticReal() || cr.head.value?.status === '草稿')" class="t-12 c-3">草稿 {{ draft.id }}：属性改动 {{ Object.keys(DIFF).length }} 处<template v-if="draftDiff.other">，其余 {{ draftDiff.other }} 处（对象类型、关系、映射、指标等）见<router-link :to="`/build/ontology/releases?cr=${draft.id}`">变更单</router-link></template></span>
          <span class="ot-sp" />
          <a-tooltip :title="!canEdit ? editTip : !isStaticReal() ? '演示模式不支持批量编辑' : !bulkKeys.length ? '先勾选要修改的属性' : ''"><a-button :disabled="!bulkOn || !bulkKeys.length" @click="openBulk">批量编辑{{ bulkKeys.length ? `（${bulkKeys.length}）` : '' }}</a-button></a-tooltip>
          <a-tooltip :title="editTip"><a-button :type="canEdit ? 'primary' : 'default'" :disabled="!canEdit" @click="openAdd">新增属性</a-button></a-tooltip>
        </div>
        <a-table :columns="propCols" :data-source="prows" :loading="propPage.loading.value" :pagination="false" size="small" row-key="api" :row-selection="bulkOn ? { selectedRowKeys: bulkKeys, onChange: (keys: (string | number)[]) => { bulkKeys = keys.map(String); } } : undefined">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <span class="c-1" :title="`${record.name} · ${record.api}`">{{ record.name }}</span>
              <a-tag v-if="DIFF[record.api]" class="ot-diff">{{ DIFF[record.api].kind }}</a-tag>
            </template>
            <template v-else-if="column.key === 'type'">{{ record.type }}<span v-if="record.enumName" class="c-3"> · {{ record.enumName }}</span></template>
            <template v-else-if="column.key === 'desc'">
              <a-tooltip v-if="DIFF[record.api]" :title="`${draft?.id} 第 ${DIFF[record.api].row} 行 · ${DIFF[record.api].field}：${DIFF[record.api].before ? `${DIFF[record.api].before} → ` : ''}${DIFF[record.api].after}`">
                <span class="ot-desc"><span v-if="DIFF[record.api].before" class="c-3 ot-del">{{ DIFF[record.api].before }}</span><template v-else>{{ record.desc }}；</template><span class="c-1">{{ DIFF[record.api].field.startsWith('取值') ? `${DIFF[record.api].field}：` : '' }}{{ DIFF[record.api].after }}</span></span>
              </a-tooltip>
              <span v-else class="ot-desc">{{ record.desc }}</span>
            </template>
            <template v-else-if="column.key === 'flags'">
              <span class="ot-flags">
                <a-tag v-if="record.pk">主键</a-tag>
                <a-tag v-if="record.title">标题</a-tag>
                <a-tag v-if="record.dim">维度</a-tag>
                <a-tag v-if="record.sensitive">敏感</a-tag>
              </span>
            </template>
          </template>
        </a-table>
        <div class="ot-pager">
          <AppPagination v-model:page="ppage" variant="simple" :total="isStaticReal() ? propPage.total.value : pfiltered.length" :size="PPAGE" unit="条" />
        </div>
      </div>

      <!-- 关系 -->
      <div v-else-if="tab === 'links'" class="ot-body">
        <div class="ot-tool">
          <InfoTip v-if="linkTip" :text="linkTip" />
          <span class="ot-sp" />
          <a-tooltip :title="editTip"><a-button :type="canEdit ? 'primary' : 'default'" :disabled="!canEdit" @click="router.push({ path: '/build/ontology/link-types', query: { from: current.id } })">新增关系</a-button></a-tooltip>
        </div>
        <a-table :columns="linkCols" :data-source="LINKS" :pagination="false" size="middle" row-key="name">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'def'">
              <template v-if="LINKS.filter((l) => l.target === record.target).length > 1">{{ record.isDefault ? '默认' : '需用户指明' }}</template>
              <span v-else class="c-3">--</span>
            </template>
          </template>
        </a-table>
      </div>

      <!-- 数据源与映射 -->
      <div v-else-if="tab === 'mapping'" class="ot-body">
        <a-alert v-if="mappingPage.error.value" type="error" :message="mappingPage.error.value" show-icon />
        <a-descriptions :column="4" bordered size="small">
          <a-descriptions-item label="数据源">{{ qo?.source ?? '--' }}</a-descriptions-item>
          <a-descriptions-item label="数据库">{{ sourceDb }}</a-descriptions-item>
          <a-descriptions-item label="源表">{{ srcTable }}</a-descriptions-item>
          <a-descriptions-item label="主键映射">{{ pkProp ? `${pkProp.source} → ${pkProp.name}` : '--' }}</a-descriptions-item>
          <a-descriptions-item label="同步方式">{{ meta.sync }}</a-descriptions-item>
          <a-descriptions-item label="增量字段">{{ isStaticReal() ? '—' : fullSync ? '--（全量同步）' : 'update_time' }}</a-descriptions-item>
          <a-descriptions-item label="最近同步">
            <span v-if="freshFail" class="c-error">{{ lastSync }} · 延迟（{{ freshFail.actual }}）</span>
            <span v-else class="num">{{ isStaticReal() ? '未提供同步运行记录' : `${lastSync} · 成功` }}</span>
          </a-descriptions-item>
          <a-descriptions-item label="数据截至"><span class="num">{{ qo?.cutoff ?? '--' }}</span></a-descriptions-item>
        </a-descriptions>
        <div class="ot-tool">
          <span class="t-14 fw-600">字段映射<InfoTip :text="`已映射 ${MAP_ALL.length} / 源表 ${MAP_ALL.length + meta.unused} 个字段，${meta.unused} 个未使用`" /></span>
          <span class="ot-sp" />
          <a-tooltip v-if="isStaticReal()" :title="readonly ? reason : '读取源表结构并比较当前映射，不自动修改定义'"><a-button :disabled="readonly" :loading="metadataLoading" @click="recollectMetadata">重新采集元数据</a-button></a-tooltip>
          <a-button v-else @click="message.success(`已提交重新采集 ${current.name} 源表元数据，完成后映射会自动刷新`)">重新采集元数据</a-button>
        </div>
        <a-table :columns="MAP_COLS" :data-source="mrows" :loading="mappingPage.loading.value" size="small" row-key="prop" :pagination="false" />
        <div class="ot-pager">
          <AppPagination v-model:page="mpage" variant="simple" :total="isStaticReal() ? mappingPage.total.value : MAP_ALL.length" :size="MPAGE" unit="条" />
        </div>
      </div>

      <div v-else class="ot-body ot-empty">
        <div class="t-14 c-3">{{ tabTitle }} · 待设计</div>
      </div>
    </div>
  </div>
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTarget" @ok="start" />
  <ObjectImportDrawer v-model:open="importOpen" @saved="definitionSaved" />
  <a-modal v-model:open="metadataOpen" title="源表元数据差异" :footer="null" :width="640">
    <template v-if="metadataDiff">
      <p class="t-14">{{ metadataDiff.schema }}.{{ metadataDiff.table }} · 仅比较已发布映射，定义修改仍需通过变更单。</p>
      <a-table size="small" :pagination="{ pageSize: 8, hideOnSinglePage: true }" row-key="name" :columns="[{ title: '变化', dataIndex: 'change' }, { title: '源列', dataIndex: 'name' }, { title: '源类型', dataIndex: 'type' }]" :data-source="[...metadataDiff.added.map(c => ({ ...c, change: '新增列' })), ...metadataDiff.deleted.map(name => ({ name, type: '—', change: '删除列' }))]" />
    </template>
  </a-modal>
  <DefinitionEditor v-model:open="definitionOpen" kind="object" :target-id="newDefinition ? undefined : current.id" :cr-id="newDefinition ? undefined : draft?.id" :new-target="!!cr.head.value?.targets.find((t) => t.kind === 'object' && t.id === current.id)?.isNew" @saved="definitionSaved" />
  <a-modal v-model:open="addOpen" :title="`新增属性 · ${current.name}`" ok-text="保存到草稿" :ok-button-props="{ disabled: !!addErr }" :width="480" @ok="saveAdd">
    <a-form layout="vertical">
      <a-form-item label="名称" required><a-input v-model:value="addForm.name" placeholder="如：配送方式" /></a-form-item>
      <a-form-item label="API 名" required :help="addForm.api && addErr ? addErr : undefined" :validate-status="addForm.api && addErr ? 'error' : undefined"><a-input v-model:value="addForm.api" placeholder="如：delivery_mode" class="num" /></a-form-item>
      <a-form-item label="类型"><a-select v-model:value="addForm.type" :options="['string', 'long_text', 'int', 'decimal', 'date', 'timestamp', 'boolean', 'enum'].map((v) => ({ value: v, label: v }))" /></a-form-item>
      <a-checkbox v-model:checked="addForm.required">必填</a-checkbox>
    </a-form>
  </a-modal>
  <a-modal v-model:open="bulkOpen" :title="`批量编辑属性 · ${current.name}（${bulkKeys.length} 个）`" ok-text="保存到草稿" :ok-button-props="{ disabled: !bulkDirty }" :width="480" @ok="saveBulk">
    <a-form layout="vertical">
      <a-form-item label="展示层级"><a-select v-model:value="bulkForm.visibility" :options="[{ value: '', label: '不改' }, { value: 'prominent', label: '突出' }, { value: 'normal', label: '普通' }, { value: 'hidden', label: '隐藏' }]" /></a-form-item>
      <a-form-item v-for="[k, label] in BULK_FLAGS" :key="k" :label="label"><a-radio-group v-model:value="bulkForm[k]" :options="[{ value: '', label: '不改' }, { value: 'yes', label: '是' }, { value: 'no', label: '否' }]" /></a-form-item>
      <a-alert type="info" message="隐藏属性不可筛选/排序/列表显示，敏感属性不可筛选，长文本不可筛选/排序；保存时自动修正。审核并整版发布后生效。" />
    </a-form>
  </a-modal>
</template>

<style scoped>
.ot { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.ot-list { width: 280px; flex: none; }
.ot-search { padding: 12px 12px 8px; flex: none; }
.ot-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; }
.ot-items li { padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.ot-items li:hover { background: var(--g3); }
.ot-items li.active { background: var(--brand-bg); }
.ot-items li.active .ot-name { color: var(--brand); }
.ot-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.ot-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ot-grp { flex-shrink: 0; }
.ot-foot { padding: 8px 12px 8px 16px; border-top: var(--border); flex: none; display: flex; align-items: center; justify-content: space-between; }
.ot-detail { flex: 1; }
.ot-head { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; flex: none; }
.ot-sp { flex: 1; }
.ot-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 12px; }
.ot-empty { align-items: center; justify-content: center; }
.ot-ov { flex-direction: row; gap: 16px; }
.ot-ov-left { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.ot-ov-right { width: 400px; flex: none; display: flex; flex-direction: column; gap: 8px; padding-left: 16px; border-left: var(--border); }
.ot-qs { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 4px; }
.ot-stat { display: grid; grid-template-columns: 96px 1fr; gap: 8px 12px; margin: 0 0 8px; }
.ot-stat dd { margin: 0; }
.ot-stat dt { font-weight: var(--fw-regular); }
.ot-tool { display: flex; align-items: center; gap: 12px; flex: none; }
.ot-tool-search { width: 280px; }
.ot-flags :deep(.ant-tag) { margin-inline-end: 4px; }
.ot-qs li { display: flex; align-items: center; gap: 8px; }
.ot-cert { margin-inline-end: 0; cursor: default; }
.ot-sq { margin: 0; font-family: Menlo, Consolas, monospace; }
.ot-diff { margin-inline: 8px 0; }
.ot-desc { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ot-del { text-decoration: line-through; margin-right: 8px; }
.ot-pager { display: flex; align-items: center; justify-content: flex-end; gap: 12px; }
</style>
