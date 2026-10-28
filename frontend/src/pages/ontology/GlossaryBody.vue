<script setup lang="ts">
/**
 * 业务知识：左侧知识列表，右侧详情（内容 / 问数中的作用 / 引用 / 版本历史）。
 * 收录术语与同义词、业务规则与计算说明、财务日历与大促期、挂在对象和指标上的口径说明。
 * URL：?k= 选中条目，?tab= 页签，?q= / ?type= / ?st= 列表筛选，?view=draft 草稿视图，?ver= 历史版本只读。
 * 草稿逻辑用公共的 useDraftView('knowledge')：已发布只读，唯一入口“发起变更”；新建知识、停用都记入变更单，随整版发布生效。
 */
import { computed, nextTick, reactive, ref, shallowRef, watch, onBeforeUnmount } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import ObjRef from '@/components/ObjRef.vue';
import AppPagination from '@/components/AppPagination.vue';
import { useReadonly } from '@/composables/useReadonly';
import { useAssistantDraft } from '@/composables/useAssistantDraft';
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
import { useStaticCr, useRealCrRecords, activeTargetCr, draftFields, draftRows } from './parts/useStaticCr';
import { useStaticPage } from './parts/useStaticPage';
import { fetchKnowledgeChoices, fetchKnowledgeConflicts, fetchStaticDetail, fetchStaticPage, resolveKnowledge, staticContextKey, isStaticReal, startAllocatedTarget, fetchCurrentAfterWrite, applyModelChange } from '@/api/static';

const route = useRoute();
const router = useRouter();
const app = useAppStore();
const confirm = useConfirmStore();
const { readonly, reason } = useReadonly();
const pad = (n: number) => String(n).padStart(2, '0');
const nowText = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };

const baseline = shallowRef<KEntry[]>(isStaticReal() ? [] : KNOWLEDGE);
const entries = reactive<KEntry[]>(baseline.value.map((e) => ({ ...e })));
const firstId = ref('');
const selectedId = computed(() => typeof route.query.k === 'string' ? route.query.k : firstId.value);
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
type Shown = '已发布' | '草稿中' | '审核中' | '已停用' | '已通过待发布' | '已驳回';
const workflow = useRealCrRecords();
const shownStatus = (e: KEntry): Shown => {
  const s = statusOf(e);
  if (readonly.value && app.viewVersion) return s === '已停用' ? '已停用' : '已发布';
  if (s === '已停用') return '已停用';
  if (isStaticReal()) {
    const record = activeTargetCr(workflow.rows.value, 'knowledge', e.id);
    if (record) return record.status === '草稿' ? '草稿中' : record.status === '已通过' ? '已通过待发布' : record.status as Shown;
    return draftOf('knowledge', e.id) || s === '草稿' ? '草稿中' : '已发布';
  }
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
const knowledgePage = useStaticPage<KEntry>(() => '/knowledge', () => ({ q: kw.value, type: ktype.value, st: st.value }), lpage, LPAGE);
watch(knowledgePage.items, (rows) => { if (isStaticReal() && !firstId.value && rows.length) firstId.value = rows[0].id; });
const list = computed(() => isStaticReal() ? knowledgePage.items.value : filtered.value.slice((lpage.value - 1) * LPAGE, lpage.value * LPAGE));
const listTotal = computed(() => isStaticReal() ? knowledgePage.total.value : filtered.value.length);

const cur = computed(() => isStaticReal() ? entries.find((e) => e.id === selectedId.value) : visible.value.find((e) => e.id === route.query.k) ?? visible.value[0]);
watch(() => cur.value?.id, (id) => {
  if (isStaticReal()) return;
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
const targetId = () => isStaticReal() ? selectedId.value : cur.value?.id ?? '';
const dv = useDraftView('knowledge', targetId);
const { draft, setView } = dv;
// real 模式先写库（发起 / 加入、保存到草稿、停用、提交、放弃），见 parts/useStaticCr.ts
const cr = useStaticCr('knowledge', () => ({ id: targetId(), name: cur.value?.name, isNew: cur.value?.status === '草稿' }), dv, workflow.reload);
/** 只存在于草稿里的条目（新增的），直接看草稿 */
const draftOnly = computed(() => cur.value?.status === '草稿');
const isDraft = computed(() => dv.isDraft.value || (!readonly.value && !!draft.value && draftOnly.value));
const canEdit = computed(() => isStaticReal() ? !readonly.value && cur.value?.status === '已发布' && !draft.value : isDraft.value);
const editTip = computed(() => dv.editTip.value);
const changeOpen = ref(false);
const changeTarget = ref('');
const creating = ref(false);
const pendingNewCr = ref<string>();
const openChange = (name: string, isNew = false) => { if (readonly.value) return; if (isStaticReal() && isNew) { void openNew(); return; } creating.value = isNew; changeTarget.value = name; changeOpen.value = true; };
async function start(opts: { join?: string; title?: string; desc?: string }) {
  if (readonly.value) return;
  if (!creating.value) { await cr.start(opts); return; }
  let nid: string, crId: string | undefined, at: string, by: string;
  if (isStaticReal()) {
    const key = staticContextKey();
    try {
      const result = await startAllocatedTarget('knowledge', opts, '未命名知识', pendingNewCr.value);
      if (key !== staticContextKey()) return;
      nid = result.target.id; crId = result.crId; at = result.updatedAt; by = result.owner;
      dv.start({ join: crId, target: nid }); pendingNewCr.value = undefined; await workflow.reload(); if (key !== staticContextKey()) return;
    } catch (e) { if (key !== staticContextKey()) return; pendingNewCr.value = (e as { crId?: string }).crId ?? pendingNewCr.value; message.error(`${e instanceof Error ? e.message : String(e)}${pendingNewCr.value ? `（草稿 ${pendingNewCr.value} 已保留，可重试）` : ''}`); return; }
  } else {
    const n = entries.filter((e) => e.id.startsWith('K-N')).length + 1;
    nid = `K-N${String(n).padStart(2, '0')}`; crId = await cr.start(opts, { id: nid, isNew: true, name: '未命名知识' }); at = nowText(); by = CURRENT_USER;
  }
  if (!crId) return;
  const ne: KEntry = {
    id: nid, name: '未命名知识', type: (ktype.value as KType) || '术语', status: '草稿', cr: crId, updatedAt: at, owner: by,
    definition: '', calc: '', synonyms: [], applies: [], period: '随下一次整版发布生效', maps: '', filters: [], tries: [], refs: [],
    history: [{ ver: '—', cr: crId, status: '草稿', by, at, change: '新增（待填写）' }],
  };
  entries.unshift(ne);
  router.replace({ query: { ...route.query, k: nid, tab: 'content', view: 'draft', type: undefined, st: undefined, q: undefined } })
    .then(async () => { if (isStaticReal()) { await nextTick(); await cr.reload(); } if (canEdit.value) beginEdit(); });
}

/** 当前视图下的字段：草稿视图叠加草稿改动 */
const view = computed<KFields & { status: KStatus }>(() => {
  const e = cur.value!;
  const base = { name: e.name, type: e.type, definition: e.definition, calc: e.calc, synonyms: e.synonyms, applies: e.applies, period: e.period, owner: e.owner, maps: e.maps, filters: e.filters, status: statusOf(e) };
  return isDraft.value ? { ...base, ...(e.draft ?? {}), status: e.draftStatus ?? base.status } : base;
});
/** 草稿里改了的字段：新增条目全部算“新增” */
const mark = (k: keyof KFields | 'status'): '' | '修改' | '新增' => {
  if (!isDraft.value) return '';
  if (draftOnly.value) return '新增';
  if (k === 'status') return cur.value?.draftStatus && cur.value?.draftStatus !== cur.value.status ? '修改' : '';
  const d = cur.value?.draft;
  return d && k in d && JSON.stringify(d[k]) !== JSON.stringify(cur.value?.[k]) ? '修改' : '';
};
const before = (k: keyof KFields) => {
  const v = cur.value?.[k];
  return Array.isArray(v) ? (v as unknown[]).map((x) => (typeof x === 'string' ? x : (x as KApply).name)).join('、') : String(v ?? '');
};
const appliesView = computed(() => {
  const pub = new Set((cur.value?.applies ?? []).map((a) => a.kind + a.id));
  const now = view.value.applies.map((a) => ({ ...a, mark: isDraft.value && (draftOnly.value || !pub.has(a.kind + a.id)) ? '新增' : '' }));
  const removed = isDraft.value && !draftOnly.value ? (cur.value?.applies ?? []).filter((a) => !view.value.applies.some((b) => b.kind === a.kind && b.id === a.id)).map((a) => ({ ...a, mark: '删除' })) : [];
  return [...now, ...removed];
});

// —— 内容：草稿里可编辑 ——
interface Form { name: string; type: KType; definition: string; calc: string; synonyms: string[]; applies: string[]; period: string; owner: string }
const editing = ref(false);
const form = reactive<Form>({ name: '', type: '术语', definition: '', calc: '', synonyms: [], applies: [], period: '', owner: '' });
const snap = ref(''), baseVersion = ref<number>(), newOpen = ref(false), saving = ref(false);
const realCandidates = ref<KApply[]>([]), candidateLoading = ref(false), candidateError = ref('');
const realConflicts = ref<{ word: string; ids: string[] }[]>([]);
const realConflictRules = ref<Record<string, string>>({}), ruleError = ref('');
let candidateSeq = 0, ruleSeq = 0;
async function loadCandidates() {
  const my = ++candidateSeq, key = staticContextKey();
  realCandidates.value = []; candidateError.value = ''; candidateLoading.value = false;
  if (!isStaticReal()) return;
  candidateLoading.value = true;
  try { const rows = await fetchKnowledgeChoices(); if (my === candidateSeq && key === staticContextKey() && rows) realCandidates.value = rows; }
  catch (e) { if (my === candidateSeq && key === staticContextKey()) candidateError.value = e instanceof Error ? e.message : String(e); }
  finally { if (my === candidateSeq && key === staticContextKey()) candidateLoading.value = false; }
}
async function loadConflictRules() {
  const my = ++ruleSeq, key = staticContextKey(); realConflictRules.value = {}; realConflicts.value = []; ruleError.value = '';
  if (!isStaticReal()) return;
  try { const data = await fetchKnowledgeConflicts(isDraft.value ? draft.value?.id : undefined); if (my === ruleSeq && key === staticContextKey()) { realConflictRules.value = data.rule; realConflicts.value = data.conflicts; } }
  catch (e) { if (my === ruleSeq && key === staticContextKey()) ruleError.value = e instanceof Error ? e.message : String(e); }
}
watch([staticContextKey, editing], () => { if (editing.value) void loadCandidates(); });
watch(() => JSON.stringify([staticContextKey(), isDraft.value, draft.value?.id, cr.head.value?.lockVersion]), loadConflictRules, { immediate: true });
const APPLY_ALL = computed<KApply[]>(() => isStaticReal() ? realCandidates.value : [
  ...Q_OBJECT_TYPES.map((t) => ({ kind: 'object-type' as const, id: t.key, name: t.name })),
  ...METRICS.map((m) => ({ kind: 'metric' as const, id: m.id, name: m.name })),
]);
const unavailableApplies = computed(() => isStaticReal() && cur.value && !newOpen.value ? view.value.applies.filter((a) => !realCandidates.value.some((x) => x.kind === a.kind && x.id === a.id) && form.applies.includes(`${a.kind}:${a.id}`)) : []);
const APPLY_OPTIONS = computed(() => isStaticReal() ? [
  ...(['object-type', 'metric'] as const).map((kind) => ({ label: kind === 'metric' ? '指标' : '对象类型', options: realCandidates.value.filter((a) => a.kind === kind).map((a) => ({ value: `${a.kind}:${a.id}`, label: a.name, disabled: false })) })),
  { label: '已失效或不可见的已存引用（仅可移除）', options: unavailableApplies.value.map((a) => ({ value: `${a.kind}:${a.id}`, label: `${a.name || a.id}（不可选）`, disabled: true })) },
] : [
  { label: '对象类型', options: Q_OBJECT_TYPES.map((t) => ({ value: `object-type:${t.key}`, label: t.name, disabled: false })) },
  { label: '指标', options: METRICS.map((m) => ({ value: `metric:${m.id}`, label: m.name, disabled: false })) },
]);
async function beginEdit() {
  const entry = cur.value; if (!entry) return;
  let v: KFields = view.value;
  if (isStaticReal()) { const context = staticContextKey(), id = entry.id; try { const current = await fetchCurrentAfterWrite(); const fresh = await fetchStaticDetail<KEntry>(`/knowledge/${encodeURIComponent(id)}`); const after = await fetchCurrentAfterWrite(); if (context !== staticContextKey() || id !== cur.value?.id) return; if (current.lockVersion !== after.lockVersion) throw new Error('定义已更新，请重新打开编辑'); baseVersion.value = current.lockVersion; v = fresh; baseline.value = [...baseline.value.filter(item => item.id !== id), { ...fresh, refs: [] }]; } catch (e) { message.error(e instanceof Error ? e.message : String(e)); return; } }
  Object.assign(form, { name: v.name, type: v.type, definition: v.definition, calc: v.calc, synonyms: [...v.synonyms], applies: v.applies.map((a) => `${a.kind}:${a.id}`), period: v.period, owner: v.owner });
  snap.value = JSON.stringify(form);
  editing.value = true;
}
async function openNew(expected?: string | MouseEvent) {
  const context = staticContextKey(), page = route.fullPath;
  try {
    const current = await fetchCurrentAfterWrite(); if (context !== staticContextKey() || page !== route.fullPath || (typeof expected === 'string' && (expected !== JSON.stringify(form) || newOpen.value || editing.value || saving.value))) return;
    baseVersion.value = current.lockVersion;
    Object.assign(form, { name: '', type: '术语', definition: '', calc: '', synonyms: [], applies: [], period: '', owner: '' });
    newOpen.value = true; const initial = JSON.stringify(form); await loadCandidates(); return initial;
  } catch (e) { message.error(e instanceof Error ? e.message : String(e)); }
}
useAssistantDraft(['knowledge'], () => readonly.value || newOpen.value || editing.value || saving.value, async (draft) => {
  const context = staticContextKey(), page = route.fullPath;
  const initial = await openNew(JSON.stringify(form));
  if (context !== staticContextKey() || page !== route.fullPath || !newOpen.value || editing.value || saving.value || initial !== JSON.stringify(form)) return;
  for (const key of ['name', 'type', 'definition', 'calc', 'period', 'owner'] as const) {
    if (typeof draft[key] === 'string') (form as unknown as Record<string, unknown>)[key] = key === 'type' && !K_TYPES.includes(draft[key] as typeof K_TYPES[number]) ? '' : draft[key];
  }
  if (Array.isArray(draft.synonyms)) form.synonyms = draft.synonyms.filter((s): s is string => typeof s === 'string');
  message.info('AI 建议已填入表单，请核对后保存');
});
async function saveNew() {
  if (saving.value || baseVersion.value == null || candidateLoading.value || candidateError.value) return;
  if (!form.name.trim()) { message.error('请填写知识名称'); return; }
  if (!K_TYPES.includes(form.type as typeof K_TYPES[number])) { message.error('请选择有效的知识类型'); return; }
  const applies = form.applies.map(k => realCandidates.value.find(a => `${a.kind}:${a.id}` === k));
  if (applies.some(a => !a)) { message.error('适用范围无效，请重新选择'); return; }
  const context = staticContextKey(); saving.value = true;
  try {
    const values = { ...form, applies };
    const rows = (Object.keys(FIELD_LABEL) as (keyof Form)[]).map(k => ({ key: k, op: 'add' as const, after: values[k] }));
    const result = await applyModelChange({ kind: 'knowledge', name: form.name, isNew: true }, [{ section: 'content', rows }], baseVersion.value);
    if (context !== staticContextKey()) return;
    newOpen.value = false; await knowledgePage.reload(); await setQ({ k: result.targetId, tab: 'content', view: undefined });
    message.success('业务知识已保存并生效');
  } catch (e) { if (context === staticContextKey()) message.error(e instanceof Error ? e.message : String(e)); }
  finally { saving.value = false; }
}
const dirty = computed(() => editing.value && JSON.stringify(form) !== snap.value);
const guard = useUnsavedGuard(dirty);
/** 内容字段 → 改动列表「字段」列 */
const FIELD_LABEL: Record<keyof Form, string> = { name: '名称', type: '类型', definition: '定义', calc: '计算方式', synonyms: '同义词', applies: '适用范围', period: '生效时间', owner: '负责人' };
async function save() {
  const e = cur.value;
  if (!e) return;
  if (isStaticReal() && (candidateLoading.value || candidateError.value)) { message.error('请先成功加载真实适用对象和指标目录'); return; }
  const pool = [...APPLY_ALL.value, ...view.value.applies];
  const applies = form.applies.map((k) => pool.find((a) => `${a.kind}:${a.id}` === k));
  if (applies.some((a) => !a)) { message.error('存在未识别的引用，输入已保留，请重新选择'); return; }
  const next: Partial<KFields> = { name: form.name, type: form.type, definition: form.definition, calc: form.calc, synonyms: [...form.synonyms], applies: applies as KApply[], period: form.period, owner: form.owner };
  // real：content 段，一个字段一行（新条目全部 add；已发布条目对比已发布值，改回原值的行去掉）
  const changes = (Object.keys(FIELD_LABEL) as (keyof Form)[])
    .filter((k) => draftOnly.value || JSON.stringify(next[k]) !== JSON.stringify(e[k]) || (e.draft && k in e.draft))
    .map((k) => ({ key: k, before: draftOnly.value ? undefined : e[k], after: next[k], type: '业务知识', field: FIELD_LABEL[k] }));
  if (isStaticReal()) {
    if (saving.value || baseVersion.value == null) return;
    const context = staticContextKey(); saving.value = true;
    try {
      await applyModelChange({ kind: 'knowledge', id: e.id, name: e.name }, [{ section: 'content', rows: changes.map(c => ({ ...c, op: 'update' as const })) }], baseVersion.value);
      if (context !== staticContextKey()) return;
      const data = await fetchStaticDetail<KEntry>(`/knowledge/${encodeURIComponent(e.id)}`);
      baseline.value = [...baseline.value.filter(item => item.id !== e.id), { ...data, refs: [] }];
      await knowledgePage.reload();
    } catch (error) { if (context === staticContextKey()) message.error(error instanceof Error ? error.message : String(error)); return; }
    finally { saving.value = false; }
  } else if (!(await cr.save('content', changes))) return;
  if (!isStaticReal()) {
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
  }
  editing.value = false;
  message.success(isStaticReal() ? '业务知识已保存并生效' : `已保存到草稿 ${draft.value?.id ?? ''}`);
}
const cancelEdit = () => guard(() => { editing.value = false; });

// —— 停用：确认卡列出影响，记入变更单 ——
const refCount = (e: KEntry, t: string) => e.refs.filter((r) => r.type === t).length;
async function stop() {
  const e = cur.value;
  if (!e) return;
  const words = [e.name, ...e.synonyms];
  const join = draft.value?.id;
  const context = staticContextKey();
  let version: number | undefined;
  let references = 0;
  if (isStaticReal()) {
    try { const [data, current] = await Promise.all([fetchStaticPage<KEntry['refs'][number]>(`/knowledge/${encodeURIComponent(e.id)}/refs`, { page: 1, size: 1 }), fetchCurrentAfterWrite()]); references = data.total; version = current.lockVersion; }
    catch (error) { if (context === staticContextKey()) message.error(error instanceof Error ? error.message : String(error)); return; }
    if (context !== staticContextKey() || e.id !== cur.value?.id) return;
  }
  const ok = await confirm.ask({
    title: '停用业务知识', target: `${e.name}（${e.id} · ${e.type}）`, tag: '待确认', danger: true, okText: isStaticReal() ? '停用' : '记入变更单',
    fields: [
      { label: '识别词', value: words.join('、') },
      { label: '引用', value: isStaticReal() ? `共 ${references} 项` : `指标 ${refCount(e, '指标')} · 认证问答 ${refCount(e, '认证问答')} · 规则 ${refCount(e, '规则')} · 看板 ${refCount(e, '看板')}` },
      { label: '默认过滤', value: e.filters.length ? e.filters.map((f) => `${f.field} ${f.cond}`).join('；') : '无' },
      { label: '生效', value: isStaticReal() ? '确认后立即生效' : '发布后生效' },
    ],
    impact: `发布后问数不再识别“${words.slice(0, 3).join('”“')}”${e.filters.length ? '，也不再自动加默认过滤' : ''}；引用它的 ${isStaticReal() ? references : e.refs.length} 项会出现在变更单影响分析里，认证问答需要重新认证。历史版本和已保存的回答不受影响。`,
    picker: isStaticReal() || join ? undefined : {
      label: '记入变更单', placeholder: '选择变更单', required: true,
      options: [{ value: '__new', label: `新建变更单：停用业务知识“${e.name}”` }, ...allDrafts().map((d) => ({ value: d.id, label: `${d.id}　${d.title}` }))],
    },
  });
  if (!ok || context !== staticContextKey() || e.id !== cur.value?.id || readonly.value) return;
  if (isStaticReal()) {
    if (version == null) return;
    try {
      await applyModelChange({ kind: 'knowledge', id: e.id, name: e.name }, [{ section: 'status', rows: [{ key: 'status', op: 'update', before: e.status, after: '已停用', type: '业务知识', field: '状态' }] }], version);
      if (context !== staticContextKey()) return;
      const data = await fetchStaticDetail<KEntry>(`/knowledge/${encodeURIComponent(e.id)}`);
      baseline.value = [...baseline.value.filter(item => item.id !== e.id), { ...data, refs: [] }]; await knowledgePage.reload();
      message.success('业务知识已停用');
    } catch (error) { if (context === staticContextKey()) message.error(error instanceof Error ? error.message : String(error)); }
    return;
  }
  const pv = confirm.pickValue;
  const id = join ?? (await cr.start(pv && pv !== '__new' ? { join: pv } : { title: `停用业务知识“${e.name}”` }));
  if (!id || context !== staticContextKey() || e.id !== cur.value?.id || readonly.value) return;
  // real：status 段一行（已发布条目 → 已停用）
  if (!(await cr.save('status', [{ key: 'status', before: e.status, after: '已停用', type: '业务知识', field: '状态' }], id))) return;
  if (!isStaticReal()) e.draftStatus = '已停用';
  if (context !== staticContextKey() || e.id !== cur.value?.id) return;
  setView('draft');
  message.success(`已记入 ${id}：${e.name} 停用（待审核）`);
}

// —— 问数中的作用 ——
const conflicts = computed(() => isStaticReal() ? new Map(realConflicts.value.map((r) => [r.word, r.ids])) : synonymConflicts(entries.filter((e) => e.status !== '已停用' || e.id === cur.value?.id)));
const words = computed(() => [view.value.name, ...view.value.synonyms]);
const isConflict = (w: string) => (conflicts.value.get(w)?.length ?? 0) > 1 && conflicts.value.get(w)!.includes(cur.value?.id ?? '');
const conflictRows = computed(() => words.value.filter(isConflict).map((w) => ({
  word: w,
  others: conflicts.value.get(w)!.filter((id) => id !== cur.value?.id).map((id) => entries.find((x) => x.id === id) ?? { id, name: id }),
  rule: isStaticReal() ? realConflictRules.value[w] : CONFLICT_RULE[w],
})));
const nameOf = (id: string) => entries.find((e) => e.id === id)?.name ?? id;
const filterCols = [{ title: '字段', dataIndex: 'field', width: 220 }, { title: '条件', dataIndex: 'cond', width: 260 }, { title: '说明', dataIndex: 'note' }];

const tryQ = ref('');
const tried = ref('');
watch(() => cur.value?.id, () => { tryQ.value = isStaticReal() ? '' : cur.value?.tries[0] ?? ''; tried.value = tryQ.value; }, { immediate: true });
const knowledgeTrialKey = computed(() => JSON.stringify([staticContextKey(), cur.value?.id, isDraft.value, draft.value?.id, cr.head.value?.lockVersion, cr.loading.value, cr.error.value]));
const trialRows = ref<TryRow[]>([]), trialBusy = ref(false), trialError = ref(''), trialLoadedKey = ref('');
let trialSeq = 0;
watch(knowledgeTrialKey, () => { ++trialSeq; trialRows.value = []; tried.value = ''; trialError.value = ''; trialBusy.value = false; });
async function runTry(s?: string) {
  if (s) tryQ.value = s;
  if (!isStaticReal()) { tried.value = tryQ.value.trim(); return; }
  const seq = ++trialSeq, key = knowledgeTrialKey.value, text = tryQ.value.trim();
  trialRows.value = []; trialError.value = ''; trialBusy.value = false; tried.value = text; trialLoadedKey.value = key;
  if (!cur.value || (isDraft.value && (!cr.head.value || cr.loading.value || cr.error.value))) { trialError.value = '请先成功加载草稿定义'; return; }
  trialBusy.value = true;
  try {
    const result = await resolveKnowledge({ text, knowledge: cur.value?.id, ...(isDraft.value ? { crId: draft.value?.id } : {}) });
    if (seq === trialSeq && key === knowledgeTrialKey.value && text === tryQ.value.trim()) trialRows.value = result.rows;
  } catch (e) { if (seq === trialSeq && key === knowledgeTrialKey.value && text === tryQ.value.trim()) trialError.value = e instanceof Error ? e.message : String(e); }
  finally { if (seq === trialSeq && key === knowledgeTrialKey.value) trialBusy.value = false; }
}
const METRIC_WORDS: [string, string][] = [['含税销售额', 'gmv_tax'], ['销售额', 'gmv'], ['GMV', 'gmv'], ['毛利率', 'gross_margin'], ['毛利', 'gross_margin'], ['客单价', 'atv'], ['出库及时率', 'ship_ontime'], ['订单数', 'order_cnt'], ['有效单', 'order_cnt']];
const TIME_WORDS: [RegExp, string][] = [
  [/上个?月/, '2026-09-01 至 2026-09-30'], [/上周/, '2026-10-12 至 2026-10-18'], [/本周/, '2026-10-19 至 2026-10-25'], [/昨天/, '2026-10-24'],
  [/近\s*3\s*个月/, '2026-07-26 至 2026-10-25'], [/本财年/, 'FY2026（2026-02-01 至今）'], [/FY2026 Q3/, '2026-08-02 至 2026-10-31（财务日历）'],
];
interface TryRow { k: string; v: string; cls?: string; by?: string }
const tryRows = computed<TryRow[]>(() => {
  if (isStaticReal()) return trialLoadedKey.value === knowledgeTrialKey.value && tried.value === tryQ.value.trim() ? trialRows.value : [];
  const s = tried.value;
  if (!s) return [];
  const rows: TryRow[] = [];
  const pool = entries.filter((e) => (e.status === '已发布' || e.id === cur.value?.id) && (statusOf(e) !== '已停用' || e.id === cur.value?.id));
  const hitIds = new Set<string>();
  const seen = new Set<string>();
  pool.forEach((e) => {
    const f = e.id === cur.value?.id ? view.value : e;
    [f.name, ...f.synonyms].filter((w) => s.includes(w) && !seen.has(w)).forEach((w) => {
      const ids = conflicts.value.get(w);
      if (ids && ids.length > 1) {
        seen.add(w);
        rows.push({ k: `“${w}”`, v: `同义词冲突：${ids.map(nameOf).join(' / ')}。${CONFLICT_RULE[w] ?? '未设置默认，问数会先追问用户'}`, cls: 'c-1' });
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
    return (id === cur.value?.id ? view.value.filters : e.filters).map((f) => `${f.field} ${f.cond}`);
  });
  rows.push(fs.length ? { k: '默认过滤', v: fs.join('；') } : { k: '默认过滤', v: '无', cls: 'c-3' });
  if (!hitIds.has(cur.value?.id ?? '')) rows.push({ k: '本条知识', v: `问题里没有出现“${words.value.slice(0, 3).join('”“')}”，未使用本条`, cls: 'c-3' });
  return rows;
});

// —— 引用 / 版本历史 ——
const REF_PAGE = 8;
const rpage = ref(1);
watch(() => cur.value?.id, () => { rpage.value = 1; });
const refsPage = useStaticPage<KEntry['refs'][number]>(() => targetId() ? `/knowledge/${encodeURIComponent(targetId())}/refs` : '', () => ({}), rpage, REF_PAGE, () => tab.value === 'refs' && !draftOnly.value);
const hpage = ref(1);
watch(selectedId, () => { hpage.value = 1; });
const historySize = ref(10);
watch(historySize, () => { hpage.value = 1; });
const historyPage = useStaticPage<KEntry['history'][number]>(() => targetId() ? `/knowledge/${encodeURIComponent(targetId())}/history` : '', () => ({}), hpage, historySize, () => tab.value === 'history' && !draftOnly.value);
const refRows = computed(() => isStaticReal() ? refsPage.items.value : (cur.value?.refs ?? []).slice((rpage.value - 1) * REF_PAGE, rpage.value * REF_PAGE));
const refTotal = computed(() => isStaticReal() ? refsPage.total.value : cur.value?.refs.length ?? 0);
const refCols = [{ title: '类型', dataIndex: 'type', width: 120 }, { title: '名称', key: 'name', width: 360 }, { title: '使用方式', dataIndex: 'note' }];
const refSummary = computed(() => {
  const m = new Map<string, number>();
  (cur.value?.refs ?? []).forEach((r) => m.set(r.type, (m.get(r.type) ?? 0) + 1));
  return [...m].map(([k, v]) => `${k} ${v}`).join(' · ');
});
const histRows = computed(() => {
  const ver = app.viewVersion;
  if (isStaticReal()) return historyPage.items.value;
  const rows = cur.value?.history ?? [];
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
const canResolve = (a: KApply) => isStaticReal() ? !!a.id : (a.kind === 'metric' ? METRICS.some((m) => m.id === a.id) : Q_OBJECT_TYPES.some((t) => t.key === a.id));

watch([baseline, cr.items, cr.head, isDraft, cr.loading], () => {
  if (!isStaticReal()) return;
  const base = structuredClone(baseline.value);
  const target = cr.head.value?.targets.find((t) => t.kind === 'knowledge' && t.id === targetId());
  if (target?.isNew && !base.some((e) => e.id === target.id)) base.push({ id: target.id, name: target.name ?? '', type: '术语', status: '草稿', cr: cr.head.value!.id, updatedAt: cr.head.value!.updatedAt, owner: cr.head.value!.owner, definition: '', calc: '', synonyms: [], applies: [], period: '', maps: '', filters: [], tries: [], refs: [], history: [] });
  const item = base.find((e) => e.id === targetId());
  if (item && isDraft.value) {
    item.draft = draftFields(cr.items.value, 'content');
    item.draftStatus = draftFields(cr.items.value, 'status').status as KStatus | undefined;
  }
  entries.splice(0, entries.length, ...base);
});

let definitionSeq = 0;
const definitionError = ref('');
watch(() => JSON.stringify([selectedId.value, staticContextKey(), route.query.view, workflow.loading.value,
  draft.value?.id, cr.loading.value, cr.error.value, cr.head.value?.targets]), async () => {
  const n = ++definitionSeq, key = staticContextKey(), id = selectedId.value;
  definitionError.value = '';
  if (!isStaticReal() || !id || (route.query.view === 'draft' && workflow.loading.value)
    || (draft.value && (cr.loading.value || !cr.head.value || cr.error.value))
    || cr.head.value?.targets.some((t) => t.kind === 'knowledge' && t.id === id && t.isNew)) return;
  try {
    const data = await fetchStaticDetail<KEntry>(`/knowledge/${encodeURIComponent(id)}`);
    if (n !== definitionSeq || key !== staticContextKey() || id !== selectedId.value) return;
    baseline.value = [...baseline.value.filter((e) => e.id !== id), { ...data, refs: [] }];
  } catch (e) { if (n === definitionSeq && key === staticContextKey() && id === selectedId.value && !cr.head.value?.targets.some((t) => t.kind === 'knowledge' && t.id === id && t.isNew)) definitionError.value = e instanceof Error ? e.message : String(e); }
}, { immediate: true });
onBeforeUnmount(() => { ++definitionSeq; ++ruleSeq; ++candidateSeq; });

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
      <a-alert v-if="knowledgePage.error.value || definitionError" type="error" :message="knowledgePage.error.value || definitionError" show-icon />
      <div v-if="knowledgePage.loading.value" style="text-align: center; padding: 8px"><a-spin size="small" /></div>
      <ul class="gl-items" :aria-busy="knowledgePage.loading.value">
        <li v-for="e in list" :key="e.id" :class="{ active: e.id === cur?.id }" :title="`${e.name} · ${e.type}${e.synonyms.length ? ` · ${e.synonyms.slice(0, 3).join('、')}` : ''} · ${e.id}`" @click="pick(e.id)">
          <div class="gl-row">
            <span class="t-14 gl-name gl-ell">{{ e.name }}</span>
            <span class="t-12 c-3 gl-ft">{{ e.type }}</span>
            <span v-if="shownStatus(e) !== '已发布'" class="t-12 num" :class="shownClass(shownStatus(e))">{{ shownStatus(e) }}{{ crOf(e) && shownStatus(e) !== '已停用' ? ` · ${crOf(e)}` : '' }}</span>
          </div>
        </li>
      </ul>
      <div class="gl-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="listTotal" :size="LPAGE" unit="条" class="gl-pager" empty-text="没有匹配的业务知识" />
      </div>
    </div>

    <div v-if="cur" class="panel gl-detail">
      <DraftBanner v-if="!isStaticReal() && isDraft && draft" :draft="draft"
        @submit="cr.submit" @discard="draftOnly ? message.info('这条知识只在草稿里，放弃请到变更单处理') : cr.discard()" />
      <div class="gl-head">
        <span class="t-16 fw-600 c-1">{{ view.name }}</span>
        <span class="t-14 c-3 num">{{ cur.id }}</span>
        <a-tag>{{ view.type }}</a-tag>
        <a-tooltip :title="crOf(cur) && shownStatus(cur) !== '已发布' ? `${crOf(cur)} ${shownStatus(cur) === '审核中' ? '审核中' : '草稿'}，问数仍使用已发布版本` : ''">
          <a-tag :color="shownStatus(cur) === '审核中' ? 'warning' : undefined">{{ shownStatus(cur) }}</a-tag>
        </a-tooltip>
        <a-tag v-if="mark('status')" class="gl-diff">{{ mark('status') }}</a-tag>
        <span class="t-12 c-3">{{ view.owner }} · 更新于 {{ cur.updatedAt }}</span>
        <a-segmented v-if="!isStaticReal() && draft && !readonly && !draftOnly" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => guard(() => { editing = false; setView(v); })" />
        <span class="gl-sp" />
        <a-tooltip v-if="readonly" :title="reason"><a-button disabled>停用</a-button></a-tooltip>
        <a-button v-else-if="canStop" @click="stop">停用</a-button>
        <a-tooltip v-if="readonly" :title="reason"><a-button disabled>发起变更</a-button></a-tooltip>
        <a-button v-else-if="!isStaticReal() && !draft" type="primary" @click="openChange(`${cur.name}（${cur.id}）`)">发起变更</a-button>
        <a-button v-else-if="!isStaticReal() && draft && !isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
      </div>
      <a-tabs v-model:activeKey="tab" class="gl-tabs">
        <a-tab-pane key="content" tab="内容" />
        <a-tab-pane key="qa" tab="问数中的作用" />
        <a-tab-pane key="refs" tab="引用" />
        <a-tab-pane key="history" tab="版本历史" />
      </a-tabs>

      <!-- 内容 -->
      <div v-if="tab === 'content'" class="gl-body gl-two">
        <div class="gl-left">
          <div class="gl-sec t-14 fw-600 gl-sec-row">
            <span>知识内容</span>
            <a-space v-if="editing">
              <a-button size="small" @click="cancelEdit">取消</a-button>
              <a-button size="small" :disabled="saving || !dirty || (isStaticReal() && (candidateLoading || !!candidateError))" @click="save">保存</a-button>
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
                <a-tag v-for="w in view.synonyms" :key="w" :title="isConflict(w) ? '与其他知识冲突，见“问数中的作用”' : undefined">{{ w }}</a-tag>
              </template>
              <span v-else class="c-3">--</span>
              <a-tag v-if="mark('synonyms')" class="gl-diff">{{ mark('synonyms') }}</a-tag>
              <span v-if="conflictRows.length" class="t-12 c-1">{{ conflictRows.length }} 个词与其他知识冲突，见“问数中的作用”</span>
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
            <a-form-item label="适用于"><a-alert v-if="candidateError" type="error" :message="candidateError" show-icon><template #action><a-button size="small" @click="loadCandidates">重试</a-button></template></a-alert><a-select v-model:value="form.applies" :loading="candidateLoading" :disabled="candidateLoading || !!candidateError" mode="multiple" :options="APPLY_OPTIONS" option-filter-prop="label" placeholder="选择对象类型或指标" /><div v-for="a in unavailableApplies" :key="`${a.kind}:${a.id}`" class="t-12 c-warning">{{ a.name || a.id }}（已失效或无权限；保留到显式移除）<a-button type="link" size="small" @click="form.applies = form.applies.filter((k) => k !== `${a.kind}:${a.id}`)">移除引用</a-button></div></a-form-item>
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
          <AppPagination v-model:page="hpage" variant="full" :total="isStaticReal() ? historyPage.total.value : histRows.length" v-model:size="historySize" :empty="false" />
        <div class="t-12 c-3">业务知识只随本体整版发布生效；问数始终使用已发布版本 {{ app.ontologyVersion }}。</div>
        </div>
      </div>

      <!-- 问数中的作用 -->
      <div v-else-if="tab === 'qa'" class="gl-body gl-two">
        <div class="gl-left">
          <div class="gl-sec t-14 fw-600">术语识别</div>
          <a-descriptions :column="1" bordered size="small" class="gl-desc">
            <a-descriptions-item label="识别词">
              <a-tag v-for="w in words" :key="w" :title="isConflict(w) ? '与其他知识冲突' : undefined">{{ w }}</a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="映射为">{{ view.maps || '--' }}</a-descriptions-item>
            <a-descriptions-item label="计算说明"><span class="num gl-ell" :title="view.calc">{{ view.calc || '--' }}</span></a-descriptions-item>
          </a-descriptions>
          <div class="gl-sec t-14 fw-600 gl-sec-row"><span>默认过滤</span><span class="t-12 c-3 fw-400">问题里没写时自动加上，回答里注明</span></div>
          <a-table v-if="view.filters.length" :columns="filterCols" :data-source="view.filters" :pagination="false" size="small" row-key="field" />
          <div v-else class="t-14 c-3">没有默认过滤条件</div>
          <div class="gl-sec t-14 fw-600 gl-sec-row">
            <span>同义词冲突</span>
            <span class="t-12 fw-400" :class="conflictRows.length ? 'c-1' : 'c-3'">{{ conflictRows.length ? `${conflictRows.length} 个词同时指向其他知识（V9）` : '没有冲突' }}</span>
          </div>
          <a-alert v-if="ruleError" type="warning" :message="`冲突配置未提供：${ruleError}`" show-icon />
          <div v-for="c in conflictRows" :key="c.word" class="gl-conflict">
            <span class="t-14 c-1 fw-600 gl-cw">“{{ c.word }}”</span>
            <span class="t-14 c-2">同时指向
              <template v-for="(o, i) in c.others" :key="o.id"><a @click="pick(o.id)">{{ o.name }}</a><span class="c-3 num">（{{ o.id }}）</span>{{ i < c.others.length - 1 ? '、' : '' }}</template>
            </span>
            <span class="gl-sp" />
            <span class="t-12" :class="c.rule ? 'c-3' : 'c-1'">{{ c.rule ?? (isStaticReal() ? '未提供冲突处理配置' : '未设置默认，问数会先追问用户') }}</span>
          </div>
        </div>
        <div class="gl-right gl-try">
          <div class="gl-sec t-14 fw-600 gl-sec-row"><span>试一试</span><span v-if="!isStaticReal()" class="t-12 c-3 fw-400">按{{ isDraft ? `草稿 ${draft?.id}` : `已发布版本 ${app.ontologyVersion}` }}解析，不会真的查询</span></div>
          <a-alert v-if="isStaticReal() && trialError" type="error" :message="trialError" />
          <span v-if="isStaticReal()" class="t-12 c-3">按已保存定义预览词义与时间，不执行数据查询。</span>
          <div class="gl-try-in">
            <a-input v-model:value="tryQ" placeholder="输入一个问题，看问数怎么理解" @press-enter="runTry()" />
            <a-button :loading="trialBusy" @click="runTry()">解析</a-button>
          </div>
          <div v-if="!isStaticReal()" class="gl-try-ex">
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
        <a-alert v-if="refsPage.error.value" type="error" :message="refsPage.error.value" show-icon />
        <div class="t-14 c-2">{{ isStaticReal() ? (refsPage.loading.value ? '加载中…' : refTotal ? `共 ${refTotal} 项引用本条知识` : '暂无引用') : cur.refs.length ? `共 ${cur.refs.length} 项引用本条知识：${refSummary}` : '暂无指标、认证问答或规则引用本条知识' }}</div>
        <template v-if="refTotal">
          <a-table :columns="refCols" :data-source="refRows" :loading="refsPage.loading.value" :pagination="false" size="middle" row-key="name">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'name'">
                <ObjRef v-if="record.hover" :kind="record.hover.kind" :id="record.hover.id">{{ record.name }}</ObjRef>
                <span v-else class="c-1">{{ record.name }}</span>
              </template>
            </template>
          </a-table>
          <div class="gl-ref-foot">
            <AppPagination v-model:page="rpage" variant="simple" :total="refTotal" :size="REF_PAGE" unit="项" :empty="false" />
          </div>
        </template>
        <div class="t-12 c-3">修改或停用本条知识时，以上各项会出现在变更单的影响分析里；认证问答需要重新认证。</div>
      </div>

      <!-- 版本历史 -->
      <div v-else class="gl-body">
        <a-alert v-if="historyPage.error.value" type="error" :message="historyPage.error.value" show-icon />
        <a-table :columns="histCols" :data-source="isStaticReal() ? histRows : histRows.slice((hpage - 1) * historySize, hpage * historySize)" :loading="historyPage.loading.value" :pagination="false" size="middle" :row-key="(r: any) => r.ver + (r.cr ?? '') + r.at">
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
        <div class="t-12 c-3">业务知识只随本体整版发布生效；回滚会生成新的变更单，随下一次发布生效。</div>
      </div>
    </div>
  </div>
  <a-modal v-model:open="newOpen" title="新建业务知识" ok-text="保存" :confirm-loading="saving" :mask-closable="!saving" @ok="saveNew">
    <a-form layout="vertical">
      <a-form-item label="名称" required><a-input v-model:value="form.name" /></a-form-item><a-form-item label="类型"><a-select v-model:value="form.type" :options="K_TYPES.map(t => ({ value: t, label: t }))" /></a-form-item>
      <a-form-item label="定义"><a-textarea v-model:value="form.definition" /></a-form-item><a-form-item label="计算说明"><a-textarea v-model:value="form.calc" /></a-form-item><a-form-item label="同义词"><a-select v-model:value="form.synonyms" mode="tags" /></a-form-item>
      <a-form-item label="适用于"><a-select v-model:value="form.applies" mode="multiple" :options="APPLY_OPTIONS" :loading="candidateLoading" :disabled="candidateLoading || !!candidateError" /></a-form-item><a-alert v-if="candidateError" type="error" :message="candidateError" show-icon />
      <a-form-item label="生效期"><a-input v-model:value="form.period" /></a-form-item><a-form-item label="负责人"><a-input v-model:value="form.owner" /></a-form-item>
    </a-form>
  </a-modal>
  <StartChangeModal v-model:open="changeOpen" :target-name="pendingNewCr && creating ? `${changeTarget}（重试保留草稿 ${pendingNewCr}）` : changeTarget" :title-placeholder="creating ? '例如：新增术语“前置仓”' : undefined" @ok="start" />
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
