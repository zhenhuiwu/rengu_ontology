<script setup lang="ts">
/**
 * 业务知识：左侧知识列表，右侧详情（内容 / 问数中的作用 / 引用 / 版本历史）。
 * 收录术语与同义词、业务规则与计算说明、财务日历与大促期、挂在对象和指标上的口径说明。
 * URL：?k= 选中条目，?tab= 页签，?q= / ?type= / ?st= 列表筛选，?view=draft 草稿视图，?ver= 历史版本只读。
 * 草稿逻辑用公共的 useDraftView('knowledge')：已发布只读，唯一入口“发起变更”；新建知识、停用都记入变更单，随整版发布生效。
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
import { CHANGE_REQUESTS, CURRENT_USER } from '@/mock/changes';
import { METRICS } from '@/mock/metrics';
import { Q_OBJECT_TYPES } from '@/mock/quality';
import {
  KNOWLEDGE, K_TYPES, CONFLICT_RULE, synonymConflicts,
  type KEntry, type KFields, type KStatus, type KType, type KApply,
} from '@/mock/knowledge';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import { allDrafts, draftOf, useDraftView } from './parts/useDraftView';

const route = useRoute();
const router = useRouter();
const app = useAppStore();
const confirm = useConfirmStore();
const { readonly, reason } = useReadonly();
const pad = (n: number) => String(n).padStart(2, '0');
const nowText = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };

const entries = reactive<KEntry[]>(KNOWLEDGE.map((e) => ({ ...e })));
/** 查看历史版本时只列出该版本已发布的条目，状态按该版本算 */
const visible = computed(() => {
  const ver = app.viewVersion;
  if (!readonly.value || !ver) return entries;
  return entries.filter((e) => !!e.since && e.since <= ver);
});
const statusOf = (e: KEntry): KStatus => {
  const ver = app.viewVersion;
  if (readonly.value && ver) return e.stoppedIn && e.stoppedIn <= ver ? '已停用' : '已发布';
  return e.status;
};
/** 条目所在的未发布变更单：以 useDraftView 登记为准，没有登记的用条目自带的 cr */
const crOf = (e: KEntry) => draftOf('knowledge', e.id)?.id ?? (e.status === '草稿' ? e.cr : undefined);
/** 显示状态：从变更单推出来（与指标口径一致）。变更单审核中 → 橙色“审核中”；只有草稿 → 灰色“草稿中”（问数仍用已发布版）；已发布、已停用灰色。 */
type Shown = '已发布' | '草稿中' | '审核中' | '已停用';
const shownStatus = (e: KEntry): Shown => {
  const s = statusOf(e);
  if (readonly.value && app.viewVersion) return s === '已停用' ? '已停用' : '已发布';
  if (s === '已停用') return '已停用';
  const cr = crOf(e);
  if (!cr) return s === '草稿' ? '草稿中' : '已发布';
  return CHANGE_REQUESTS.find((c) => c.id === cr)?.status === '审核中' ? '审核中' : '草稿中';
};
const shownClass = (s: Shown) => (s === '审核中' ? 'c-warning' : 'c-3');

// —— 列表筛选（写进 URL）——
const q = (k: string) => (route.query[k] as string) || '';
const setQ = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const kw = computed({ get: () => q('q'), set: (v) => setQ({ q: v || undefined }) });
const ktype = computed({ get: () => q('type'), set: (v) => setQ({ type: v || undefined }) });
const st = computed({ get: () => q('st'), set: (v) => setQ({ st: v || undefined }) });
const TYPE_OPTIONS = [{ value: '', label: '全部类型' }, ...K_TYPES.map((t) => ({ value: t, label: t }))];
const ST_OPTIONS = [{ value: '', label: '全部状态' }, ...(['已发布', '草稿中', '审核中', '已停用'] as const).map((v) => ({ value: v, label: v }))];
const filtered = computed(() => visible.value.filter((e) =>
  (!ktype.value || e.type === ktype.value) && (!st.value || shownStatus(e) === st.value)
  && (!kw.value || e.name.includes(kw.value) || e.id.includes(kw.value) || e.synonyms.some((s) => s.includes(kw.value)))));
const LPAGE = 19;
const lpage = ref(1);
watch([kw, ktype, st], () => { lpage.value = 1; });
const list = computed(() => filtered.value.slice((lpage.value - 1) * LPAGE, lpage.value * LPAGE));

const cur = computed(() => visible.value.find((e) => e.id === route.query.k) ?? visible.value[0]);
watch(() => cur.value?.id, (id) => {
  const i = filtered.value.findIndex((e) => e.id === id);
  if (i >= 0) lpage.value = Math.floor(i / LPAGE) + 1;
}, { immediate: true });

const tab = computed({
  get: () => (route.query.tab as string) || 'content',
  set: (v) => guard(() => { editing.value = false; setQ({ tab: v }); }),
});
const pick = (id: string) => guard(() => {
  editing.value = false;
  router.replace({ query: { ...route.query, k: id, view: undefined, drawer: undefined, drawer2: undefined } });
});

// —— 草稿（公共 useDraftView，kind = knowledge）——
const dv = useDraftView('knowledge', () => cur.value?.id ?? '');
const { draft, setView } = dv;
/** 只存在于草稿里的条目（新增的），直接看草稿 */
const draftOnly = computed(() => cur.value?.status === '草稿');
const isDraft = computed(() => dv.isDraft.value || (!readonly.value && !!draft.value && draftOnly.value));
const canEdit = computed(() => isDraft.value);
const editTip = computed(() => dv.editTip.value);
const changeOpen = ref(false);
const changeTarget = ref('');
const creating = ref(false);
const openChange = (name: string, isNew = false) => { if (readonly.value) return; creating.value = isNew; changeTarget.value = name; changeOpen.value = true; };
function start(opts: { join?: string; title?: string }) {
  if (!creating.value) { dv.start(opts); return; }
  const n = entries.filter((e) => e.id.startsWith('K-N')).length + 1;
  const nid = `K-N${String(n).padStart(2, '0')}`;
  const crId = dv.start({ ...opts, target: nid });
  const at = nowText();
  const ne: KEntry = {
    id: nid, name: '未命名知识', type: (ktype.value as KType) || '术语', status: '草稿', cr: crId, updatedAt: at, owner: CURRENT_USER,
    definition: '', calc: '', synonyms: [], applies: [], period: '随下一次整版发布生效', maps: '', filters: [], tries: [], refs: [],
    history: [{ ver: '—', cr: crId, status: '草稿', by: CURRENT_USER, at, change: '新增（待填写）' }],
  };
  entries.unshift(ne);
  router.replace({ query: { ...route.query, k: nid, tab: 'content', view: 'draft', type: undefined, st: undefined, q: undefined } })
    .then(() => beginEdit());
}

/** 当前视图下的字段：草稿视图叠加草稿改动 */
const view = computed<KFields & { status: KStatus }>(() => {
  const e = cur.value;
  const base = { name: e.name, type: e.type, definition: e.definition, calc: e.calc, synonyms: e.synonyms, applies: e.applies, period: e.period, owner: e.owner, maps: e.maps, filters: e.filters, status: statusOf(e) };
  return isDraft.value ? { ...base, ...(e.draft ?? {}), status: e.draftStatus ?? base.status } : base;
});
/** 草稿里改了的字段：新增条目全部算“新增” */
const mark = (k: keyof KFields | 'status'): '' | '修改' | '新增' => {
  if (!isDraft.value) return '';
  if (draftOnly.value) return '新增';
  if (k === 'status') return cur.value.draftStatus && cur.value.draftStatus !== cur.value.status ? '修改' : '';
  const d = cur.value.draft;
  return d && k in d && JSON.stringify(d[k]) !== JSON.stringify(cur.value[k]) ? '修改' : '';
};
const before = (k: keyof KFields) => {
  const v = cur.value[k];
  return Array.isArray(v) ? (v as unknown[]).map((x) => (typeof x === 'string' ? x : (x as KApply).name)).join('、') : String(v ?? '');
};
const appliesView = computed(() => {
  const pub = new Set(cur.value.applies.map((a) => a.kind + a.id));
  const now = view.value.applies.map((a) => ({ ...a, mark: isDraft.value && (draftOnly.value || !pub.has(a.kind + a.id)) ? '新增' : '' }));
  const removed = isDraft.value && !draftOnly.value ? cur.value.applies.filter((a) => !view.value.applies.some((b) => b.kind === a.kind && b.id === a.id)).map((a) => ({ ...a, mark: '删除' })) : [];
  return [...now, ...removed];
});

// —— 内容：草稿里可编辑 ——
interface Form { name: string; type: KType; definition: string; calc: string; synonyms: string[]; applies: string[]; period: string; owner: string }
const editing = ref(false);
const form = reactive<Form>({ name: '', type: '术语', definition: '', calc: '', synonyms: [], applies: [], period: '', owner: '' });
const snap = ref('');
const APPLY_ALL: KApply[] = [
  ...Q_OBJECT_TYPES.map((t) => ({ kind: 'object-type' as const, id: t.key, name: t.name })),
  ...METRICS.map((m) => ({ kind: 'metric' as const, id: m.id, name: m.name })),
];
const APPLY_OPTIONS = [
  { label: '对象类型', options: Q_OBJECT_TYPES.map((t) => ({ value: `object-type:${t.key}`, label: t.name })) },
  { label: '指标', options: METRICS.map((m) => ({ value: `metric:${m.id}`, label: m.name })) },
];
function beginEdit() {
  const v = view.value;
  Object.assign(form, { name: v.name, type: v.type, definition: v.definition, calc: v.calc, synonyms: [...v.synonyms], applies: v.applies.map((a) => `${a.kind}:${a.id}`), period: v.period, owner: v.owner });
  snap.value = JSON.stringify(form);
  editing.value = true;
}
const dirty = computed(() => editing.value && JSON.stringify(form) !== snap.value);
const guard = useUnsavedGuard(dirty);
function save() {
  const e = cur.value;
  const applies = form.applies.map((k) => APPLY_ALL.find((a) => `${a.kind}:${a.id}` === k)!).filter(Boolean);
  const next: Partial<KFields> = { name: form.name, type: form.type, definition: form.definition, calc: form.calc, synonyms: [...form.synonyms], applies, period: form.period, owner: form.owner };
  if (draftOnly.value) Object.assign(e, next);
  else {
    const d: Partial<KFields> = { ...(e.draft ?? {}) };
    (Object.keys(next) as (keyof KFields)[]).forEach((k) => {
      if (JSON.stringify(next[k]) === JSON.stringify(e[k])) delete d[k];
      else (d as Record<string, unknown>)[k] = next[k];
    });
    e.draft = d;
  }
  e.updatedAt = nowText();
  editing.value = false;
  message.success(`已保存到草稿 ${draft.value?.id ?? ''}`);
}
const cancelEdit = () => guard(() => { editing.value = false; });

// —— 停用：确认卡列出影响，记入变更单 ——
const refCount = (e: KEntry, t: string) => e.refs.filter((r) => r.type === t).length;
async function stop() {
  const e = cur.value;
  const words = [e.name, ...e.synonyms];
  const join = draft.value?.id;
  const ok = await confirm.ask({
    title: '停用业务知识', target: `${e.name}（${e.id} · ${e.type}）`, tag: '待确认', danger: true, okText: '记入变更单',
    fields: [
      { label: '识别词', value: words.join('、') },
      { label: '引用', value: `指标 ${refCount(e, '指标')} · 认证问答 ${refCount(e, '认证问答')} · 规则 ${refCount(e, '规则')} · 看板 ${refCount(e, '看板')}` },
      { label: '默认过滤', value: e.filters.length ? e.filters.map((f) => `${f.field} ${f.cond}`).join('；') : '无' },
      { label: '生效', value: '审核通过后随整版发布生效' },
    ],
    impact: `发布后问数不再识别“${words.slice(0, 3).join('”“')}”${e.filters.length ? '，也不再自动加默认过滤' : ''}；引用它的 ${e.refs.length} 项会出现在变更单影响分析里，认证问答需要重新认证。历史版本和已保存的回答不受影响。`,
    picker: join ? undefined : {
      label: '记入变更单', placeholder: '选择变更单', required: true,
      options: [{ value: '__new', label: `新建变更单：停用业务知识“${e.name}”` }, ...allDrafts().map((d) => ({ value: d.id, label: `${d.id}　${d.title}` }))],
    },
  });
  if (!ok) return;
  const pv = confirm.pickValue;
  const id = join ?? (pv && pv !== '__new' ? dv.start({ join: pv }) : dv.start({ title: `停用业务知识“${e.name}”` }));
  e.draftStatus = '已停用';
  setView('draft');
  message.success(`已记入 ${id}：${e.name} 停用（待审核）`);
}

// —— 问数中的作用 ——
const conflicts = computed(() => synonymConflicts(entries.filter((e) => e.status !== '已停用' || e.id === cur.value?.id)));
const words = computed(() => [view.value.name, ...view.value.synonyms]);
const isConflict = (w: string) => (conflicts.value.get(w)?.length ?? 0) > 1 && conflicts.value.get(w)!.includes(cur.value.id);
const conflictRows = computed(() => words.value.filter(isConflict).map((w) => ({
  word: w,
  others: conflicts.value.get(w)!.filter((id) => id !== cur.value.id).map((id) => entries.find((x) => x.id === id)!),
  rule: CONFLICT_RULE[w],
})));
const nameOf = (id: string) => entries.find((e) => e.id === id)?.name ?? id;
const filterCols = [{ title: '字段', dataIndex: 'field', width: 220 }, { title: '条件', dataIndex: 'cond', width: 260 }, { title: '说明', dataIndex: 'note' }];

const tryQ = ref('');
const tried = ref('');
watch(() => cur.value?.id, () => { tryQ.value = cur.value?.tries[0] ?? ''; tried.value = tryQ.value; }, { immediate: true });
const runTry = (s?: string) => { if (s) tryQ.value = s; tried.value = tryQ.value.trim(); };
const METRIC_WORDS: [string, string][] = [['含税销售额', 'gmv_tax'], ['销售额', 'gmv'], ['GMV', 'gmv'], ['毛利率', 'gross_margin'], ['毛利', 'gross_margin'], ['客单价', 'atv'], ['出库及时率', 'ship_ontime'], ['订单数', 'order_cnt'], ['有效单', 'order_cnt']];
const TIME_WORDS: [RegExp, string][] = [
  [/上个?月/, '2026-09-01 至 2026-09-30'], [/上周/, '2026-10-12 至 2026-10-18'], [/本周/, '2026-10-19 至 2026-10-25'], [/昨天/, '2026-10-24'],
  [/近\s*3\s*个月/, '2026-07-26 至 2026-10-25'], [/本财年/, 'FY2026（2026-02-01 至今）'], [/FY2026 Q3/, '2026-08-02 至 2026-10-31（财务日历）'],
];
interface TryRow { k: string; v: string; cls?: string; by?: string }
const tryRows = computed<TryRow[]>(() => {
  const s = tried.value;
  if (!s) return [];
  const rows: TryRow[] = [];
  const pool = entries.filter((e) => (e.status === '已发布' || e.id === cur.value.id) && (statusOf(e) !== '已停用' || e.id === cur.value.id));
  const hitIds = new Set<string>();
  const seen = new Set<string>();
  pool.forEach((e) => {
    const f = e.id === cur.value.id ? view.value : e;
    [f.name, ...f.synonyms].filter((w) => s.includes(w) && !seen.has(w)).forEach((w) => {
      const ids = conflicts.value.get(w);
      if (ids && ids.length > 1) {
        seen.add(w);
        rows.push({ k: `“${w}”`, v: `同义词冲突：${ids.map(nameOf).join(' / ')}。${CONFLICT_RULE[w] ?? '未设置默认，问数会先追问用户'}`, cls: 'c-warning' });
        ids.forEach((id) => hitIds.add(id));
      } else {
        seen.add(w);
        rows.push({ k: `“${w}”`, v: `${f.name}（${e.type}）→ ${f.maps || '--'}`, by: e.id });
        hitIds.add(e.id);
      }
    });
  });
  const m = METRIC_WORDS.find(([w]) => s.includes(w));
  const met = m ? METRICS.find((x) => x.id === m[1]) : undefined;
  rows.push(met ? { k: '指标', v: `${met.name} ${met.version}` } : { k: '指标', v: '未识别到指标，问数会追问要看什么', cls: 'c-3' });
  const cal = [...hitIds].map((id) => entries.find((e) => e.id === id)!).find((e) => e.type === '财务日历' && e.periods?.length);
  const t = TIME_WORDS.find(([re]) => re.test(s));
  rows.push(cal && !t ? { k: '时间', v: `${cal.periods![cal.periods!.length > 2 ? 2 : cal.periods!.length - 1].start} 至 ${cal.periods![cal.periods!.length - 1].end}（${cal.name}）` }
    : t ? { k: '时间', v: t[1] } : { k: '时间', v: '未指明，默认近 30 天（2026-09-26 至 2026-10-25）', cls: 'c-3' });
  const dim = s.match(/各(大区|区域|门店|品类|渠道|仓|品牌)/);
  if (dim) rows.push({ k: '分组', v: `按${dim[1] === '区域' ? '大区' : dim[1] === '仓' ? '仓库' : dim[1]}` });
  const fs = [...hitIds].flatMap((id) => {
    const e = entries.find((x) => x.id === id)!;
    return (id === cur.value.id ? view.value.filters : e.filters).map((f) => `${f.field} ${f.cond}`);
  });
  rows.push(fs.length ? { k: '默认过滤', v: fs.join('；') } : { k: '默认过滤', v: '无', cls: 'c-3' });
  if (!hitIds.has(cur.value.id)) rows.push({ k: '本条知识', v: `问题里没有出现“${words.value.slice(0, 3).join('”“')}”，未使用本条`, cls: 'c-3' });
  return rows;
});

// —— 引用 / 版本历史 ——
const REF_PAGE = 8;
const rpage = ref(1);
watch(() => cur.value?.id, () => { rpage.value = 1; });
const refRows = computed(() => cur.value.refs.slice((rpage.value - 1) * REF_PAGE, rpage.value * REF_PAGE));
const refCols = [{ title: '类型', dataIndex: 'type', width: 120 }, { title: '名称', key: 'name', width: 360 }, { title: '使用方式', dataIndex: 'note' }];
const refSummary = computed(() => {
  const m = new Map<string, number>();
  cur.value.refs.forEach((r) => m.set(r.type, (m.get(r.type) ?? 0) + 1));
  return [...m].map(([k, v]) => `${k} ${v}`).join(' · ');
});
const histRows = computed(() => {
  const ver = app.viewVersion;
  const rows = cur.value.history;
  return readonly.value && ver ? rows.filter((h) => h.ver !== '—' && h.ver <= ver && h.status !== '草稿') : rows;
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

const canStop = computed(() => !readonly.value && view.value.status === '已发布' && !draftOnly.value);
const canResolve = (a: KApply) => (a.kind === 'metric' ? METRICS.some((m) => m.id === a.id) : Q_OBJECT_TYPES.some((t) => t.key === a.id));
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="readonly ? reason : ''">
      <a-button :disabled="readonly" @click="openChange('新建业务知识', true)">新建知识</a-button>
    </a-tooltip>
  </Teleport>

  <div class="gl">
    <div class="panel gl-list">
      <div class="gl-filter">
        <a-input-search :value="kw" placeholder="搜索名称、编号或同义词" allow-clear @change="(e: any) => (kw = e.target.value)" />
        <div class="gl-selects">
          <a-select v-model:value="ktype" :options="TYPE_OPTIONS" size="small" class="gl-sel" />
          <a-select v-model:value="st" :options="ST_OPTIONS" size="small" class="gl-sel" />
        </div>
      </div>
      <ul class="gl-items">
        <li v-for="e in list" :key="e.id" :class="{ active: e.id === cur?.id }" :title="`${e.name} · ${e.type}${e.synonyms.length ? ` · ${e.synonyms.slice(0, 3).join('、')}` : ''} · ${e.id}`" @click="pick(e.id)">
          <div class="gl-row">
            <span class="t-14 gl-name gl-ell">{{ e.name }}</span>
            <span class="t-12 c-3 gl-ft">{{ e.type }}</span>
            <span v-if="shownStatus(e) !== '已发布'" class="t-12 num" :class="shownClass(shownStatus(e))">{{ shownStatus(e) }}{{ crOf(e) && shownStatus(e) !== '已停用' ? ` · ${crOf(e)}` : '' }}</span>
          </div>
        </li>
      </ul>
      <div class="gl-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="filtered.length" :size="LPAGE" unit="条" class="gl-pager" empty-text="没有匹配的业务知识" />
      </div>
    </div>

    <div v-if="cur" class="panel gl-detail">
      <DraftBanner v-if="isDraft && draft" :draft="draft"
        @submit="message.success(`${draft.id} 已提交审核，可在“变更与发布”查看`)" @discard="draftOnly ? message.info('这条知识只在草稿里，放弃请到变更单处理') : setView('published')" />
      <div class="gl-head">
        <span class="t-16 fw-600 c-1">{{ view.name }}</span>
        <span class="t-14 c-3 num">{{ cur.id }}</span>
        <a-tag>{{ view.type }}</a-tag>
        <a-tooltip :title="crOf(cur) && shownStatus(cur) !== '已发布' ? `${crOf(cur)} ${shownStatus(cur) === '审核中' ? '审核中' : '草稿'}，问数仍使用已发布版本` : ''">
          <a-tag :color="shownStatus(cur) === '审核中' ? 'warning' : undefined">{{ shownStatus(cur) }}</a-tag>
        </a-tooltip>
        <a-tag v-if="mark('status')" class="gl-diff">{{ mark('status') }}</a-tag>
        <span class="t-12 c-3">{{ view.owner }} · 更新于 {{ cur.updatedAt }}</span>
        <a-segmented v-if="draft && !readonly && !draftOnly" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => guard(() => { editing = false; setView(v); })" />
        <span class="gl-sp" />
        <a-tooltip v-if="readonly" :title="reason"><a-button disabled>停用</a-button></a-tooltip>
        <a-button v-else-if="canStop" @click="stop">停用</a-button>
        <a-tooltip v-if="readonly" :title="reason"><a-button disabled>发起变更</a-button></a-tooltip>
        <a-button v-else-if="!draft" type="primary" @click="openChange(`${cur.name}（${cur.id}）`)">发起变更</a-button>
        <a-button v-else-if="!isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
      </div>
      <a-tabs v-model:activeKey="tab" class="gl-tabs">
        <a-tab-pane key="content" tab="内容" />
        <a-tab-pane key="qa" tab="问数中的作用" />
        <a-tab-pane key="refs" :tab="`引用 ${cur.refs.length}`" />
        <a-tab-pane key="history" tab="版本历史" />
      </a-tabs>

      <!-- 内容 -->
      <div v-if="tab === 'content'" class="gl-body gl-two">
        <div class="gl-left">
          <div class="gl-sec t-14 fw-600 gl-sec-row">
            <span>知识内容</span>
            <a-space v-if="editing">
              <a-button size="small" @click="cancelEdit">取消</a-button>
              <a-button size="small" :disabled="!dirty" @click="save">保存到草稿</a-button>
            </a-space>
            <a-tooltip v-else :title="editTip"><a-button size="small" :disabled="!canEdit" @click="beginEdit">编辑内容</a-button></a-tooltip>
          </div>
          <a-descriptions v-if="!editing" :column="2" bordered size="small" class="gl-desc">
            <a-descriptions-item label="名称">{{ view.name }}<a-tag v-if="mark('name')" class="gl-diff">{{ mark('name') }}</a-tag></a-descriptions-item>
            <a-descriptions-item label="类型">{{ view.type }}<a-tag v-if="mark('type')" class="gl-diff">{{ mark('type') }}</a-tag></a-descriptions-item>
            <a-descriptions-item label="定义" :span="2">
              <div v-if="mark('definition') === '修改'" class="c-3 gl-del">{{ before('definition') }}</div>
              <span class="c-1">{{ view.definition || '--' }}</span><a-tag v-if="mark('definition')" class="gl-diff">{{ mark('definition') }}</a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="计算说明" :span="2">
              <div v-if="mark('calc') === '修改'" class="c-3 num gl-del">{{ before('calc') }}</div>
              <span class="num gl-ell" :title="view.calc">{{ view.calc || '--' }}</span><a-tag v-if="mark('calc')" class="gl-diff">{{ mark('calc') }}</a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="同义词" :span="2">
              <template v-if="view.synonyms.length">
                <a-tag v-for="w in view.synonyms" :key="w" :color="isConflict(w) ? 'warning' : undefined">{{ w }}</a-tag>
              </template>
              <span v-else class="c-3">--</span>
              <a-tag v-if="mark('synonyms')" class="gl-diff">{{ mark('synonyms') }}</a-tag>
              <span v-if="conflictRows.length" class="t-12 c-warning">{{ conflictRows.length }} 个词与其他知识冲突，见“问数中的作用”</span>
            </a-descriptions-item>
            <a-descriptions-item label="生效期">{{ view.period || '--' }}<a-tag v-if="mark('period')" class="gl-diff">{{ mark('period') }}</a-tag></a-descriptions-item>
            <a-descriptions-item label="负责人">{{ view.owner }}<a-tag v-if="mark('owner')" class="gl-diff">{{ mark('owner') }}</a-tag></a-descriptions-item>
            <a-descriptions-item label="状态">
              <span :class="view.status === '已停用' ? 'c-3' : 'c-1'">{{ view.status }}</span><a-tag v-if="mark('status')" class="gl-diff">{{ mark('status') }}</a-tag>
              <span v-if="mark('status') === '修改'" class="t-12 c-3">（已发布版：{{ cur.status }}）</span>
            </a-descriptions-item>
            <a-descriptions-item label="生效版本">
              <span class="num">{{ cur.since ? `${cur.since} 起` : `未发布 · ${cur.cr} 草稿` }}{{ cur.stoppedIn ? `，${cur.stoppedIn} 停用` : '' }}</span>
            </a-descriptions-item>
          </a-descriptions>
          <a-form v-else layout="horizontal" :label-col="{ style: { width: '88px' } }" class="gl-form">
            <div class="gl-form-grid">
              <a-form-item label="名称"><a-input v-model:value="form.name" /></a-form-item>
              <a-form-item label="类型"><a-select v-model:value="form.type" :options="K_TYPES.map((t) => ({ value: t, label: t }))" /></a-form-item>
            </div>
            <a-form-item label="定义"><a-textarea v-model:value="form.definition" :rows="3" placeholder="用业务语言写清楚是什么、不包括什么；问数会用它理解提问" /></a-form-item>
            <a-form-item label="计算说明"><a-textarea v-model:value="form.calc" :rows="2" placeholder="例如：门店.开业日期 ≤ 统计期起始 − 12 个月" /></a-form-item>
            <a-form-item label="同义词"><a-select v-model:value="form.synonyms" mode="tags" placeholder="输入后回车" /></a-form-item>
            <a-form-item label="适用于"><a-select v-model:value="form.applies" mode="multiple" :options="APPLY_OPTIONS" option-filter-prop="label" placeholder="选择对象类型或指标" /></a-form-item>
            <div class="gl-form-grid">
              <a-form-item label="生效期"><a-input v-model:value="form.period" /></a-form-item>
              <a-form-item label="负责人"><a-input v-model:value="form.owner" /></a-form-item>
            </div>
          </a-form>
          <template v-if="cur.periods?.length && !editing">
            <div class="gl-sec t-14 fw-600">日历区间</div>
            <a-table :columns="[{ title: '名称', dataIndex: 'name', width: 160 }, { title: '开始', dataIndex: 'start', width: 140 }, { title: '结束', dataIndex: 'end', width: 140 }, { title: '说明', dataIndex: 'note' }]"
              :data-source="cur.periods" :pagination="false" size="small" :row-key="(r: any) => r.name + r.start" />
          </template>
        </div>
        <div class="gl-right">
          <div class="gl-sec t-14 fw-600 gl-sec-row"><span>适用对象与指标</span><span class="t-12 c-3 fw-400">{{ view.applies.length }} 项</span></div>
          <div v-if="!appliesView.length" class="t-14 c-3">还没有关联对象或指标</div>
          <div v-for="a in appliesView" :key="a.kind + a.id" class="gl-apply">
            <span class="t-12 c-3 gl-apply-kind">{{ a.kind === 'metric' ? '指标' : '对象类型' }}</span>
            <span class="t-14" :class="a.mark === '删除' ? 'c-3 gl-del' : ''">
              <ObjRef v-if="canResolve(a) && a.mark !== '删除'" :kind="a.kind" :id="a.id">{{ a.name }}</ObjRef><template v-else>{{ a.name }}</template>
            </span>
            <span class="t-12 c-3 num">{{ a.id }}</span>
            <span class="gl-sp" />
            <a-tag v-if="a.mark" class="gl-diff">{{ a.mark }}</a-tag>
          </div>
          <div class="gl-sec t-14 fw-600">问数如何使用</div>
          <div class="t-14 c-2">识别“{{ words.slice(0, 4).join('”“') }}”后映射为 {{ view.maps || '--' }}{{ view.filters.length ? `，并默认加 ${view.filters.length} 个过滤条件` : '' }}。</div>
          <div class="t-12 c-3">业务知识只随本体整版发布生效；问数始终使用已发布版本 {{ app.ontologyVersion }}。</div>
        </div>
      </div>

      <!-- 问数中的作用 -->
      <div v-else-if="tab === 'qa'" class="gl-body gl-two">
        <div class="gl-left">
          <div class="gl-sec t-14 fw-600">术语识别</div>
          <a-descriptions :column="1" bordered size="small" class="gl-desc">
            <a-descriptions-item label="识别词">
              <a-tag v-for="w in words" :key="w" :color="isConflict(w) ? 'warning' : undefined">{{ w }}</a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="映射为">{{ view.maps || '--' }}</a-descriptions-item>
            <a-descriptions-item label="计算说明"><span class="num gl-ell" :title="view.calc">{{ view.calc || '--' }}</span></a-descriptions-item>
          </a-descriptions>
          <div class="gl-sec t-14 fw-600 gl-sec-row"><span>默认过滤</span><span class="t-12 c-3 fw-400">问题里没写时自动加上，回答里注明</span></div>
          <a-table v-if="view.filters.length" :columns="filterCols" :data-source="view.filters" :pagination="false" size="small" row-key="field" />
          <div v-else class="t-14 c-3">没有默认过滤条件</div>
          <div class="gl-sec t-14 fw-600 gl-sec-row">
            <span>同义词冲突</span>
            <span class="t-12 fw-400" :class="conflictRows.length ? 'c-warning' : 'c-3'">{{ conflictRows.length ? `${conflictRows.length} 个词同时指向其他知识（V9）` : '没有冲突' }}</span>
          </div>
          <div v-for="c in conflictRows" :key="c.word" class="gl-conflict">
            <span class="t-14 c-warning fw-600 gl-cw">“{{ c.word }}”</span>
            <span class="t-14 c-2">同时指向
              <template v-for="(o, i) in c.others" :key="o.id"><a @click="pick(o.id)">{{ o.name }}</a><span class="c-3 num">（{{ o.id }}）</span>{{ i < c.others.length - 1 ? '、' : '' }}</template>
            </span>
            <span class="gl-sp" />
            <span class="t-12" :class="c.rule ? 'c-3' : 'c-warning'">{{ c.rule ?? '未设置默认，问数会先追问用户' }}</span>
          </div>
        </div>
        <div class="gl-right gl-try">
          <div class="gl-sec t-14 fw-600 gl-sec-row"><span>试一试</span><span class="t-12 c-3 fw-400">按{{ isDraft ? `草稿 ${draft?.id}` : `已发布版本 ${app.ontologyVersion}` }}解析，不会真的查询</span></div>
          <div class="gl-try-in">
            <a-input v-model:value="tryQ" placeholder="输入一个问题，看问数怎么理解" @press-enter="runTry()" />
            <a-button @click="runTry()">解析</a-button>
          </div>
          <div class="gl-try-ex">
            <span class="t-12 c-3">示例</span>
            <a v-for="s in cur.tries" :key="s" class="t-12" @click="runTry(s)">{{ s }}</a>
          </div>
          <div v-if="tryRows.length" class="gl-try-out">
            <div v-for="(r, i) in tryRows" :key="i" class="gl-try-row">
              <span class="t-12 c-3 gl-try-k">{{ r.k }}</span>
              <span class="t-14" :class="r.cls ?? 'c-1'">{{ r.v }}</span>
            </div>
          </div>
          <div v-else class="t-14 c-3">输入问题后点“解析”</div>
        </div>
      </div>

      <!-- 引用 -->
      <div v-else-if="tab === 'refs'" class="gl-body">
        <div class="t-14 c-2">{{ cur.refs.length ? `共 ${cur.refs.length} 项引用本条知识：${refSummary}` : '暂无指标、认证问答或规则引用本条知识' }}</div>
        <template v-if="cur.refs.length">
          <a-table :columns="refCols" :data-source="refRows" :pagination="false" size="middle" row-key="name">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'name'">
                <ObjRef v-if="record.hover" :kind="record.hover.kind" :id="record.hover.id">{{ record.name }}</ObjRef>
                <span v-else class="c-1">{{ record.name }}</span>
              </template>
            </template>
          </a-table>
          <div class="gl-ref-foot">
            <AppPagination v-model:page="rpage" variant="simple" :total="cur.refs.length" :size="REF_PAGE" unit="项" :empty="false" />
          </div>
        </template>
        <div class="t-12 c-3">修改或停用本条知识时，以上各项会出现在变更单的影响分析里；认证问答需要重新认证。</div>
      </div>

      <!-- 版本历史 -->
      <div v-else class="gl-body">
        <a-table :columns="histCols" :data-source="histRows" :pagination="false" size="middle" :row-key="(r: any) => r.ver + (r.cr ?? '') + r.at">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'cr'">
              <a v-if="record.cr" class="num" @click="crLink(record.cr)">{{ record.cr }}</a>
              <span v-else class="c-3">启用变更单前</span>
            </template>
            <template v-else-if="column.key === 'status'">
              <span :class="record.status === '生效中' ? 'c-1' : 'c-3'">{{ record.status }}</span>
            </template>
          </template>
        </a-table>
        <div class="t-12 c-3">业务知识只随本体整版发布生效；回滚会生成新的变更单，经审核后随下一次发布生效。</div>
      </div>
    </div>
  </div>
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTarget" :title-placeholder="creating ? '例如：新增术语“前置仓”' : undefined" @ok="start" />
</template>

<style scoped>
.gl-ell { display: inline-block; max-width: calc(100% - 48px); vertical-align: bottom; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.gl { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.gl-list { width: 300px; flex: none; }
.gl-filter { padding: 12px 12px 8px; display: flex; flex-direction: column; gap: 8px; flex: none; }
.gl-selects { display: flex; gap: 8px; }
.gl-sel { flex: 1; min-width: 0; }
.gl-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; }
.gl-items li { padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.gl-items li:hover { background: var(--g3); }
.gl-items li.active { background: var(--brand-bg); }
.gl-items li.active .gl-name { color: var(--brand); }
.gl-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.gl-syn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.gl-pager { flex: 1; min-height: 0; }
.gl-foot { padding: 8px 12px 8px 16px; border-top: var(--border); flex: none; display: flex; align-items: center; justify-content: space-between; }
.gl-detail { flex: 1; min-width: 0; }
.gl-head { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; flex: none; }
.gl-head :deep(.ant-tag) { margin-inline-end: 0; }
.gl-sp { flex: 1; }
.gl-tabs { padding: 0 16px; flex: none; }
.gl-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.gl-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
.gl-two { flex-direction: row; gap: 16px; }
.gl-left { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.gl-right { width: 520px; flex: none; display: flex; flex-direction: column; gap: 8px; padding-left: 16px; border-left: var(--border); }
.gl-sec { margin-top: 4px; }
.gl-sec-row { display: flex; justify-content: space-between; align-items: baseline; }
.gl-desc :deep(.ant-descriptions-item-label) { width: 104px; white-space: nowrap; }
.gl-desc :deep(table) { table-layout: fixed; }
.gl-diff { margin-inline: 8px 0; }
.gl-del { text-decoration: line-through; }
.gl-form-grid { display: grid; grid-template-columns: 1fr 1fr; column-gap: 16px; }
.gl-form :deep(.ant-form-item) { margin-bottom: 12px; }
.gl-apply { display: flex; align-items: center; gap: 12px; padding: 6px 0; border-bottom: var(--border); }
.gl-apply-kind { width: 56px; flex: none; }
.gl-conflict { display: flex; align-items: center; gap: 12px; padding: 8px 12px; border: var(--border); border-radius: var(--radius); }
.gl-cw { flex: none; }
.gl-try-in { display: flex; gap: 8px; }
.gl-try-ex { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center; }
.gl-try-out { display: flex; flex-direction: column; border: var(--border); border-radius: var(--radius); }
.gl-try-row { display: flex; gap: 12px; padding: 8px 12px; align-items: baseline; }
.gl-try-row + .gl-try-row { border-top: var(--border); }
.gl-try-k { width: 72px; flex: none; }
.gl-ref-foot { display: flex; justify-content: flex-end; }

.gl-ell { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gl-ft { flex-shrink: 0; }
</style>
