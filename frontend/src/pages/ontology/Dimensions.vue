<script setup lang="ts">
/**
 * 维度与字典：左侧维度 / 码表列表，右侧详情（定义 / 取值 / 同义词与问数映射 / 使用情况 / 版本历史）。
 * URL：?dim= 选中项，?tab= 页签，?k= 维度或码表，?g= 分组，?q= 搜索，?lv= / ?vq= / ?vs= / ?vp= 取值筛选和页码，
 *      ?conf=1 只看冲突，?try= 试一试的说法，?view=draft 草稿视图，?ver= 历史版本（只读）。
 * 草稿逻辑用公共的 useDraftView('dimension')：已发布只读，唯一入口“发起变更”；草稿里的改动用灰色“新增 / 修改”标出。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import ObjRef from '@/components/ObjRef.vue';
import AppPagination from '@/components/AppPagination.vue';
import { useReadonly } from '@/composables/useReadonly';
import { useUnsavedGuard } from '@/composables/useUnsavedGuard';
import { useAppStore } from '@/stores/app';
import { useConfirmStore } from '@/stores/confirm';
import { CURRENT_USER } from '@/mock/changes';
import {
  DIM_ITEMS, metricUsage,
  type DimItem, type DimValue, type DimMapping, type DimLevel, type DimQa, type DimHistory, type ValueStatus, type MapMatch,
} from '@/mock/dimensions';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import { useDraftView } from './parts/useDraftView';

const route = useRoute();
const router = useRouter();
const app = useAppStore();
const confirm = useConfirmStore();
const { readonly, reason } = useReadonly();
const pad = (n: number) => String(n).padStart(2, '0');
const nowText = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const fmtInt = (n: number) => n.toLocaleString('en-US');

const items = reactive<DimItem[]>(DIM_ITEMS.map((d) => ({
  ...d, values: d.values.map((v) => ({ ...v, synonyms: [...v.synonyms] })), mappings: d.mappings.map((m) => ({ ...m })),
  qa: { ...d.qa }, history: [...d.history],
})));
const ver = computed(() => (readonly.value ? app.viewVersion : null));
/** 在某个历史版本里是否有效：since ≤ 版本 < until */
const live = (x: { since?: string; until?: string }, v: string | null) => (!v ? !x.until : (!x.since || x.since <= v) && (!x.until || v < x.until));
const visible = computed(() => items.filter((d) => !ver.value || d.since <= ver.value));

// —— 列表筛选（写进 URL）——
const q = (k: string) => (route.query[k] as string) || '';
const setQ = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const kw = computed({ get: () => q('q'), set: (v) => setQ({ q: v || undefined }) });
const kind = computed({ get: () => q('k') || 'all', set: (v) => setQ({ k: v === 'all' ? undefined : v }) });
const grp = computed({ get: () => q('g'), set: (v) => setQ({ g: v || undefined }) });
const GROUP_OPTIONS = computed(() => [{ value: '', label: '全部分组' }, ...[...new Set(visible.value.map((d) => d.group))].map((g) => ({ value: g, label: g }))]);
const KIND_OPTIONS = [{ label: '全部', value: 'all' }, { label: '维度', value: '维度' }, { label: '码表', value: '码表' }];
const filtered = computed(() => visible.value.filter((d) =>
  (kind.value === 'all' || d.kind === kind.value) && (!grp.value || d.group === grp.value)
  && (!kw.value || d.name.includes(kw.value) || d.api.includes(kw.value) || d.objectName.includes(kw.value)
    || d.values.some((v) => v.name.includes(kw.value) || v.synonyms.includes(kw.value)))));
const LPAGE = 12;
const lpage = ref(1);
watch([kw, kind, grp], () => { lpage.value = 1; });
const list = computed(() => filtered.value.slice((lpage.value - 1) * LPAGE, lpage.value * LPAGE));
const cur = computed(() => visible.value.find((d) => d.id === route.query.dim) ?? visible.value[0]);
watch(() => cur.value?.id, (id) => {
  const i = filtered.value.findIndex((d) => d.id === id);
  if (i >= 0) lpage.value = Math.floor(i / LPAGE) + 1;
}, { immediate: true });

const tab = computed({
  get: () => (route.query.tab as string) || 'def',
  set: (v) => guard(() => { editing.value = false; setQ({ tab: v, vp: undefined }); }),
});
const pick = (id: string) => guard(() => {
  editing.value = false;
  router.replace({ query: { ...route.query, dim: id, view: undefined, lv: undefined, vq: undefined, vs: undefined, vp: undefined, conf: undefined, drawer: undefined, drawer2: undefined } });
});

// —— 草稿（公共 useDraftView，kind = dimension）——
const dv = useDraftView('dimension', () => cur.value?.id ?? '');
const { draft, isDraft, canEdit, setView } = dv;
const editTip = computed(() => dv.editTip.value);
const changeOpen = ref(false);
const openChange = () => { if (!readonly.value) changeOpen.value = true; };
function start(opts: { join?: string; title?: string }) {
  const id = dv.start(opts);
  cur.value.history.unshift({ ver: '—', cr: id, status: '草稿', by: CURRENT_USER, at: nowText(), change: '发起变更（待填写）' });
}
/** 草稿里改动的计数写回变更单 */
const touch = (change: string) => {
  const d = draft.value;
  if (!d) return;
  d.changes += 1; d.updatedAt = nowText();
  const h = cur.value.history.find((x) => x.cr === d.id && x.status === '草稿');
  if (h) { h.change = h.change.endsWith('（待填写）') ? change : `${h.change}；${change}`; h.at = d.updatedAt; }
};

// —— 版本：层级和问数设置（CR-0005 之前组织维度是 省份 → 门店）——
const before = computed(() => {
  const b = cur.value.levelsBefore;
  return b && ver.value && ver.value < b.ver ? b : null;
});
const levels = computed<DimLevel[]>(() => before.value?.levels ?? cur.value.levels);
const curDesc = computed(() => before.value?.desc ?? cur.value.desc);
const curAttr = computed(() => before.value?.attr ?? cur.value.attr);
const qa = computed<DimQa>(() => {
  const b = cur.value.levelsBefore;
  return b && ver.value && ver.value < b.ver ? { ...cur.value.qa, ...b.qa } : cur.value.qa;
});

// —— 取值：已发布版显示 pub 原值，草稿新增的只在草稿视图出现 ——
type Row = DimValue & { diffTag?: '新增' | '修改' };
const inView = (draftId?: string) => !draftId || (isDraft.value && draftId === draft.value?.id);
const valueRows = computed<Row[]>(() => cur.value.values
  .filter((v) => live(v, ver.value) && inView(v.draft))
  .filter((v) => !ver.value || !v.diff)
  .map((v) => (isDraft.value ? { ...v, diffTag: v.diff } : { ...v, ...(v.pub ?? {}), diffTag: undefined })));
const lv = computed({ get: () => q('lv'), set: (v) => setQ({ lv: v || undefined, vp: undefined }) });
const vq = computed({ get: () => q('vq'), set: (v) => setQ({ vq: v || undefined, vp: undefined }) });
const vs = computed({ get: () => q('vs'), set: (v) => setQ({ vs: v || undefined, vp: undefined }) });
const vp = computed({ get: () => Number(q('vp')) || 1, set: (v) => setQ({ vp: v > 1 ? String(v) : undefined }) });
const valueLevels = computed(() => [...new Set(valueRows.value.map((v) => v.level))]);
const LV_OPTIONS = computed(() => [{ label: '全部层级', value: '' }, ...valueLevels.value.map((l) => ({ label: l, value: l }))]);
const VS_OPTIONS = [{ value: '', label: '全部状态' }, { value: '启用', label: '启用' }, { value: '停用', label: '停用' }];
const shownValues = computed(() => valueRows.value.filter((v) => (!lv.value || v.level === lv.value) && (!vs.value || v.status === vs.value)
  && (!vq.value || v.name.includes(vq.value) || v.code.includes(vq.value) || v.synonyms.some((s) => s.includes(vq.value)))));
const valueSummary = computed(() => {
  const all = valueRows.value;
  const off = all.filter((v) => v.status === '停用').length;
  const diff = all.filter((v) => v.diffTag).length;
  return `共 ${all.length} 个取值${off ? `，停用 ${off} 个` : ''}${diff ? `，草稿改动 ${diff} 个` : ''}`;
});
const multiLevel = computed(() => valueLevels.value.length > 1);
const VPAGE = 10;
const valueCols = computed(() => [
  { title: '编码', dataIndex: 'code', width: 120 },
  { title: '名称', key: 'name', width: 260 },
  ...(multiLevel.value ? [{ title: '层级', dataIndex: 'level', width: 88 }, { title: '上级', key: 'parent', width: 120 }] : []),
  { title: '同义词', key: 'synonyms', width: 220 },
  ...(cur.value.countLabel ? [{ title: cur.value.countLabel, key: 'count', width: 112, align: 'right' as const }] : []),
  { title: '状态', key: 'status', width: 72 },
  { title: '说明', key: 'note' },
  { title: '操作', key: 'op', width: 112 },
]);
const vpager = computed(() => ({ current: vp.value, pageSize: VPAGE, showSizeChanger: false, showTotal: (t: number) => `共 ${t} 条`, size: 'small' as const, onChange: (p: number) => { vp.value = p; } }));

// —— 新增 / 编辑取值（只在草稿里）——
interface VForm { code: string; name: string; level: string; parent: string; synonyms: string[]; status: ValueStatus; note: string }
const vOpen = ref(false);
const vEdit = ref<DimValue | null>(null);
const vForm = reactive<VForm>({ code: '', name: '', level: '', parent: '', synonyms: [], status: '启用', note: '' });
const vErr = computed(() => {
  if (!vForm.code.trim() || !vForm.name.trim()) return '编码和名称必填';
  const dup = cur.value.values.find((v) => v !== vEdit.value && live(v, null) && inView(v.draft) && v.level === vForm.level && (v.code === vForm.code.trim() || v.name === vForm.name.trim()));
  return dup ? `同一层级已有取值 ${dup.name}（${dup.code}），编码和名称需唯一（V5）` : '';
});
const parentLevel = computed(() => { const i = levels.value.findIndex((l) => l.name === vForm.level); return i > 0 ? levels.value[i - 1].name : ''; });
const PARENT_OPTIONS = computed(() => cur.value.values.filter((v) => v.level === parentLevel.value && live(v, null)).map((v) => ({ value: v.name, label: v.name })));
const synConflicts = computed(() => vForm.synonyms.flatMap((s) => hitsOf(s).filter((h) => !(h.item === cur.value.id && h.value === vForm.name)).map((h) => `“${s}”已是 ${h.itemName} · ${h.value}`)));
function openValue(v?: Row) {
  const src = v ? cur.value.values.find((x) => x.key === v.key) ?? null : null;
  vEdit.value = src;
  const lvl = src?.level ?? (lv.value || valueLevels.value[valueLevels.value.length - 1] || levels.value[0]?.name || '');
  Object.assign(vForm, { code: src?.code ?? '', name: src?.name ?? '', level: lvl, parent: src?.parent ?? '', synonyms: [...(src?.synonyms ?? [])], status: src?.status ?? '启用', note: src?.note ?? '' });
  vOpen.value = true;
}
function saveValue() {
  if (vErr.value) return;
  const f = { code: vForm.code.trim(), name: vForm.name.trim(), level: vForm.level, parent: vForm.parent || undefined, synonyms: [...vForm.synonyms], status: vForm.status, note: vForm.note || undefined };
  const v = vEdit.value;
  if (!v) {
    cur.value.values.push({ key: `${f.level}:${f.code}`, ...f, count: 0, draft: draft.value?.id, diff: '新增' });
    touch(`新增取值“${f.name}”（${f.code}）`);
    vs.value = ''; lv.value = '';
    vp.value = Math.ceil((valueRows.value.length) / VPAGE);
  } else {
    if (!v.diff) v.pub = { name: v.name, parent: v.parent, synonyms: [...v.synonyms], status: v.status, note: v.note };
    Object.assign(v, f, { diff: v.diff ?? '修改' });
    touch(`修改取值“${f.name}”`);
  }
  vOpen.value = false;
  message.success(`已保存到草稿 ${draft.value?.id ?? ''}`);
}
async function toggleValue(r: Row) {
  const v = cur.value.values.find((x) => x.key === r.key);
  if (!v) return;
  const off = v.status === '启用';
  const used = off ? usageRows.value.length : 0;
  const ok = await confirm.ask({
    title: off ? '停用取值' : '启用取值', target: `${cur.value.name} · ${v.name}（${v.code}）`, tag: `草稿 ${draft.value?.id}`,
    fields: [{ label: '当前状态', value: v.status }, { label: cur.value.countLabel ?? '记录数', value: v.count != null ? fmtInt(v.count) : '--' }],
    impact: off ? `停用后问数不再把它当作筛选候选，同义词随之失效；历史数据仍按原取值统计。本${cur.value.kind}有 ${used} 项使用，发布前会出现在影响分析里。` : '启用后问数可以按它筛选和拆分。',
    okText: off ? '停用' : '启用', danger: off,
  });
  if (!ok) return;
  if (!v.diff) v.pub = { status: v.status };
  v.status = off ? '停用' : '启用';
  v.diff = v.diff ?? '修改';
  touch(`${off ? '停用' : '启用'}取值“${v.name}”`);
  message.success(`已${off ? '停用' : '启用'}，保存到草稿 ${draft.value?.id ?? ''}`);
}

// —— 同义词与问数映射 ——
interface Hit { item: string; itemName: string; value: string }
/** 全部维度和码表里，说法 → 取值（启用的取值名称、同义词、映射） */
const PHRASES = computed(() => {
  const m = new Map<string, Hit[]>();
  const add = (p: string, h: Hit) => { const a = m.get(p) ?? []; if (!a.some((x) => x.item === h.item && x.value === h.value)) a.push(h); m.set(p, a); };
  visible.value.forEach((d) => {
    d.values.filter((v) => live(v, ver.value) && v.status === '启用' && inView(v.draft)).forEach((v) => {
      add(v.name, { item: d.id, itemName: d.name, value: v.name });
      v.synonyms.forEach((s) => add(s, { item: d.id, itemName: d.name, value: v.name }));
    });
    d.mappings.filter((x) => inView(x.draft)).forEach((x) => x.targets.forEach((t) => add(x.phrase, { item: d.id, itemName: d.name, value: t })));
  });
  return m;
});
const hitsOf = (p: string) => PHRASES.value.get(p) ?? [];
type MapRow = DimMapping & { auto?: string; diffTag?: string };
const mapRows = computed<MapRow[]>(() => {
  const d = cur.value;
  const explicit = d.mappings.filter((m) => inView(m.draft) && (!ver.value || !m.diff));
  const seen = new Set(explicit.map((m) => m.phrase));
  const rows: MapRow[] = explicit.map((m) => ({ ...m, diffTag: isDraft.value ? m.diff : undefined }));
  valueRows.value.filter((v) => v.status === '启用').forEach((v) => {
    const add = (p: string, src: '取值同义词' | '取值名称') => {
      if (seen.has(p)) return;
      const others = hitsOf(p).filter((h) => !(h.item === d.id && h.value === v.name));
      if (src === '取值名称' && !others.length) return;
      seen.add(p);
      rows.push({ key: `${p}>${v.name}`, phrase: p, targets: [v.name], match: '精确', source: src, hits: hitsFor(p), auto: others.map((h) => `${h.itemName} · ${h.value}`).join('、'), diffTag: v.diffTag });
    };
    add(v.name, '取值名称');
    v.synonyms.forEach((s) => add(s, '取值同义词'));
  });
  return rows.map((r) => {
    if (r.auto !== undefined || r.conflict) return r;
    const others = hitsOf(r.phrase).filter((h) => !(h.item === d.id && r.targets.includes(h.value)));
    return others.length ? { ...r, auto: others.map((h) => `${h.itemName} · ${h.value}`).join('、') } : r;
  });
});
const hitsFor = (p: string) => { let h = 0; for (const c of p) h = (h * 31 + c.charCodeAt(0)) % 997; return 20 + h * 3; };
const conflictText = (r: MapRow) => r.conflict ?? (r.auto ? `与 ${r.auto} 同名` : '');
const conf = computed({ get: () => q('conf') === '1', set: (v) => setQ({ conf: v ? '1' : undefined, vp: undefined }) });
const shownMaps = computed(() => (conf.value ? mapRows.value.filter((r) => conflictText(r)) : mapRows.value));
const conflictCount = computed(() => mapRows.value.filter((r) => conflictText(r)).length);
const mapCols = [
  { title: '说法', key: 'phrase', width: 160 },
  { title: '匹配到', key: 'targets', width: 260 },
  { title: '匹配方式', dataIndex: 'match', width: 88 },
  { title: '来源', dataIndex: 'source', width: 104 },
  { title: '近 7 天命中', key: 'hits', width: 104, align: 'right' as const },
  { title: '歧义与处理', key: 'conflict' },
];
const mpager = computed(() => ({ current: vp.value, pageSize: 8, showSizeChanger: false, showTotal: (t: number) => `共 ${t} 条`, size: 'small' as const, onChange: (p: number) => { vp.value = p; } }));

// 试一试：输入问句里的词，看会匹配到哪些取值
const tryText = ref(q('try'));
watch(() => route.query.try, (v) => { tryText.value = (v as string) || ''; });
const tryResult = computed(() => {
  const p = tryText.value.trim();
  if (!p) return null;
  const exact = hitsOf(p);
  const contains: Hit[] = [];
  if (p.length >= 2) {
    PHRASES.value.forEach((hs, k) => { if (k !== p && k.includes(p)) hs.forEach((h) => { if (!contains.some((x) => x.item === h.item && x.value === h.value) && !exact.some((x) => x.item === h.item && x.value === h.value)) contains.push(h); }); });
  }
  const rule = visible.value.flatMap((d) => d.mappings).find((m) => m.phrase === p && inView(m.draft));
  return { exact, contains: contains.slice(0, 6), more: Math.max(0, contains.length - 6), rule };
});
const runTry = (v: string) => setQ({ try: v.trim() || undefined });

// 新增同义词 / 映射（只在草稿里），实时提示重名
const mOpen = ref(false);
const mForm = reactive<{ phrase: string; targets: string[]; match: MapMatch; resolve: string }>({ phrase: '', targets: [], match: '精确', resolve: '' });
const TARGET_OPTIONS = computed(() => valueRows.value.filter((v) => v.status === '启用').map((v) => ({ value: v.name, label: `${v.name}（${v.code}）` })));
const mHits = computed(() => (mForm.phrase.trim() ? hitsOf(mForm.phrase.trim()).filter((h) => !(h.item === cur.value.id && mForm.targets.includes(h.value))) : []));
const openMap = () => { Object.assign(mForm, { phrase: '', targets: [], match: '精确', resolve: '' }); mOpen.value = true; };
function saveMap() {
  const p = mForm.phrase.trim();
  if (!p || !mForm.targets.length) return;
  const others = mHits.value.map((h) => `${h.itemName} · ${h.value}`).join('、');
  cur.value.mappings.unshift({
    key: `${p}>${mForm.targets.join('|')}`, phrase: p, targets: [...mForm.targets], match: mForm.targets.length > 1 ? '多值' : mForm.match, source: '业务知识', hits: 0,
    conflict: others ? `与 ${others} 同名` : undefined, resolve: mForm.resolve || undefined, draft: draft.value?.id, diff: '新增',
  });
  touch(`新增说法“${p}”`);
  mOpen.value = false; vp.value = 1;
  message.success(`已保存到草稿 ${draft.value?.id ?? ''}`);
}

// —— 定义：问数设置在草稿里可编辑 ——
const editing = ref(false);
const form = reactive<DimQa & { desc: string }>({ defaultLevel: '', unspecified: '', nullAs: '', sort: '', note: '', desc: '' });
const snap = ref('');
function beginEdit() { Object.assign(form, { ...cur.value.qa, desc: cur.value.desc }); snap.value = JSON.stringify(form); editing.value = true; }
const dirty = computed(() => editing.value && JSON.stringify(form) !== snap.value);
const guard = useUnsavedGuard(dirty);
function save() {
  const { desc, ...rest } = form;
  Object.assign(cur.value.qa, rest);
  cur.value.desc = desc;
  editing.value = false;
  touch('修改问数设置');
  message.success(`已保存到草稿 ${draft.value?.id ?? ''}`);
}
const cancelEdit = () => guard(() => { editing.value = false; });
const LEVEL_OPTIONS = computed(() => levels.value.map((l) => ({ value: l.name, label: l.name })));

const DEF_ITEMS = computed(() => {
  const d = cur.value;
  return [
    { k: '取值来源', v: d.valueSrc }, { k: '同步方式', v: d.sync },
    { k: '物理字段', v: before.value?.column ?? d.column, num: true }, { k: '负责人', v: d.owner },
    { k: '生效版本', v: (() => {
      const last = d.history.find((h) => h.cr && h.status !== '草稿' && (!ver.value || h.ver <= ver.value));
      return `随本体发布 ${d.since} 生效${last && last.ver !== d.since ? `，最近一次 ${last.ver}（${last.cr}）` : ''}`;
    })() },
    { k: '分组', v: d.group },
  ];
});
const levelCols = [
  { title: '层级', key: 'name', width: 160 }, { title: 'API 名', dataIndex: 'api', width: 140 },
  { title: '取自', dataIndex: 'source', width: 240 }, { title: '取值数', key: 'count', width: 96, align: 'right' as const }, { title: '说明', key: 'note' },
];
const CHECKS = computed(() => {
  const rows: { code: string; name: string; result: '通过' | '警告' | '阻断'; note: string }[] = [];
  const vals = valueRows.value;
  const dupCode = vals.find((v, i) => vals.findIndex((x) => x.level === v.level && x.code === v.code) !== i);
  rows.push(dupCode ? { code: 'V5', name: '取值唯一', result: '阻断', note: `编码 ${dupCode.code} 重复` } : { code: 'V5', name: '取值唯一', result: '通过', note: `${vals.length} 个取值，同一层级编码和名称不重复` });
  rows.push(levels.value.some((l) => l.name === qa.value.defaultLevel) ? { code: 'V6', name: '默认层级', result: '通过', note: `问数未指明层级时按“${qa.value.defaultLevel}”` } : { code: 'V6', name: '默认层级', result: '阻断', note: '默认层级不在层级里' });
  rows.push(conflictCount.value ? { code: 'V9', name: '同义词歧义', result: '警告', note: `${conflictCount.value} 个说法有歧义，已写处理方式的不阻断发布` } : { code: 'V9', name: '同义词歧义', result: '通过', note: '没有重名的说法' });
  const off = vals.filter((v) => v.status === '停用' && (v.count ?? 0) > 0);
  rows.push(off.length ? { code: 'V12', name: '停用取值', result: '警告', note: `${off.map((v) => v.name).join('、')} 已停用但仍有数据` } : { code: 'V12', name: '停用取值', result: '通过', note: '停用的取值没有当前数据' });
  return rows;
});
const checkClass = (r: string) => (r === '阻断' ? 'c-error' : r === '警告' ? 'c-warning' : 'c-3');

// —— 使用情况 / 版本历史 ——
const TABLE_PAGER = { pageSize: 10, showSizeChanger: false, showTotal: (t: number) => `共 ${t} 条`, size: 'small' as const };
const usageRows = computed(() => [...metricUsage(cur.value), ...cur.value.usage]);
const usageCols = [{ title: '类型', dataIndex: 'type', width: 96 }, { title: '名称', key: 'name', width: 320 }, { title: '使用方式', dataIndex: 'note' }];
const usageSummary = computed(() => {
  const m = new Map<string, number>();
  usageRows.value.forEach((u) => m.set(u.type, (m.get(u.type) ?? 0) + 1));
  return [...m].map(([k, v]) => `${k} ${v}`).join(' · ');
});
const histRows = computed<DimHistory[]>(() => (ver.value ? cur.value.history.filter((h) => h.ver !== '—' && h.ver <= ver.value!) : cur.value.history));
const histCols = [
  { title: '本体发布', dataIndex: 'ver', width: 120 }, { title: '变更单', key: 'cr', width: 120 }, { title: '状态', key: 'status', width: 96 },
  { title: '变更说明', dataIndex: 'change' }, { title: '操作人', dataIndex: 'by', width: 96 }, { title: '时间', dataIndex: 'at', width: 168 },
];
const crLink = (id: string) => router.push({ path: '/build/ontology/releases', query: { cr: id } });
const levelText = (d: DimItem) => (d.levelsBefore && ver.value && ver.value < d.levelsBefore.ver ? d.levelsBefore.levels : d.levels).map((l) => l.name).join(' › ');
const hasDraft = (d: DimItem) => !readonly.value && d.values.some((v) => v.draft) ;
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="readonly ? reason : '新维度需要先在对象类型里有对应属性'">
      <a-button :disabled="true">新建维度</a-button>
    </a-tooltip>
  </Teleport>

  <div class="dm">
    <div class="panel dm-list">
      <div class="dm-filter">
        <a-input-search :value="kw" placeholder="搜索名称、API 名、对象或取值" allow-clear @change="(e: any) => (kw = e.target.value)" />
        <div class="dm-selects">
          <a-segmented v-model:value="kind" :options="KIND_OPTIONS" size="small" />
          <a-select v-model:value="grp" :options="GROUP_OPTIONS" size="small" class="dm-sel" />
        </div>
      </div>
      <ul class="dm-items">
        <li v-for="d in list" :key="d.id" :class="{ active: d.id === cur?.id }" @click="pick(d.id)">
          <div class="dm-row">
            <span class="t-14 dm-name">{{ d.name }}</span>
            <span v-if="hasDraft(d)" class="t-12 c-3">有草稿</span>
            <span v-else class="t-12 c-3">{{ d.kind }}</span>
          </div>
          <div class="dm-row">
            <span class="t-12 c-3 dm-ell">{{ d.kind === '维度' ? levelText(d) : d.attr }}</span>
            <span class="t-12 c-3 num">{{ d.values.filter((v) => live(v, ver) && !v.draft).length }} 个取值</span>
          </div>
        </li>
      </ul>
      <div class="dm-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="filtered.length" :size="LPAGE" unit="项" class="dm-pager" empty-text="没有匹配的维度或码表" />
      </div>
    </div>

    <div v-if="cur" class="panel dm-detail">
      <DraftBanner v-if="isDraft && draft" :draft="draft"
        @submit="message.success(`${draft.id} 已提交审核，可在“变更与发布”查看`)" @discard="setView('published')" />
      <div class="dm-head">
        <span class="t-16 fw-600 c-1">{{ cur.name }}</span>
        <span class="t-14 c-3 num">{{ cur.api }}</span>
        <a-tag>{{ cur.kind }}</a-tag>
        <span class="t-14 c-2">
          <ObjRef kind="object-type" :id="cur.object">{{ cur.objectName }}</ObjRef>
          <span class="c-3"> · {{ curAttr }}</span>
        </span>
        <a-segmented v-if="draft && !readonly" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => guard(() => { editing = false; setView(v); })" />
        <span class="dm-sp" />
        <a-tooltip v-if="readonly" :title="reason"><a-button type="primary" disabled>发起变更</a-button></a-tooltip>
        <a-button v-else-if="!draft" type="primary" @click="openChange">发起变更</a-button>
        <a-button v-else-if="!isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
      </div>
      <a-tabs v-model:activeKey="tab" class="dm-tabs">
        <a-tab-pane key="def" tab="定义" />
        <a-tab-pane key="values" :tab="cur.kind === '码表' ? '取值字典' : '取值'" />
        <a-tab-pane key="synonyms" tab="同义词与问数映射" />
        <a-tab-pane key="usage" tab="使用情况" />
        <a-tab-pane key="history" tab="版本历史" />
      </a-tabs>

      <!-- 定义 -->
      <div v-if="tab === 'def'" class="dm-body dm-def">
        <div class="dm-def-left">
          <a-descriptions :column="2" bordered size="small" class="dm-desc">
            <a-descriptions-item label="显示名">{{ cur.name }}</a-descriptions-item>
            <a-descriptions-item label="API 名"><span class="num">{{ cur.api }}</span></a-descriptions-item>
            <a-descriptions-item label="来源对象">
              <ObjRef kind="object-type" :id="cur.object">{{ cur.objectName }}</ObjRef>
            </a-descriptions-item>
            <a-descriptions-item label="来源属性">{{ curAttr }}</a-descriptions-item>
            <a-descriptions-item v-for="d in DEF_ITEMS" :key="d.k" :label="d.k"><span :class="{ num: d.num }">{{ d.v }}</span></a-descriptions-item>
            <a-descriptions-item label="说明" :span="2">{{ curDesc }}</a-descriptions-item>
          </a-descriptions>
          <div class="dm-sec t-14 fw-600 dm-sec-row">
            <span>层级</span>
            <span class="t-12 c-3 fw-400">{{ levels.length > 1 ? `从上到下逐级下钻：${levels.map((l) => l.name).join(' › ')}` : '单层' }}{{ before ? `（${before.ver} 起改为 ${cur.levels.map((l) => l.name).join(' › ')}）` : '' }}</span>
          </div>
          <a-table :columns="levelCols" :data-source="levels" :pagination="false" size="small" row-key="api">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'name'">
                <span class="dm-nowrap"><span class="c-1">{{ record.name }}</span><a-tag v-if="record.name === qa.defaultLevel" class="dm-tag-gap">问数默认</a-tag></span>
              </template>
              <template v-else-if="column.key === 'count'"><span class="num">{{ fmtInt(record.count) }}</span></template>
              <template v-else-if="column.key === 'note'"><span class="c-3">{{ record.note || '--' }}</span></template>
            </template>
          </a-table>
          <div v-if="before?.footnote" class="t-12 c-3">{{ before.footnote }}</div>
          <div v-else-if="cur.id === 'org'" class="t-12 c-3">门店的“区域”（华东一区—五区）见“运营片区”；按行政省份统计见“行政区域”。</div>
        </div>
        <div class="dm-def-right">
          <div class="dm-sec t-14 fw-600 dm-sec-row">
            <span>问数设置</span>
            <a-space v-if="editing">
              <a-button size="small" @click="cancelEdit">取消</a-button>
              <a-button size="small" :disabled="!dirty" @click="save">保存到草稿</a-button>
            </a-space>
            <a-tooltip v-else :title="editTip"><a-button size="small" :disabled="!canEdit" @click="beginEdit">编辑</a-button></a-tooltip>
          </div>
          <a-descriptions v-if="!editing" :column="1" bordered size="small" class="dm-desc">
            <a-descriptions-item label="默认层级">{{ qa.defaultLevel }}</a-descriptions-item>
            <a-descriptions-item label="未指明层级">{{ qa.unspecified }}</a-descriptions-item>
            <a-descriptions-item label="空值显示为">{{ qa.nullAs }}</a-descriptions-item>
            <a-descriptions-item label="排序">{{ qa.sort }}</a-descriptions-item>
            <a-descriptions-item label="补充说明">{{ qa.note || '--' }}</a-descriptions-item>
          </a-descriptions>
          <a-form v-else layout="horizontal" :label-col="{ style: { width: '96px' } }" class="dm-form">
            <a-form-item label="默认层级"><a-select v-model:value="form.defaultLevel" :options="LEVEL_OPTIONS" /></a-form-item>
            <a-form-item label="未指明层级"><a-input v-model:value="form.unspecified" /></a-form-item>
            <a-form-item label="空值显示为"><a-input v-model:value="form.nullAs" /></a-form-item>
            <a-form-item label="排序"><a-input v-model:value="form.sort" /></a-form-item>
            <a-form-item label="补充说明"><a-textarea v-model:value="form.note" :rows="2" /></a-form-item>
            <a-form-item label="说明"><a-textarea v-model:value="form.desc" :rows="2" /></a-form-item>
          </a-form>
          <div class="dm-sec t-14 fw-600">发布校验</div>
          <div v-for="c in CHECKS" :key="c.code" class="dm-check">
            <span class="t-12 c-3 num dm-check-code">{{ c.code }}</span>
            <span class="t-14 c-1 dm-check-name">{{ c.name }}</span>
            <span class="t-12" :class="checkClass(c.result)">{{ c.result }}</span>
            <span class="t-12 c-3 dm-check-note">{{ c.note }}</span>
          </div>
          <div class="t-12 c-3">维度和码表只随本体整版发布生效；问数始终使用已发布版本 {{ app.ontologyVersion }}。</div>
        </div>
      </div>

      <!-- 取值 -->
      <div v-else-if="tab === 'values'" class="dm-body">
        <div class="dm-bar">
          <a-select v-if="multiLevel" v-model:value="lv" :options="LV_OPTIONS" size="small" class="dm-bar-sel" />
          <a-input-search :value="vq" placeholder="搜索编码、名称或同义词" allow-clear size="small" class="dm-bar-search" @change="(e: any) => (vq = e.target.value)" />
          <a-select v-model:value="vs" :options="VS_OPTIONS" size="small" class="dm-bar-sel" />
          <span class="t-12 c-3">{{ valueSummary }}</span>
          <span class="dm-sp" />
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit" @click="openValue()">新增取值</a-button></a-tooltip>
        </div>
        <a-table :columns="valueCols" :data-source="shownValues" :pagination="vpager" size="small" row-key="key" class="dm-table">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <ObjRef v-if="record.ref" :kind="record.ref.kind" :id="record.ref.id">{{ record.name }}</ObjRef>
              <span v-else :class="record.status === '停用' ? 'c-3' : 'c-1'">{{ record.name }}</span>
              <a-tag v-if="record.diffTag" class="dm-tag-gap">{{ record.diffTag }}</a-tag>
            </template>
            <template v-else-if="column.key === 'parent'"><span class="c-2">{{ record.parent || '--' }}</span></template>
            <template v-else-if="column.key === 'synonyms'">
              <span v-if="record.synonyms.length" class="c-2 dm-ell1" :title="record.synonyms.join('、')">{{ record.synonyms.join('、') }}</span>
              <span v-else class="c-3">--</span>
            </template>
            <template v-else-if="column.key === 'count'"><span class="num">{{ record.count != null ? fmtInt(record.count) : '--' }}</span></template>
            <template v-else-if="column.key === 'status'"><span :class="record.status === '停用' ? 'c-3' : 'c-1'">{{ record.status }}</span></template>
            <template v-else-if="column.key === 'note'">
              <span class="c-3 dm-ell1" :title="[record.src, record.note].filter(Boolean).join(' · ')">{{ [record.src, record.note].filter(Boolean).join(' · ') || '--' }}</span>
            </template>
            <template v-else-if="column.key === 'op'">
              <a-tooltip :title="editTip">
                <a-space :size="12">
                  <a :class="{ 'dm-off': !canEdit }" @click="canEdit && openValue(record)">编辑</a>
                  <a :class="{ 'dm-off': !canEdit }" @click="canEdit && toggleValue(record)">{{ record.status === '启用' ? '停用' : '启用' }}</a>
                </a-space>
              </a-tooltip>
            </template>
          </template>
        </a-table>
        <div class="t-12 c-3">{{ cur.valuesNote ? `${cur.valuesNote}。` : '' }}取值来自 {{ cur.valueSrc }}；停用的取值不参与问数匹配，历史数据仍按原取值统计。</div>
      </div>

      <!-- 同义词与问数映射 -->
      <div v-else-if="tab === 'synonyms'" class="dm-body">
        <div class="dm-try">
          <span class="t-14 fw-600 c-1">试一试</span>
          <a-input-search v-model:value="tryText" placeholder="输入问句里的说法，例如 华东、前置仓、已发货" enter-button="匹配" size="small" class="dm-try-input" @search="runTry" />
          <template v-if="tryResult">
            <span v-if="!tryResult.exact.length && !tryResult.contains.length" class="t-12 c-warning">没有匹配到取值，问数会追问用户</span>
            <template v-else>
              <span v-if="tryResult.exact.length" class="t-12 c-2">精确：{{ tryResult.exact.map((h) => `${h.itemName} · ${h.value}`).join('、') }}</span>
              <span v-if="tryResult.contains.length" class="t-12 c-3">包含：{{ tryResult.contains.map((h) => `${h.itemName} · ${h.value}`).join('、') }}{{ tryResult.more ? ` 等 ${tryResult.contains.length + tryResult.more} 个` : '' }}</span>
              <span v-if="tryResult.exact.length + tryResult.contains.length > 1" class="t-12 c-warning">有歧义{{ tryResult.rule?.resolve ? `：${tryResult.rule.resolve}` : '，精确命中优先，仍有多个时追问' }}</span>
            </template>
          </template>
          <span v-else class="t-12 c-3">在全部 {{ visible.length }} 个维度和码表里按名称、同义词和映射匹配</span>
        </div>
        <div class="dm-bar">
          <span class="t-12 c-2">共 {{ mapRows.length }} 个说法</span>
          <span v-if="conflictCount" class="t-12 c-warning">{{ conflictCount }} 个有歧义</span>
          <a-checkbox v-model:checked="conf" class="t-12">只看有歧义的</a-checkbox>
          <span class="dm-sp" />
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit" @click="openMap">新增说法</a-button></a-tooltip>
        </div>
        <a-table :columns="mapCols" :data-source="shownMaps" :pagination="mpager" size="small" row-key="key" class="dm-table">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'phrase'">
              <span class="c-1">{{ record.phrase }}</span>
              <a-tag v-if="record.diffTag" class="dm-tag-gap">{{ record.diffTag }}</a-tag>
            </template>
            <template v-else-if="column.key === 'targets'">
              <span v-if="record.targets.length" class="c-2">{{ record.targets.join('、') }}</span>
              <span v-else class="c-warning">没有对应取值</span>
            </template>
            <template v-else-if="column.key === 'hits'"><span class="num">{{ fmtInt(record.hits) }}</span></template>
            <template v-else-if="column.key === 'conflict'">
              <span v-if="conflictText(record)" class="c-warning">{{ conflictText(record) }}</span>
              <span v-if="record.resolve" class="c-3">{{ conflictText(record) ? '；' : '' }}{{ record.resolve }}</span>
              <span v-if="!conflictText(record) && !record.resolve" class="c-3">--</span>
            </template>
          </template>
        </a-table>
        <div class="t-12 c-3">匹配顺序：精确命中 › 同义词 › 包含匹配；同一说法对应多个取值时按“处理方式”回答，没有处理方式时追问用户。有歧义不阻断发布（V9 警告）。</div>
      </div>

      <!-- 使用情况 -->
      <div v-else-if="tab === 'usage'" class="dm-body">
        <div class="t-14 c-2">{{ usageRows.length ? `共 ${usageRows.length} 项使用本${cur.kind}：${usageSummary}` : `暂无指标、视图或规则使用本${cur.kind}` }}</div>
        <a-table v-if="usageRows.length" :columns="usageCols" :data-source="usageRows" :pagination="usageRows.length > 10 ? TABLE_PAGER : false" size="middle" :row-key="(r: any) => r.type + r.name">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <ObjRef v-if="record.hover" :kind="record.hover.kind" :id="record.hover.id">{{ record.name }}</ObjRef>
              <span v-else class="c-1">{{ record.name }}</span>
            </template>
          </template>
        </a-table>
        <div class="t-12 c-3">指标按“允许的维度”统计（见指标口径）。停用取值、改层级或改同义词时，以上各项会出现在变更单的影响分析里。</div>
      </div>

      <!-- 版本历史 -->
      <div v-else class="dm-body">
        <a-table :columns="histCols" :data-source="histRows" :pagination="histRows.length > 10 ? TABLE_PAGER : false" size="middle" :row-key="(r: any) => r.ver + (r.cr ?? '') + r.at">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'cr'">
              <a v-if="record.cr" class="num" @click="crLink(record.cr)">{{ record.cr }}</a>
              <span v-else class="c-3">启用变更单前</span>
            </template>
            <template v-else-if="column.key === 'status'">
              <span :class="record.status === '已驳回' ? 'c-error' : record.status === '生效中' ? 'c-1' : 'c-3'">{{ record.status }}</span>
            </template>
          </template>
        </a-table>
        <div class="t-12 c-3">维度和码表只随本体整版发布生效；回滚会生成新的变更单，经审核后随下一次发布生效。</div>
      </div>
    </div>
  </div>

  <StartChangeModal v-model:open="changeOpen" :target-name="cur ? `${cur.name}（${cur.api}）` : ''" @ok="start" />

  <a-modal v-model:open="vOpen" :title="vEdit ? `编辑取值 · ${vEdit.name}` : `新增取值 · ${cur?.name}`" :width="560" ok-text="保存到草稿" :ok-button-props="{ disabled: !!vErr }" @ok="saveValue">
    <a-form layout="horizontal" :label-col="{ style: { width: '80px' } }" class="dm-form">
      <a-form-item label="层级" required><a-select v-model:value="vForm.level" :options="LEVEL_OPTIONS" :disabled="!!vEdit" /></a-form-item>
      <a-form-item v-if="parentLevel" label="上级"><a-select v-model:value="vForm.parent" :options="PARENT_OPTIONS" show-search allow-clear :placeholder="`选择${parentLevel}`" /></a-form-item>
      <a-form-item label="编码" required><a-tooltip :title="vEdit ? '已发布取值的编码不可修改（V7）' : ''"><a-input v-model:value="vForm.code" :disabled="!!vEdit" placeholder="与源系统编码一致" /></a-tooltip></a-form-item>
      <a-form-item label="名称" required><a-input v-model:value="vForm.name" /></a-form-item>
      <a-form-item label="同义词"><a-select v-model:value="vForm.synonyms" mode="tags" :open="false" placeholder="输入后回车" /></a-form-item>
      <a-form-item label="状态"><a-radio-group v-model:value="vForm.status"><a-radio value="启用">启用</a-radio><a-radio value="停用">停用</a-radio></a-radio-group></a-form-item>
      <a-form-item label="说明"><a-input v-model:value="vForm.note" /></a-form-item>
    </a-form>
    <div v-if="vErr" class="t-12 c-error">{{ vErr }}</div>
    <div v-for="c in synConflicts" :key="c" class="t-12 c-warning">{{ c }}，问数会出现歧义</div>
  </a-modal>

  <a-modal v-model:open="mOpen" :title="`新增说法 · ${cur?.name}`" :width="560" ok-text="保存到草稿" :ok-button-props="{ disabled: !mForm.phrase.trim() || !mForm.targets.length }" @ok="saveMap">
    <a-form layout="horizontal" :label-col="{ style: { width: '80px' } }" class="dm-form">
      <a-form-item label="说法" required><a-input v-model:value="mForm.phrase" placeholder="用户在问题里的说法" /></a-form-item>
      <a-form-item label="匹配到" required><a-select v-model:value="mForm.targets" mode="multiple" :options="TARGET_OPTIONS" option-filter-prop="label" placeholder="可选多个取值" /></a-form-item>
      <a-form-item label="匹配方式"><a-radio-group v-model:value="mForm.match"><a-radio value="精确">精确</a-radio><a-radio value="包含">包含</a-radio></a-radio-group></a-form-item>
      <a-form-item label="处理方式"><a-input v-model:value="mForm.resolve" placeholder="有歧义时问数怎么理解" /></a-form-item>
    </a-form>
    <div v-if="mHits.length" class="t-12 c-warning">“{{ mForm.phrase.trim() }}”已对应 {{ mHits.map((h) => `${h.itemName} · ${h.value}`).join('、') }}，保存后会标为有歧义，建议写明处理方式</div>
    <div v-else-if="mForm.phrase.trim()" class="t-12 c-3">没有重名的说法</div>
  </a-modal>
</template>

<style scoped>
.dm { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.dm-list { width: 300px; flex: none; }
.dm-filter { padding: 12px 12px 8px; display: flex; flex-direction: column; gap: 8px; flex: none; }
.dm-selects { display: flex; gap: 8px; align-items: center; }
.dm-sel { flex: 1; min-width: 0; }
.dm-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; }
.dm-items li { padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.dm-items li:hover { background: var(--g3); }
.dm-items li.active { background: var(--brand-bg); }
.dm-items li.active .dm-name { color: var(--brand); }
.dm-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.dm-ell { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dm-ell1 { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dm-pager { flex: 1; min-height: 0; }
.dm-foot { padding: 8px 12px 8px 16px; border-top: var(--border); flex: none; display: flex; align-items: center; justify-content: space-between; }
.dm-detail { flex: 1; min-width: 0; }
.dm-head { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; flex: none; }
.dm-sp { flex: 1; }
.dm-tabs { padding: 0 16px; flex: none; }
.dm-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.dm-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
.dm-def { flex-direction: row; gap: 16px; }
.dm-def-left { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.dm-def-right { width: 440px; flex: none; display: flex; flex-direction: column; gap: 8px; padding-left: 16px; border-left: var(--border); }
.dm-sec { margin-top: 4px; }
.dm-sec-row { display: flex; justify-content: space-between; align-items: baseline; }
.dm-desc :deep(.ant-descriptions-item-label) { width: 112px; }
.dm-form :deep(.ant-form-item) { margin-bottom: 12px; }
.dm-check { display: flex; align-items: baseline; gap: 8px; padding: 6px 0; border-bottom: var(--border); }
.dm-check-code { width: 32px; flex: none; }
.dm-check-name { width: 88px; flex: none; }
.dm-check-note { flex: 1; min-width: 0; text-align: right; }
.dm-bar { display: flex; align-items: center; gap: 12px; flex: none; }
.dm-bar-sel { width: 120px; }
.dm-bar-search { width: 240px; }
.dm-table :deep(.ant-table-cell) { white-space: nowrap; }
.dm-tag-gap { margin-left: 8px; }
.dm-nowrap { white-space: nowrap; }
.dm-off { color: var(--g6); cursor: not-allowed; }
.dm-try { display: flex; align-items: center; gap: 12px; padding: 8px 12px; border: var(--border); border-radius: var(--radius); background: var(--g2); flex: none; min-width: 0; }
.dm-try-input { width: 360px; flex: none; }
</style>
