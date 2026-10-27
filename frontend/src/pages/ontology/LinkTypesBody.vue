<script setup lang="ts">
/**
 * 关系类型：左侧关系列表，右侧详情（定义 / 关联映射 / 问数路径 / 使用情况 / 版本历史），页头可切到“关系图”。
 * URL：?link= 选中关系，?tab= 页签，?mode=graph 关系图，?q= / ?src= / ?st= 列表筛选，?view=draft 草稿视图。
 * 草稿逻辑用公共的 useDraftView('link')（已发布只读，唯一入口“发起变更”）；新建关系经公共 start 登记到所选变更单。
 */
import InfoTip from '@/components/InfoTip.vue';
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import ObjRef from '@/components/ObjRef.vue';
import HoverCard from '@/components/HoverCard.vue';
import AppPagination from '@/components/AppPagination.vue';
import { useDrawer } from '@/composables/useDrawer';
import { useReadonly } from '@/composables/useReadonly';
import { useUnsavedGuard } from '@/composables/useUnsavedGuard';
import { useAppStore } from '@/stores/app';
import { CHANGE_REQUESTS, CURRENT_USER } from '@/mock/changes';
import {
  LINKS, LINK_OBJECTS, CARD_API, PATH_GROUPS, objById as mockObjById, objName as mockObjName,
  type LinkType, type LinkStatus, type Cardinality, type QaPath,
} from '@/mock/links';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import { useDraftView } from './parts/useDraftView';
import { useStaticCr, useRealCrRecords, draftFields, draftRows } from './parts/useStaticCr';
import { useStaticPage } from './parts/useStaticPage';
import { isStaticReal, startAllocatedTarget, staticContextKey, fetchStaticDetail } from '@/api/static';

const workflow = useRealCrRecords();
const route = useRoute();
const router = useRouter();
const app = useAppStore();
const { open: openDrawer } = useDrawer();
const { readonly, reason } = useReadonly();
const pad = (n: number) => String(n).padStart(2, '0');
const nowText = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };

const baseline = shallowRef<LinkType[]>(isStaticReal() ? [] : LINKS);
const links = reactive<LinkType[]>(baseline.value.map((l) => ({ ...l })));
const firstId = ref('');
const selectedId = computed(() => typeof route.query.link === 'string' ? route.query.link : firstId.value);
interface LinkProperty { api: string; name: string; type: string }
const catalog = shallowRef<{ objects: typeof LINK_OBJECTS; properties: Record<string, LinkProperty[]>; links: LinkType[]; paths: typeof PATH_GROUPS }>({ objects: [], properties: {}, links: [], paths: [] });
const catalogLoading = ref(false);
const objectRows = computed(() => isStaticReal() ? catalog.value.objects : LINK_OBJECTS);
const pathRows = computed(() => isStaticReal() ? catalog.value.paths : PATH_GROUPS);
const objName = (id: string) => isStaticReal() ? objectRows.value.find((o) => o.id === id)?.name ?? id : mockObjName(id);
const objById = (id: string) => isStaticReal() ? objectRows.value.find((o) => o.id === id) : mockObjById(id);
/** 查看历史版本时只列出该版本已发布的关系 */
const visible = computed(() => {
  const ver = app.viewVersion;
  if (!readonly.value || !ver) return links;
  return links.filter((l) => l.status === '已发布' && !!l.since && l.since <= ver);
});
const visibleIds = computed(() => new Set((isStaticReal() ? catalog.value.links : visible.value).map((l) => l.id)));

// —— 列表筛选（写进 URL）——
const q = (k: string) => (route.query[k] as string) || '';
const setQ = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const kw = computed({ get: () => q('q'), set: (v) => setQ({ q: v || undefined }) });
const src = computed({ get: () => q('src'), set: (v) => setQ({ src: v || undefined }) });
const st = computed({ get: () => q('st'), set: (v) => setQ({ st: v || undefined }) });
const SRC_OPTIONS = computed(() => [
  { value: '', label: '全部源对象' },
  ...objectRows.value.filter((o) => (isStaticReal() ? catalog.value.links : visible.value).some((l) => l.from === o.id)).map((o) => ({ value: o.id, label: o.name })),
]);
const ST_OPTIONS = [{ value: '', label: '全部状态' }, { value: '已发布', label: '已发布' }, { value: '草稿', label: '草稿' }, { value: '已驳回', label: '已驳回' }];
const filtered = computed(() => visible.value.filter((l) =>
  (!src.value || l.from === src.value) && (!st.value || l.status === st.value)
  && (!kw.value || l.name.includes(kw.value) || apiOf(l).includes(kw.value) || objName(l.from).includes(kw.value) || objName(l.to).includes(kw.value))));
const LPAGE = isStaticReal() ? 10 : 18;
const lpage = ref(1);
watch([kw, src, st], () => { lpage.value = 1; });
const linkPage = useStaticPage<LinkType>(() => '/link-types', () => ({ q: kw.value, src: src.value, st: st.value }), lpage, LPAGE);
watch(linkPage.items, (rows) => { if (isStaticReal() && !firstId.value && rows.length) firstId.value = rows[0].id; });
const list = computed(() => isStaticReal() ? linkPage.items.value : filtered.value.slice((lpage.value - 1) * LPAGE, lpage.value * LPAGE));
const listTotal = computed(() => isStaticReal() ? linkPage.total.value : filtered.value.length);

const apiOf = (l: LinkType) => l.api ?? l.id;
const cur = computed(() => isStaticReal() ? links.find((l) => l.id === selectedId.value) : visible.value.find((l) => l.id === route.query.link) ?? visible.value[0]);
// 选中项不在当前页时翻到它所在的页
watch(() => cur.value?.id, (id) => {
  if (isStaticReal()) return;
  const i = filtered.value.findIndex((l) => l.id === id);
  if (i >= 0) lpage.value = Math.floor(i / LPAGE) + 1;
}, { immediate: true });

const mode = computed({
  get: () => (route.query.mode === 'graph' ? 'graph' : 'list'),
  set: (v) => guard(() => setQ({ mode: v === 'graph' ? 'graph' : undefined })),
});
const tab = computed({
  get: () => (route.query.tab as string) || 'def',
  set: (v) => guard(() => { editing.value = false; setQ({ tab: v }); }),
});
/** 切换关系时清掉草稿视图和两层抽屉 */
const pick = (id: string) => guard(() => {
  editing.value = false;
  router.replace({ query: { ...route.query, link: id, view: undefined, drawer: undefined, drawer2: undefined } });
});

// —— 草稿（公共 useDraftView，kind = link）——
const targetId = () => isStaticReal() ? selectedId.value : cur.value?.id ?? '';
const dv = useDraftView('link', targetId);
const { draft, setView } = dv;
// real 模式先写库（发起 / 加入、保存到草稿、提交、放弃），见 parts/useStaticCr.ts
const cr = useStaticCr('link', () => ({ id: targetId(), name: cur.value?.name, isNew: cur.value?.status === '草稿' }), dv, workflow.reload);
/** 只存在于草稿里的关系（新增的），直接看草稿 */
const draftOnly = computed(() => cur.value?.status === '草稿');
const isDraft = computed(() => dv.isDraft.value || (!readonly.value && !!draft.value && draftOnly.value));
const canEdit = computed(() => isDraft.value && (!isStaticReal() || cr.head.value?.status === '草稿' && !cr.loading.value && !cr.error.value));
const editTip = computed(() => (isDraft.value ? '' : !readonly.value && cur.value?.status === '已驳回' ? '变更单已驳回，请重新发起变更' : dv.editTip.value));
const changeOpen = ref(false);
const changeTarget = ref('');
const creating = ref(false);
const pendingNewCr = ref<string>();
const openChange = (name: string, isNew = false) => { if (readonly.value) return; creating.value = isNew; changeTarget.value = name; changeOpen.value = true; };
async function start(opts: { join?: string; title?: string; desc?: string }) {
  if (readonly.value) return;
  if (!creating.value) { await cr.start(opts); return; }
  let nid: string, id: string | undefined, by: string, at: string;
  if (isStaticReal()) {
    const key = staticContextKey();
    try {
      const result = await startAllocatedTarget('link', opts, '未命名关系', pendingNewCr.value);
      if (key !== staticContextKey()) return;
      nid = result.target.id; id = result.crId; by = result.owner; at = result.updatedAt;
      dv.start({ join: id, target: nid }); pendingNewCr.value = undefined; await workflow.reload(); if (key !== staticContextKey()) return;
    } catch (e) { if (key !== staticContextKey()) return; pendingNewCr.value = (e as { crId?: string }).crId ?? pendingNewCr.value; message.error(`${e instanceof Error ? e.message : String(e)}${pendingNewCr.value ? `（草稿 ${pendingNewCr.value} 已保留，可重试）` : ''}`); return; }
  } else {
    const n = links.filter((l) => l.id.startsWith('new_link_')).length + 1;
    nid = `new_link_${n}`; id = await cr.start(opts, { id: nid, isNew: true, name: '未命名关系' }); by = CURRENT_USER; at = nowText();
  }
  if (!id) return;
  const from = src.value || cur.value?.from || (isStaticReal() ? '' : 'order');
  const nl: LinkType = {
    id: nid, api: '', name: '未命名关系', from, to: '', card: 'N : 1', key: '', fromProp: '', toProp: '', join: '',
    reverse: '', nullable: false, desc: '', status: '草稿', cr: id, owner: by, usage: [],
    history: [{ ver: '—', cr: id, status: '草稿', by, at, change: '新增关系（待填写）' }],
  };
  links.unshift(nl);
  router.replace({ query: { ...route.query, link: nl.id, tab: 'def', mode: undefined, view: 'draft', drawer: undefined, drawer2: undefined, src: undefined, st: undefined, q: undefined } })
    .then(async () => { if (isStaticReal()) { await nextTick(); await cr.reload(); } if (canEdit.value) beginEdit(); });
}
const rejectedCR = computed(() => (cur.value?.status === '已驳回' ? (isStaticReal() ? workflow.rows.value : CHANGE_REQUESTS).find((c) => c.id === cur.value?.cr) : undefined));

// —— 定义：草稿里可编辑 ——
interface Form { name: string; api: string; from: string; to: string; card: Cardinality; fromProp: string; toProp: string; via: string; reverse: string; nullable: boolean; desc: string }
const editing = ref(false);
const form = reactive<Form>({ name: '', api: '', from: '', to: '', card: 'N : 1', fromProp: '', toProp: '', via: '', reverse: '', nullable: false, desc: '' });
const snap = ref('');
function beginEdit() {
  const l = cur.value;
  if (!l) return;
  Object.assign(form, { name: l.name, api: apiOf(l), from: l.from, to: l.to, card: l.card, fromProp: l.fromProp, toProp: l.toProp, via: l.via ?? '', reverse: l.reverse, nullable: l.nullable, desc: l.desc });
  snap.value = JSON.stringify(form);
  editing.value = true;
}
const dirty = computed(() => editing.value && JSON.stringify(form) !== snap.value);
const guard = useUnsavedGuard(dirty);
/** 定义表单字段 → 改动列表「字段」列 */
const FIELD_LABEL: Record<keyof Form, string> = { name: '名称', api: 'API 名', from: '源对象', to: '目标对象', card: '基数', fromProp: '本端属性', toProp: '对端属性', via: '中间表', reverse: '反向名称', nullable: '可为空', desc: '说明' };
const saving = ref(false);
async function save() {
  const l = cur.value;
  if (!l || !canEdit.value || saving.value || formError.value) return;
  const key = form.card === 'N : M' ? `经 ${form.via || '（未选择）'} 中间表` : form.to ? `${form.fromProp} = ${form.to}.${form.toProp}` : '';
  // real：def 段，一个字段一行（同字段再次保存保留最早的 before，改回原值的行去掉）
  const was = JSON.parse(snap.value) as Form;
  const changes = (Object.keys(FIELD_LABEL) as (keyof Form)[])
    .filter((k) => l.status === '草稿' || (k !== 'api' && JSON.stringify(form[k]) !== JSON.stringify(was[k])))
    .map((k) => ({ key: k, before: was[k], after: form[k], type: '关系', field: FIELD_LABEL[k] }));
  const context = staticContextKey();
  saving.value = true;
  let saved = false;
  try { saved = await cr.save('def', changes); } finally { saving.value = false; }
  if (!saved || context !== staticContextKey() || l.id !== cur.value?.id) return;
  if (!isStaticReal()) Object.assign(l, { name: form.name, from: form.from, to: form.to, card: form.card, fromProp: form.fromProp, toProp: form.toProp, via: form.via || undefined, reverse: form.reverse, nullable: form.nullable, desc: form.desc, key });
  if (!isStaticReal() && l.status === '草稿') l.api = form.api;
  editing.value = false;
  message.success(`已保存到草稿 ${draft.value?.id ?? ''}`);
}
const cancelEdit = () => guard(() => { editing.value = false; });
const CARDS: Cardinality[] = ['N : 1', '1 : N', '1 : 1', 'N : M'];
const CARD_OPTIONS = CARDS.map((k) => ({ value: k, label: `${k}（${CARD_API[k]}）` }));
const OBJ_OPTIONS = computed(() => objectRows.value.map((o) => ({ value: o.id, label: `${o.name}（${o.api}）${o.draft ? ' · 同单草稿' : ''}` })));
const VIA_OPTIONS = computed(() => {
  const options = [...OBJ_OPTIONS.value];
  const previous = cur.value?.via;
  if (previous && !options.some(option => option.value === previous)) options.push({ value: previous, label: `${previous} · 原有中间表` });
  return options;
});
const propertyOptions = (objectId: string) => (catalog.value.properties[objectId] ?? []).map(property => ({ value: property.api, label: `${property.name}（${property.api} · ${property.type}）` }));
function validateRelation(value: Form, isNew = false) {
  if (!isStaticReal()) return '';
  if (catalogLoading.value) return '对象与属性目录正在加载';
  if (catalogError.value) return '对象与属性目录加载失败，请重试';
  if (!value.name.trim()) return '请填写关系名称';
  if (isNew && !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(value.api)) return 'API名需字母开头，仅含字母、数字和下划线，最多64位';
  if (!objectRows.value.some(object => object.id === value.from) || !objectRows.value.some(object => object.id === value.to)) return '请选择关系两端的对象类型';
  if (value.card === 'N : M') return value.via && VIA_OPTIONS.value.some(option => option.value === value.via) ? '' : '请选择中间对象';
  const source = catalog.value.properties[value.from]?.find(property => property.api === value.fromProp);
  const target = catalog.value.properties[value.to]?.find(property => property.api === value.toProp);
  if (!source || !target) return '请选择关系两端的真实属性';
  const type = (value: string) => value === 'int' ? 'integer' : value;
  return type(source.type) === type(target.type) ? '' : '关系两端属性类型须一致';
}
const formError = computed(() => validateRelation(form, cur.value?.status === '草稿'));

const statusColor = (s: LinkStatus) => (s === '草稿' ? 'default' : s === '已驳回' ? 'error' : 'default');
const statusClass = (s: LinkStatus) => (s === '草稿' ? 'c-3' : s === '已驳回' ? 'c-error' : 'c-3');
/** 能解析出抽屉的对象类型（储值卡只在草稿里，没有对象详情） */
const canRef = (id: string) => !!id && !(objById(id) as { draft?: boolean } | undefined)?.draft;
const groupsOf = (id: string) => pathRows.value
  .map((g) => ({ ...g, paths: g.paths.filter((p) => p.links.every((x) => visibleIds.value.has(x))) }))
  .filter((g) => g.paths.length > 1 && g.paths.some((p) => p.links.includes(id)));
const curGroups = computed(() => (cur.value ? groupsOf(cur.value.id) : []));

const CHECKS = computed(() => {
  const l = cur.value;
  if (!l) return;
  const rows: { code: string; name: string; result: '通过' | '警告' | '阻断'; note: string }[] = [];
  const error = isStaticReal() ? validateRelation({ ...l, api: apiOf(l), via: l.via ?? '' }, l.status === '草稿') : '';
  rows.push(error ? { code: 'V3', name: '两端对象', result: catalogLoading.value ? '警告' : '阻断', note: error } : !l.to
    ? { code: 'V3', name: '两端对象', result: '阻断', note: '还没有选择目标对象' }
    : l.card === 'N : M' && !l.via
      ? { code: 'V3', name: '两端对象', result: '阻断', note: '多对多需要指定中间对象' }
      : { code: 'V3', name: '两端对象', result: '通过', note: `${objName(l.from)} 与 ${objName(l.to)} 都存在，关联键类型一致${l.via ? `；经中间表 ${l.via}` : ''}` });
  if (l.status === '已发布') rows.push({ code: 'V7', name: '已发布 API 名', result: '通过', note: `${apiOf(l)} 已发布，不可删除或改名` });
  rows.push(l.desc.length >= 8 ? { code: 'V11', name: '自然语言描述', result: '通过', note: '问数用它理解这条关系' } : { code: 'V11', name: '自然语言描述', result: '警告', note: '描述过短，问数可能选错路径' });
  const g = groupsOf(l.id)[0];
  rows.push(g ? { code: 'V13', name: '多路径默认', result: '通过', note: g.check } : l.id === 'store_manager'
    ? { code: 'V13', name: '多路径默认', result: '警告', note: '与“所属门店”反向连接同一对对象，重新提交时需指定默认路径' }
    : { code: 'V13', name: '多路径默认', result: '通过', note: '两端之间只有这一条路径' });
  return rows;
});
const checkClass = (r: string) => (r === '阻断' ? 'c-error' : r === '警告' ? 'c-warning' : 'c-3');

const DEF_ITEMS = computed(() => {
  const l = cur.value;
  if (!l) return;
  return [
    { k: '基数', v: `${l.card}（${CARD_API[l.card]}）` },
    { k: '允许为空', v: l.nullable ? '是：源端记录可以没有关联的目标，没有的不参与关联' : '否：源端每条记录都必须关联到目标' },
    { k: '反向名称', v: l.reverse ? `${l.reverse}（从${objName(l.to)}看${objName(l.from)}）` : '' },
    { k: '中间对象', v: l.via ?? '--' },
    { k: '负责人', v: l.owner },
    { k: '生效版本', v: l.since ? `随本体发布 ${l.since} 生效${l.cr ? `（${l.cr}）` : ''}` : l.cr ? `未发布 · ${l.cr} ${l.status}` : '未发布' },
  ];
});

// —— 关联映射 ——
const fmtInt = (n: number) => n.toLocaleString('en-US');
const hitWarn = (h: number) => h < 99.5;
const orphanCols = [{ title: '源端关联键', dataIndex: 'key', width: 200 }, { title: '记录数', key: 'count', width: 120, align: 'right' as const }, { title: '原因', dataIndex: 'reason' }];

// —— 问数路径 ——
const linkName = (id: string) => (isStaticReal() ? catalog.value.links : links).find((l) => l.id === id)?.name ?? id;
const chain = (p: QaPath) => {
  const steps: { obj: string; via?: string }[] = [];
  p.links.forEach((id, i) => {
    const l = (isStaticReal() ? catalog.value.links : links).find((x) => x.id === id);
    if (!l) return;
    if (i === 0) steps.push({ obj: l.from });
    steps.push({ obj: l.to, via: l.name });
  });
  return steps;
};
const curPathCount = computed(() => curGroups.value.reduce((n, g) => n + g.paths.length, 0));

// —— 使用情况 / 版本历史 ——
const up = ref(1), hpage = ref(1);
watch(() => cur.value?.id, () => { up.value = 1; hpage.value = 1; });
const historySize = ref(10);
watch(historySize, () => { hpage.value = 1; });
const historyPage = useStaticPage<LinkType['history'][number]>(() => targetId() ? `/link-types/${encodeURIComponent(targetId())}/history` : '', () => ({}), hpage, historySize, () => tab.value === 'history' && !draftOnly.value);
const usageCols = [{ title: '类型', dataIndex: 'type', width: 96 }, { title: '名称', key: 'name', width: 320 }, { title: '使用方式', dataIndex: 'note' }];
const usageSize = ref(10);
watch(usageSize, () => { up.value = 1; });
const usagePage = useStaticPage<LinkType['usage'][number]>(() => targetId() ? `/link-types/${encodeURIComponent(targetId())}/usage` : '', () => ({}), up, usageSize, () => tab.value === 'usage' && !draftOnly.value);
const usageSummary = computed(() => {
  if (isStaticReal()) return `共 ${usagePage.total.value} 项使用`;
  const m = new Map<string, number>();
  (cur.value?.usage ?? []).forEach((u) => m.set(u.type, (m.get(u.type) ?? 0) + 1));
  return [...m].map(([k, v]) => `${k} ${v}`).join(' · ');
});
const histCols = [
  { title: '本体发布', dataIndex: 'ver', width: 120 },
  { title: '变更单', key: 'cr', width: 120 },
  { title: '状态', key: 'status', width: 96 },
  { title: '变更说明', dataIndex: 'change' },
  { title: '操作人', dataIndex: 'by', width: 96 },
  { title: '时间', dataIndex: 'at', width: 168 },
];
const crLink = (id: string) => router.push({ path: '/build/ontology/releases', query: { cr: id } });

// —— 关系图 ——
const POS: Record<string, [number, number]> = {
  promotion: [0.25, 0.1], product: [0.5, 0.12], category: [0.69, 0.05], supplier: [0.9, 0.2],
  refund_line: [0.07, 0.33], order_line: [0.27, 0.33], inventory: [0.73, 0.34],
  refund: [0.25, 0.52], order: [0.5, 0.5], warehouse: [0.92, 0.55], shipment: [0.72, 0.64],
  member_level: [0.07, 0.6], member: [0.25, 0.72], stored_card: [0.07, 0.86],
  store: [0.5, 0.8], employee: [0.86, 0.84], province: [0.3, 0.94], region: [0.7, 0.94],
};
const NW = 104;
const NH = 36;
const gbox = ref<HTMLElement | null>(null);
const gw = ref(1200);
const gh = ref(720);
let ro: ResizeObserver | null = null;
onMounted(() => {
  ro = new ResizeObserver(([e]) => { gw.value = e.contentRect.width; gh.value = e.contentRect.height; });
  watch(gbox, (el, old) => { if (old) ro?.unobserve(old); if (el) ro?.observe(el); }, { immediate: true });
});
onBeforeUnmount(() => ro?.disconnect());
const PAD = 40;
const positions = computed<Record<string, [number, number]>>(() => {
  if (!isStaticReal()) return POS;
  const cols = Math.max(1, Math.ceil(Math.sqrt(objectRows.value.length))), rows = Math.max(1, Math.ceil(objectRows.value.length / cols));
  return Object.fromEntries(objectRows.value.map((o, i) => [o.id, [(i % cols + 0.5) / cols, (Math.floor(i / cols) + 0.5) / rows]]));
});
const pt = (id: string) => { const [fx, fy] = positions.value[id] ?? [0.5, 0.5]; return { x: PAD + fx * (gw.value - PAD * 2), y: PAD / 2 + fy * (gh.value - PAD) }; };
const graphLinks = computed(() => (isStaticReal() ? catalog.value.links.filter((l) => (!src.value || l.from === src.value) && (!st.value || l.status === st.value) && (!kw.value || l.name.includes(kw.value) || apiOf(l).includes(kw.value) || objName(l.from).includes(kw.value) || objName(l.to).includes(kw.value))) : filtered.value).filter((l) => l.to && positions.value[l.from] && positions.value[l.to]));
const graphNodes = computed(() => {
  const used = new Set<string>();
  (isStaticReal() ? graphLinks.value : visible.value).forEach((l) => { used.add(l.from); if (l.to) used.add(l.to); });
  return objectRows.value.filter((o) => used.has(o.id) && positions.value[o.id]).map((o) => ({ ...o, ...pt(o.id) }));
});
const trim = (from: { x: number; y: number }, to: { x: number; y: number }) => {
  const dx = to.x - from.x; const dy = to.y - from.y;
  const t = Math.min(dx ? (NW / 2 + 4) / Math.abs(dx) : Infinity, dy ? (NH / 2 + 4) / Math.abs(dy) : Infinity);
  return { x: from.x + dx * t, y: from.y + dy * t };
};
const edges = computed(() => {
  const pairCount = new Map<string, number>();
  const pairIdx = new Map<string, number>();
  const pk = (l: LinkType) => [l.from, l.to].sort().join('|');
  graphLinks.value.forEach((l) => pairCount.set(pk(l), (pairCount.get(pk(l)) ?? 0) + 1));
  return graphLinks.value.map((l) => {
    const k = pk(l);
    const i = pairIdx.get(k) ?? 0; pairIdx.set(k, i + 1);
    const n = pairCount.get(k)!;
    const a = pt(l.from); const b = pt(l.to);
    if (l.from === l.to) {
      const d = `M ${a.x - 20} ${a.y - NH / 2} C ${a.x - 40} ${a.y - 70}, ${a.x + 40} ${a.y - 70}, ${a.x + 20} ${a.y - NH / 2 - 2}`;
      return { l, d, lx: a.x, ly: a.y - 64 };
    }
    // 同一对对象之间多条关系时弯成弧线
    const sign = l.from < l.to ? 1 : -1;
    const off = (i - (n - 1) / 2) * 56 * sign;
    const mx = (a.x + b.x) / 2; const my = (a.y + b.y) / 2;
    const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    const c = { x: mx - ((b.y - a.y) / len) * off, y: my + ((b.x - a.x) / len) * off };
    const s = trim(a, c); const e = trim(b, c);
    return { l, d: `M ${s.x} ${s.y} Q ${c.x} ${c.y} ${e.x} ${e.y}`, lx: 0.25 * s.x + 0.5 * c.x + 0.25 * e.x, ly: 0.25 * s.y + 0.5 * c.y + 0.25 * e.y };
  });
});
const selEdge = computed(() => edges.value.find((e) => e.l.id === cur.value?.id));
const isEnd = (id: string) => cur.value && (cur.value.from === id || cur.value.to === id);
const hoverEdge = ref('');
const clickNode = (id: string) => { if (canRef(id)) openDrawer('object-type', id); };
const toDetail = () => guard(() => setQ({ mode: undefined }));

// Rebuild from the published baseline on every draft/view transition, never from a previous CR.
watch([baseline, cr.items, cr.head, isDraft, cr.loading], () => {
  if (!isStaticReal()) return;
  const base = structuredClone(baseline.value);
  const target = cr.head.value?.targets.find((t) => t.kind === 'link' && t.id === targetId());
  if (target?.isNew && !base.some((l) => l.id === target.id)) base.push({ id: target.id, api: '', name: target.name ?? '', from: '', to: '', card: 'N : 1', key: '', fromProp: '', toProp: '', join: '', reverse: '', nullable: false, desc: '', status: '草稿', cr: cr.head.value!.id, owner: cr.head.value!.owner, usage: [], history: [] });
  if (isDraft.value) {
    const item = base.find((l) => l.id === targetId());
    if (item) {
      Object.assign(item, draftFields(cr.items.value, 'def'));
      item.key = item.card === 'N : M' ? `经 ${item.via ?? ''} 中间表` : item.to ? `${item.fromProp} = ${item.to}.${item.toProp}` : '';
    }
  }
  links.splice(0, links.length, ...base);
});

let definitionSeq = 0, catalogSeq = 0;
const definitionError = ref(''), catalogError = ref('');
const catalogKey = () => JSON.stringify([staticContextKey(), isDraft.value ? cr.head.value?.id : '', isDraft.value ? cr.head.value?.lockVersion : 0, selectedId.value]);
async function loadCatalog() {
  const n = ++catalogSeq, key = catalogKey(); catalogError.value = '';
  if (!isStaticReal()) return;
  catalogLoading.value = true;
  catalog.value = { objects: [], properties: {}, links: [], paths: [] };
  const crId = isDraft.value && cr.head.value?.targets.some(target => target.kind === 'link' && target.id === selectedId.value) ? cr.head.value.id : undefined;
  try {
    const data = await fetchStaticDetail<typeof catalog.value>('/link-types/catalog', crId ? { crId, linkId: selectedId.value } : {});
    if (n === catalogSeq && key === catalogKey()) catalog.value = data;
  } catch (e) { if (n === catalogSeq && key === catalogKey()) catalogError.value = e instanceof Error ? e.message : String(e); }
  finally { if (n === catalogSeq && key === catalogKey()) catalogLoading.value = false; }
}
watch(catalogKey, () => void loadCatalog(), { immediate: true });
watch(() => JSON.stringify([selectedId.value, staticContextKey()]), async () => {
  const n = ++definitionSeq, key = staticContextKey(), id = selectedId.value; definitionError.value = '';
  if (!isStaticReal() || !id || cr.head.value?.targets.some((t) => t.kind === 'link' && t.id === id && t.isNew)) return;
  try { const data = await fetchStaticDetail<LinkType>(`/link-types/${encodeURIComponent(id)}`); if (n === definitionSeq && key === staticContextKey() && id === selectedId.value) baseline.value = [...baseline.value.filter((l) => l.id !== id), data]; }
  catch (e) { if (n === definitionSeq && key === staticContextKey() && id === selectedId.value && !cr.head.value?.targets.some((t) => t.kind === 'link' && t.id === id && t.isNew)) definitionError.value = e instanceof Error ? e.message : String(e); }
}, { immediate: true });
onBeforeUnmount(() => { ++definitionSeq; ++catalogSeq; });

</script>

<template>
  <Teleport defer to="#page-extra">
    <a-segmented v-model:value="mode" :options="[{ label: '列表', value: 'list' }, { label: '关系图', value: 'graph' }]" />
    <a-tooltip :title="readonly ? reason : ''">
      <a-button :disabled="readonly" @click="openChange('新建关系类型', true)">新建关系类型</a-button>
    </a-tooltip>
  </Teleport>

  <div class="lt">
    <div class="panel lt-list">
      <div class="lt-filter">
        <a-input-search :value="kw" placeholder="搜索关系名、API 名或对象" allow-clear @change="(e: any) => (kw = e.target.value)" />
        <div class="lt-selects">
          <a-select v-model:value="src" :options="SRC_OPTIONS" size="small" class="lt-sel" />
          <a-select v-model:value="st" :options="ST_OPTIONS" size="small" class="lt-sel" />
        </div>
      </div>
      <a-alert v-if="linkPage.error.value || definitionError || catalogError" type="error" :message="linkPage.error.value || definitionError || catalogError" show-icon><template v-if="catalogError" #action><a-button size="small" @click="loadCatalog">重试目录</a-button></template></a-alert>
      <div v-if="linkPage.loading.value" style="text-align: center; padding: 8px"><a-spin size="small" /></div>
      <ul class="lt-items" :aria-busy="linkPage.loading.value">
        <li v-for="l in list" :key="l.id" :class="{ active: l.id === cur?.id }" :title="`${l.name} · ${objName(l.from)} → ${l.to ? objName(l.to) : '未选择'} · ${l.card}`" @click="pick(l.id)">
          <div class="lt-row">
            <span class="t-14 lt-name lt-ell">{{ l.name }}</span>
            <span class="t-12 c-3 lt-ft">{{ objName(l.from) }} → {{ l.to ? objName(l.to) : '未选择' }}</span>
            <span v-if="l.status !== '已发布'" class="t-12" :class="statusClass(l.status)">{{ l.status }}</span>
          </div>
        </li>
      </ul>
      <div class="lt-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="listTotal" :size="LPAGE" unit="条" class="lt-pager" empty-text="没有匹配的关系" />
      </div>
    </div>

    <!-- 关系图 -->
    <div v-if="mode === 'graph'" class="panel lt-detail">
      <div class="lt-head">
        <span class="t-16 fw-600 c-1">关系图<InfoTip text="点击连线选中关系，点击对象查看对象类型" /></span>
        <span class="t-12 c-3">{{ graphNodes.length }} 个对象 · {{ graphLinks.length }} 条关系{{ src || st || kw ? '（按左侧筛选）' : '' }}</span>
        <span class="lt-sp" />
        <span class="t-12 c-3 lt-legend"><i class="lg-solid" />已发布</span>
        <span class="t-12 c-3 lt-legend"><i class="lg-dash" />草稿 / 已驳回</span>
      </div>
      <div ref="gbox" class="lt-graph">
        <svg :width="gw" :height="gh" class="lt-svg">
          <defs>
            <marker id="lt-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0 L10 5 L0 10 z" fill="var(--g7)" />
            </marker>
            <marker id="lt-arr-sel" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0 L10 5 L0 10 z" fill="var(--brand)" />
            </marker>
          </defs>
          <g v-for="e in edges" :key="e.l.id" class="lt-edge" @click="pick(e.l.id)" @mouseenter="hoverEdge = e.l.id" @mouseleave="hoverEdge = ''">
            <path :d="e.d" class="lt-hit" />
            <path :d="e.d" class="lt-line" :class="{ sel: e.l.id === cur?.id, hov: hoverEdge === e.l.id, dash: e.l.status !== '已发布' }"
              :marker-end="e.l.id === cur?.id ? 'url(#lt-arr-sel)' : 'url(#lt-arr)'" />
            <text v-if="hoverEdge === e.l.id && e.l.id !== cur?.id" :x="e.lx" :y="e.ly - 6" text-anchor="middle" class="lt-elabel t-12">{{ e.l.name }} · {{ e.l.card }}</text>
          </g>
          <g v-for="n in graphNodes" :key="n.id" class="lt-node" :class="{ end: isEnd(n.id), draft: n.draft, link: canRef(n.id) }" @click="clickNode(n.id)">
            <rect :x="n.x - NW / 2" :y="n.y - NH / 2" :width="NW" :height="NH" rx="4" />
            <text :x="n.x" :y="n.y + 5" text-anchor="middle" class="t-14">{{ n.name }}</text>
          </g>
          <g v-if="selEdge" class="lt-sel-label">
            <rect :x="selEdge.lx - 64" :y="selEdge.ly - 12" width="128" height="24" rx="4" />
            <text :x="selEdge.lx" :y="selEdge.ly + 5" text-anchor="middle" class="t-12">{{ selEdge.l.name }} · {{ selEdge.l.card }}</text>
          </g>
        </svg>
      </div>
      <div v-if="cur" class="lt-gfoot">
        <span class="t-14 fw-600 c-1">{{ cur.name }}</span>
        <span class="t-12 c-3 num">{{ apiOf(cur) }}</span>
        <a-tag :color="statusColor(cur.status)">{{ cur.status }}</a-tag>
        <span class="t-14 c-2">{{ objName(cur.from) }} → {{ cur.to ? objName(cur.to) : '未选择' }} · {{ cur.card }}</span>
        <span class="t-12 c-3 num">{{ cur.key }}</span>
        <span v-if="curGroups.length" class="t-12 c-3">问数：{{ curGroups.map((g) => g.check.replace(/^V13 通过：/, '')).join('；') }}</span>
        <span class="lt-sp" />
        <a @click="toDetail">查看详情</a>
      </div>
    </div>

    <!-- 详情 -->
    <div v-else-if="cur" class="panel lt-detail">
      <DraftBanner v-if="isDraft && draft && (!isStaticReal() || cr.head.value?.status === '草稿')" :draft="draft"
        @submit="cr.submit" @discard="draftOnly ? message.info('这条关系只在草稿里，放弃请到变更单处理') : cr.discard()" />
      <div class="lt-head">
        <span class="t-16 fw-600 c-1">{{ cur.name }}</span>
        <span class="t-14 c-3 num">{{ apiOf(cur) || '（未填写 API 名）' }}</span>
        <a-tag :color="statusColor(cur.status)">{{ cur.status }}</a-tag>
        <span class="t-14 c-2">
          <ObjRef v-if="canRef(cur.from)" kind="object-type" :id="cur.from">{{ objName(cur.from) }}</ObjRef><template v-else>{{ objName(cur.from) }}</template>
          →
          <ObjRef v-if="canRef(cur.to)" kind="object-type" :id="cur.to">{{ objName(cur.to) }}</ObjRef><template v-else>{{ cur.to ? objName(cur.to) : '未选择' }}</template>
          <span class="c-3 num"> · {{ cur.card }}</span>
        </span>
        <a-segmented v-if="draft && !readonly && !draftOnly" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => guard(() => { editing = false; setView(v); })" />
        <span class="lt-sp" />
        <a-tooltip v-if="readonly" :title="reason"><a-button disabled>发起变更</a-button></a-tooltip>
        <a-button v-else-if="!draft" type="primary" @click="openChange(`${cur.name}（${apiOf(cur)}）`)">{{ cur.status === '已驳回' ? '重新发起变更' : '发起变更' }}</a-button>
        <a-button v-else-if="!isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
      </div>
      <div v-if="rejectedCR" class="lt-reject t-12">
        <span class="c-error fw-600">{{ rejectedCR.id }} 已驳回</span>
        <span class="c-2">{{ rejectedCR.reviewer }}：{{ rejectedCR.rejectReason }}</span>
        <span class="c-3">没有已发布的版本，问数中不存在这条关系</span>
      </div>
      <a-tabs v-model:activeKey="tab" class="lt-tabs">
        <a-tab-pane key="def" tab="定义" />
        <a-tab-pane key="mapping" tab="关联映射" />
        <a-tab-pane key="paths" tab="问数路径" />
        <a-tab-pane key="usage" tab="使用情况" />
        <a-tab-pane key="history" tab="版本历史" />
      </a-tabs>

      <!-- 定义 -->
      <div v-if="tab === 'def'" class="lt-body lt-def">
        <div class="lt-def-left">
          <div class="lt-sec t-14 fw-600 lt-sec-row">
            <span>关系定义</span>
            <a-space v-if="editing">
              <a-button size="small" @click="cancelEdit">取消</a-button>
              <a-tooltip :title="formError || undefined"><a-button size="small" :disabled="!dirty || !!formError || saving" :loading="saving" @click="save">保存到草稿</a-button></a-tooltip>
            </a-space>
            <a-tooltip v-else :title="editTip"><a-button size="small" :disabled="!canEdit" @click="beginEdit">编辑定义</a-button></a-tooltip>
          </div>
          <a-descriptions v-if="!editing" :column="2" bordered size="small" class="lt-desc">
            <a-descriptions-item label="显示名">{{ cur.name }}</a-descriptions-item>
            <a-descriptions-item label="API 名"><span class="num">{{ apiOf(cur) || '--' }}</span></a-descriptions-item>
            <a-descriptions-item label="源对象">
              <ObjRef v-if="canRef(cur.from)" kind="object-type" :id="cur.from">{{ objName(cur.from) }}</ObjRef><template v-else>{{ objName(cur.from) }}</template>
              <span class="c-3 num">（{{ objById(cur.from)?.api }}）</span>
            </a-descriptions-item>
            <a-descriptions-item label="目标对象">
              <template v-if="cur.to">
                <ObjRef v-if="canRef(cur.to)" kind="object-type" :id="cur.to">{{ objName(cur.to) }}</ObjRef><template v-else>{{ objName(cur.to) }}<span class="c-3">（草稿对象）</span></template>
                <span class="c-3 num">（{{ objById(cur.to)?.api }}）</span>
              </template>
              <span v-else class="c-error">未选择</span>
            </a-descriptions-item>
            <a-descriptions-item label="关联键" :span="2"><span class="num">{{ cur.key || '--' }}</span><span v-if="cur.fromProp && cur.card !== 'N : M'" class="c-3">（{{ objName(cur.from) }}.{{ cur.fromProp }} → {{ objName(cur.to) }}.{{ cur.toProp }}）</span></a-descriptions-item>
            <a-descriptions-item v-for="d in DEF_ITEMS" :key="d.k" :label="d.k">{{ d.v || '--' }}</a-descriptions-item>
            <a-descriptions-item label="说明" :span="2">{{ cur.desc || '--' }}</a-descriptions-item>
          </a-descriptions>
          <a-form v-else layout="horizontal" :label-col="{ style: { width: '96px' } }" class="lt-form">
            <div class="lt-form-grid">
              <a-form-item label="显示名"><a-input v-model:value="form.name" :maxlength="64" /></a-form-item>
              <a-form-item label="API 名">
                <a-tooltip :title="cur.status === '草稿' ? '' : '已发布的 API 名不可改名（V7）'"><a-input v-model:value="form.api" :maxlength="64" :disabled="cur.status !== '草稿'" placeholder="例如 order_belongs_to_store" /></a-tooltip>
              </a-form-item>
              <a-form-item label="源对象"><a-select v-if="isStaticReal()" v-model:value="form.from" :options="OBJ_OPTIONS" :loading="catalogLoading" :disabled="catalogLoading || !!catalogError || saving" show-search option-filter-prop="label" placeholder="选择对象类型" @change="form.fromProp = ''" /><a-input v-else :value="objName(cur.from)" disabled /></a-form-item>
              <a-form-item label="目标对象"><a-select v-model:value="form.to" :options="OBJ_OPTIONS" :loading="catalogLoading" :disabled="isStaticReal() && (catalogLoading || !!catalogError || saving)" @change="form.toProp = ''" show-search option-filter-prop="label" placeholder="选择对象类型" /></a-form-item>
              <a-form-item label="基数"><a-select v-model:value="form.card" :options="CARD_OPTIONS" /></a-form-item>
              <a-form-item label="允许为空"><a-switch v-model:checked="form.nullable" size="small" /></a-form-item>
              <a-form-item v-if="form.card === 'N : M'" label="中间对象"><a-select v-if="isStaticReal()" v-model:value="form.via" :options="VIA_OPTIONS" show-search option-filter-prop="label" placeholder="选择当前本体的中间对象" :disabled="catalogLoading || !!catalogError || saving" /><a-input v-else v-model:value="form.via" placeholder="中间表或中间对象" /></a-form-item>
              <template v-else>
                <a-form-item label="源端属性"><a-select v-if="isStaticReal()" v-model:value="form.fromProp" :options="propertyOptions(form.from)" :disabled="!form.from || catalogLoading || !!catalogError || saving" show-search option-filter-prop="label" placeholder="选择源端属性" /><a-input v-else v-model:value="form.fromProp" /></a-form-item>
                <a-form-item label="目标端属性"><a-select v-if="isStaticReal()" v-model:value="form.toProp" :options="propertyOptions(form.to)" :disabled="!form.to || catalogLoading || !!catalogError || saving" show-search option-filter-prop="label" placeholder="选择目标端属性" /><a-input v-else v-model:value="form.toProp" /></a-form-item>
              </template>
              <a-form-item label="反向名称"><a-input v-model:value="form.reverse" :maxlength="64" /></a-form-item>
            </div>
            <a-form-item label="说明"><a-textarea v-model:value="form.desc" :rows="2" placeholder="问数会用这段描述理解这条关系（V11）" /></a-form-item>
          </a-form>
          <div class="lt-sec t-14 fw-600">生成的关联</div>
          <div class="lt-code t-14 num">{{ cur.join ? `LEFT JOIN ${objById(cur.to)?.table ?? ''} ON ${cur.join}` : '--' }}</div>
        </div>
        <div class="lt-def-right">
          <div class="lt-sec t-14 fw-600">两端对象</div>
          <div class="lt-ends">
            <div class="lt-end">
              <span class="t-12 c-3">源对象</span>
              <span class="t-14 c-1 lt-ell" :title="`${objName(cur.from)} · ${objById(cur.from)?.table ?? '--'}`">{{ objName(cur.from) }}</span>
            </div>
            <div class="lt-arrow t-12 c-3" :title="`${cur.name} → · ${cur.card} · ← ${cur.reverse || '--'}`">
              <span class="num">→ {{ cur.card }} →</span>
            </div>
            <div class="lt-end">
              <span class="t-12 c-3">目标对象</span>
              <span class="t-14 c-1 lt-ell" :title="`${cur.to ? objName(cur.to) : '未选择'} · ${objById(cur.to)?.table ?? '--'}`">{{ cur.to ? objName(cur.to) : '未选择' }}</span>
            </div>
          </div>
          <div class="lt-sec t-14 fw-600">发布校验<InfoTip :text="`关系定义只随本体整版发布生效；问数始终使用已发布版本 ${app.ontologyVersion}。`" /></div>
          <div v-for="c in CHECKS" :key="c.code" class="lt-check">
            <span class="t-12 c-3 num lt-check-code">{{ c.code }}</span>
            <span class="t-14 c-1 lt-check-name">{{ c.name }}</span>
            <span class="t-12" :class="checkClass(c.result)">{{ c.result }}</span>
            <span class="t-12 c-3 lt-check-note" :title="c.note">{{ c.note }}</span>
          </div>
        </div>
      </div>

      <!-- 关联映射 -->
      <div v-else-if="tab === 'mapping'" class="lt-body">
        <a-descriptions :column="3" bordered size="small">
          <a-descriptions-item label="源表"><span class="num">{{ objById(cur.from)?.table }}</span></a-descriptions-item>
          <a-descriptions-item label="目标表"><span class="num">{{ objById(cur.to)?.table ?? '--' }}</span></a-descriptions-item>
          <a-descriptions-item label="关联方式">{{ cur.via ? `经中间表 ${cur.via}` : cur.nullable ? 'LEFT JOIN（允许为空）' : 'INNER JOIN 语义（不允许为空）' }}</a-descriptions-item>
          <a-descriptions-item label="JOIN 条件" :span="3"><span class="num">{{ cur.join || '--' }}</span></a-descriptions-item>
        </a-descriptions>
        <template v-if="cur.quality">
          <div class="lt-sec t-14 fw-600 lt-sec-row">
            <span>关联质量</span><span class="t-12 c-3 fw-400">每日 06:00 检查 · 最近 {{ cur.quality.checkedAt }}{{ cur.status === '草稿' ? ' · 测试环境' : '' }}</span>
          </div>
          <div class="lt-kpis">
            <div class="lt-kpi">
              <span class="t-12 c-3">关联命中率<InfoTip text="源端非空关联键能在目标端找到记录的比例" /></span>
              <span class="t-kpi fw-600" :class="hitWarn(cur.quality.hit) ? 'c-warning' : 'c-1'">{{ cur.quality.hit }}%</span>
            </div>
            <div class="lt-kpi">
              <span class="t-12 c-3">孤儿记录<InfoTip text="关联键有值，但目标端不存在" /></span>
              <span class="t-kpi fw-600" :class="cur.quality.orphans && hitWarn(cur.quality.hit) ? 'c-warning' : 'c-1'">{{ fmtInt(cur.quality.orphans) }}</span>
            </div>
            <div class="lt-kpi">
              <span class="t-12 c-3">关联键为空<InfoTip :text="cur.nullable ? '允许为空，不计入命中率' : '不允许为空'" /></span>
              <span class="t-kpi fw-600 c-1">{{ fmtInt(cur.quality.nulls) }}</span>
            </div>
            <div class="lt-kpi">
              <span class="t-12 c-3">重复匹配<InfoTip :text="cur.card === 'N : 1' ? '一条源记录匹配到多条目标（应为 0）' : '目标端主键重复（应为 0）'" /></span>
              <span class="t-kpi fw-600" :class="cur.quality.dup ? 'c-error' : 'c-1'">{{ fmtInt(cur.quality.dup) }}</span>
            </div>
          </div>
          <div v-if="cur.quality.note" class="t-14" :class="hitWarn(cur.quality.hit) ? 'c-warning' : 'c-2'">{{ cur.quality.note }}</div>
          <template v-if="cur.quality.orphanSamples">
            <div class="lt-sec t-14 fw-600">孤儿记录分布</div>
            <a-table :columns="orphanCols" :data-source="cur.quality.orphanSamples" :pagination="false" size="small" row-key="key">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'count'"><span class="num">{{ fmtInt(record.count) }}</span></template>
              </template>
            </a-table>
          </template>
        </template>
        <div v-else class="lt-empty t-14 c-3">{{ cur.status === '已驳回' ? '变更单已驳回，没有做关联检查' : '保存两端对象和关联键后，会在测试环境做一次关联检查' }}</div>
      </div>

      <!-- 问数路径 -->
      <div v-else-if="tab === 'paths'" class="lt-body">
        <template v-if="curGroups.length">
          <div class="t-14 c-2">{{ objName(curGroups[0].from) }} 和 {{ objName(curGroups[0].to) }} 之间有 {{ curPathCount }} 条路径。发布校验 V13 要求有且只有一条默认路径，其余路径需要用户在问题里指明。</div>
          <div v-for="g in curGroups" :key="g.from + g.to" class="lt-group">
            <div class="lt-sec t-14 fw-600 lt-sec-row">
              <span>{{ objName(g.from) }} → {{ objName(g.to) }}</span>
              <span class="t-12 c-3 fw-400">{{ g.check }}</span>
            </div>
            <div class="lt-paths">
              <div v-for="(p, i) in g.paths" :key="i" class="lt-path" :class="{ cur: p.links.includes(cur.id) }">
                <span class="t-12 c-3 num lt-path-no">路径 {{ i + 1 }}</span>
                <span class="t-14 lt-chain">
                  <template v-for="(s, j) in chain(p)" :key="j">
                    <span v-if="s.via" class="c-3 t-12"> —{{ s.via }}→ </span><span class="c-1">{{ objName(s.obj) }}</span>
                  </template>
                </span>
                <span class="t-12 c-3">{{ p.links.length }} 跳</span>
                <span class="t-12 lt-path-mode" :class="p.isDefault ? 'c-1 fw-600' : 'c-3'">{{ p.isDefault ? '默认路径' : '需用户指明' }}</span>
                <span class="t-12 c-3 lt-path-hint">{{ p.hint ? `指明方式：${p.hint}` : '问题里没有指明路径时使用' }}</span>
              </div>
            </div>
            <a-table :columns="[{ title: '示例问题', dataIndex: 'q', width: 340 }, { title: '解析路径', key: 'path', width: 280 }, { title: '说明', dataIndex: 'how' }]"
              :data-source="g.examples" :pagination="false" size="small" row-key="q">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'path'">
                  <span v-if="record.path < 0" class="c-warning">先追问用户选择路径</span>
                  <span v-else :class="g.paths[record.path].links.includes(cur.id) ? 'c-1' : 'c-3'">{{ g.paths[record.path].links.map(linkName).join(' → ') }}</span>
                </template>
              </template>
            </a-table>
          </div>
        </template>
        <template v-else>
          <div class="t-14 c-2">{{ objName(cur.from) }} 和 {{ cur.to ? objName(cur.to) : '目标对象' }} 之间只有这一条路径，问数直接使用，不需要用户指明（V13 不涉及）。</div>
          <div v-if="cur.id === 'store_manager'" class="t-14 c-warning">未发布。重新提交时，门店和员工之间会有两条路径（本关系，以及“员工 → 门店 所属门店”的反向），需要指定默认路径。</div>
          <a-descriptions :column="1" bordered size="small" class="lt-desc">
            <a-descriptions-item label="正向">{{ objName(cur.from) }} —{{ cur.name }}→ {{ objName(cur.to) }}（{{ cur.card }}）</a-descriptions-item>
            <a-descriptions-item label="反向">{{ objName(cur.to) }} —{{ cur.reverse || '--' }}→ {{ objName(cur.from) }}</a-descriptions-item>
            <a-descriptions-item label="问数用法">{{ cur.desc || '--' }}</a-descriptions-item>
          </a-descriptions>
        </template>
      </div>

      <!-- 使用情况 -->
      <div v-else-if="tab === 'usage'" class="lt-body">
        <a-alert v-if="usagePage.error.value" type="error" :message="usagePage.error.value" show-icon />
        <div class="t-14 c-2">{{ cur.usage.length ? `共 ${cur.usage.length} 项依赖本关系：${usageSummary}` : '暂无指标、认证问答、视图或规则依赖本关系' }}</div>
        <a-table v-if="isStaticReal() ? usagePage.total.value : cur.usage.length" :columns="usageCols" :data-source="isStaticReal() ? usagePage.items.value : cur.usage.slice((up - 1) * usageSize, up * usageSize)" :loading="usagePage.loading.value" :pagination="false" size="middle" row-key="name">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <ObjRef v-if="record.hover" :kind="record.hover.kind" :id="record.hover.id">{{ record.name }}</ObjRef>
              <span v-else class="c-1">{{ record.name }}</span>
            </template>
          </template>
        </a-table>
        <AppPagination v-model:page="up" variant="full" :total="isStaticReal() ? usagePage.total.value : cur.usage.length" v-model:size="usageSize" unit="条" :empty="false" />
        <div class="t-12 c-3">修改或下线本关系时，以上对象会出现在变更单的影响分析里；已发布的 API 名不可删除或改名（V7）。</div>
      </div>

      <!-- 版本历史 -->
      <div v-else class="lt-body">
        <a-alert v-if="historyPage.error.value" type="error" :message="historyPage.error.value" show-icon />
        <a-table :columns="histCols" :data-source="isStaticReal() ? historyPage.items.value : cur.history.slice((hpage - 1) * historySize, hpage * historySize)" :loading="historyPage.loading.value" :pagination="false" size="middle" :row-key="(r: any) => r.ver + (r.cr ?? '') + r.at">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'cr'">
              <a v-if="record.cr" class="num" @click="crLink(record.cr)">{{ record.cr }}</a>
              <span v-else class="c-3">启用变更单前</span>
            </template>
            <template v-else-if="column.key === 'status'">
              <span :class="record.status === '草稿' ? 'c-3' : record.status === '已驳回' ? 'c-error' : record.status === '生效中' ? 'c-1' : 'c-3'">{{ record.status }}</span>
            </template>
          </template>
        </a-table>
        <div class="t-12 c-3">关系定义只随本体整版发布生效；回滚会生成新的变更单，经审核后随下一次发布生效。</div>
        <AppPagination v-model:page="hpage" variant="full" :total="isStaticReal() ? historyPage.total.value : cur.history.length" v-model:size="historySize" :empty="false" />
      </div>
    </div>
  </div>
  <StartChangeModal v-model:open="changeOpen" :target-name="pendingNewCr && creating ? `${changeTarget}（重试保留草稿 ${pendingNewCr}）` : changeTarget" :title-placeholder="creating ? '例如：会员持有储值卡' : undefined" @ok="start" />
</template>

<style scoped>
.lt { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.lt-list { width: 300px; flex: none; }
.lt-filter { padding: 12px 12px 8px; display: flex; flex-direction: column; gap: 8px; flex: none; }
.lt-selects { display: flex; gap: 8px; }
.lt-sel { flex: 1; min-width: 0; }
.lt-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; }
.lt-items li { padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.lt-items li:hover { background: var(--g3); }
.lt-items li.active { background: var(--brand-bg); }
.lt-items li.active .lt-name { color: var(--brand); }
.lt-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.lt-pager { flex: 1; min-height: 0; }
.lt-foot { padding: 8px 12px 8px 16px; border-top: var(--border); flex: none; display: flex; align-items: center; justify-content: space-between; }
.lt-detail { flex: 1; min-width: 0; }
.lt-head { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; flex: none; }
.lt-sp { flex: 1; }
.lt-reject { display: flex; gap: 12px; align-items: center; padding: 6px 16px; border-top: var(--border); border-bottom: var(--border); background: var(--g2); flex: none; }
.lt-tabs { padding: 0 16px; flex: none; }
.lt-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.lt-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
.lt-def { flex-direction: row; gap: 16px; }
.lt-def-left { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.lt-def-right { width: 440px; flex: none; display: flex; flex-direction: column; gap: 8px; padding-left: 16px; border-left: var(--border); }
.lt-sec { margin-top: 4px; }
.lt-sec-row { display: flex; justify-content: space-between; align-items: baseline; }
.lt-desc :deep(.ant-descriptions-item-label) { width: 112px; }
.lt-code { padding: 8px 12px; background: var(--g2); border: var(--border); border-radius: var(--radius); font-family: Menlo, Consolas, "Noto Sans Mono CJK SC", monospace; word-break: break-all; }
.lt-form-grid { display: grid; grid-template-columns: 1fr 1fr; column-gap: 16px; }
.lt-form :deep(.ant-form-item) { margin-bottom: 12px; }
.lt-ends { display: flex; align-items: stretch; gap: 8px; }
.lt-tbl { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lt-end { flex: 1; display: flex; align-items: baseline; gap: 8px; padding: 8px 12px; border: var(--border); border-radius: var(--radius); min-width: 0; }
.lt-arrow { width: 120px; flex: none; display: flex; align-items: center; justify-content: center; text-align: center; }
.lt-check { display: flex; align-items: baseline; gap: 8px; padding: 6px 0; border-bottom: var(--border); }
.lt-check-code { width: 32px; flex: none; }
.lt-check-name { width: 104px; flex: none; }
.lt-check-note { flex: 1; min-width: 0; text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lt-kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.lt-kpi { display: flex; flex-direction: column; gap: 4px; padding: 12px 16px; border: var(--border); border-radius: var(--radius); }
.lt-empty { padding: 24px 0; }
.lt-group { display: flex; flex-direction: column; gap: 8px; }
.lt-paths { display: flex; flex-direction: column; border: var(--border); border-radius: var(--radius); }
.lt-path { display: flex; align-items: center; gap: 16px; padding: 8px 12px; }
.lt-path + .lt-path { border-top: var(--border); }
.lt-path.cur { background: var(--brand-bg); }
.lt-path-no { width: 48px; flex: none; }
.lt-chain { width: 420px; flex: none; }
.lt-path-mode { width: 72px; flex: none; }
.lt-path-hint { flex: 1; min-width: 0; }
.lt-graph { flex: 1; min-height: 0; position: relative; overflow: hidden; border-top: var(--border); }
.lt-svg { position: absolute; inset: 0; display: block; }
.lt-legend { display: inline-flex; align-items: center; gap: 6px; }
.lg-solid, .lg-dash { display: inline-block; width: 24px; height: 0; border-top: 1.5px solid var(--g7); }
.lg-dash { border-top-style: dashed; }
.lt-hit { fill: none; stroke: transparent; stroke-width: 14; cursor: pointer; }
.lt-line { fill: none; stroke: var(--g6); stroke-width: 1.5; pointer-events: none; }
.lt-line.hov { stroke: var(--g8); }
.lt-line.dash { stroke-dasharray: 5 4; }
.lt-line.sel { stroke: var(--brand); stroke-width: 2; }
.lt-elabel { fill: var(--g9); paint-order: stroke; stroke: #fff; stroke-width: 4px; pointer-events: none; }
.lt-node rect { fill: #fff; stroke: var(--g6); }
.lt-node text { fill: var(--g10); pointer-events: none; }
.lt-node.link { cursor: pointer; }
.lt-node.link:hover rect { stroke: var(--g8); }
.lt-node.draft rect { stroke-dasharray: 4 3; }
.lt-node.end rect { stroke: var(--brand); fill: var(--brand-bg); }
.lt-node.end text { fill: var(--brand); }
.lt-sel-label rect { fill: #fff; stroke: var(--brand); }
.lt-sel-label text { fill: var(--brand); }
.lt-gfoot { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; border-top: var(--border); flex: none; min-width: 0; }

.lt-ell { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lt-ft { flex-shrink: 0; }
</style>
