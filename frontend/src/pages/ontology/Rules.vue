<script setup lang="ts">
import InfoTip from '@/components/InfoTip.vue';
/**
 * 规则与自动化：左侧规则列表，右侧详情。页签：条件 / 触发方式 / 动作 / 命中历史 / 回放试跑 / 版本历史。
 * URL：?rule= 选中项，?tab= 页签，?view=draft 草稿，?ver= 历史版本只读；
 *      命中历史 ?hrange= + ?hpage/hsize，命中详情抽屉 ?hit=；回放试跑 ?rrange= ?rver= ?run=1 + ?rpage/rsize。
 * 已发布版本只读，改规则只能“发起变更”进草稿。自动暂存动作只会把动作放进动作中心“待确认”，从不自动执行。
 * 开关 dynamic 走真接口时（src/api/dynamic.ts）：列表 / 筛选 / 详情与草稿 / 确认人与取值来源 / 命中历史与命中抽屉 /
 * 回放试跑 / 版本历史 / 已暂存的动作都取后台，“保存到草稿”调 PUT /rules/{id}/draft；否则保持原 mock 逻辑。
 * 变更单（useDraftView / CHANGE_REQUESTS）、动作类型 / 事件类型 / 指标目录仍由各自模块提供。
 */
import { computed, reactive, ref, watch, onScopeDispose } from 'vue';
import { fallBackIfDown } from '@/api/fallback';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import ObjRef from '@/components/ObjRef.vue';
import HoverCard from '@/components/HoverCard.vue';
import StatusTimeline from '@/components/StatusTimeline.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { useReadonly } from '@/composables/useReadonly';
import { paginate } from '@/mock/paginate';
import { fmtInt } from '@/mock/format';
import { alertTimeline } from '@/mock/dynamic';
import { metricById as mockMetricById } from '@/mock/metrics';
import { METRIC_LIST } from '@/mock/metricCatalog';
import { ACTIONS } from '@/mock/actions';
import { actionApi, actionTypes, eventTypes, paramsOf, loadActionParams, loadSnapshot, actionContextKey } from '@/api/action';
import { isStaticReal, loadStatic, getObjectTypeMeta, OBJECT_TYPE_LIST } from '@/api/static';
import {
  RULE_DEFS, RULE_DRAFTS, CONFIRMERS, MAPPING_SOURCES, hitsOf, stagedOf, replay, ruleVersions, objKindLabel, dedupeReason,
  type RuleDef, type AutoStage, type Hit,
} from '@/mock/rules';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import { useDraftView, draftOf, forgetTarget } from './parts/useDraftView';
import { useConfirmStore } from '@/stores/confirm';
import { CHANGE_REQUESTS } from '@/mock/changes';
import type { ActionRecord } from '@/mock/actions';
import type { Alert } from '@/mock/dynamic';
import { isApiError } from '@/api/http';
import {
  dynamicApi, dynamicContextKey, listRules, getRule, getRulesMeta, saveRuleDraft, createRule, openRuleDraft, discardRuleDraft, submitCR, removeCRTarget, CR_WRITE, crOpen, crDropEmpty, crErrText, listRuleHits, getRuleHit, replayRule, listRuleVersions, listRuleActionRuns,
  type RuleDto, type RuleHitDetailDto, type ReplayDto, type RuleVersionDto, type HitDim, type ReplaySampleDto,
} from '@/api/dynamic';

/** 是否走真接口（开关 dynamic）；构建时确定 */
const REAL_API = dynamicApi.isReal();
/* 目录（动作类型 / 事件类型 / 指标）：action、static 开关各自切到真接口时读它们的接口（只读，src/api/action.ts、static.ts），否则读 mock */
const catTick = ref(0);
if (actionApi.isReal()) watch(actionContextKey, () => { const key = actionContextKey(); loadSnapshot().then(() => { if (key === actionContextKey()) catTick.value += 1; }, (e) => { if (key === actionContextKey()) console.error('[api] 动作 / 事件类型目录加载失败', e); }); }, { immediate: true });
if (isStaticReal()) watch(dynamicContextKey, () => { const key = dynamicContextKey(); loadStatic(['metrics', 'objects']).then(() => { if (key === dynamicContextKey()) catTick.value += 1; }, (e) => { if (key === dynamicContextKey() && !fallBackIfDown(e)) console.error('[api] 指标目录加载失败', e); }); }, { immediate: true });
const actionTypeById = (id: string) => { void catTick.value; return actionTypes().find((a) => a.id === id); };
const eventTypeById = (id: string) => { void catTick.value; return eventTypes().find((e) => e.id === id); };
const metricById = (id: string): { name: string } | undefined => {
  void catTick.value;
  return isStaticReal() ? METRIC_LIST.find((m) => m.id === id) : mockMetricById(id);
};
/** 真接口失败保留明确错误，不切换为演示规则。 */
const live = computed(() => REAL_API);
const readFail = (e: unknown) => fail(e);
const fail = (e: unknown) => { message.error(isApiError(e) ? crErrText(e) : String(e)); };
/** 真接口状态：列表页、详情（含 draft）、下拉、命中、命中抽屉、回放结果、版本、已暂存动作 */
const remote = reactive({
  items: [] as RuleDto[], total: 0, detail: null as RuleDto | null,
  meta: { confirmers: [] as string[], mappingSources: [] as string[], statuses: [] as string[] },
  hits: { items: [] as Hit[], total: 0 }, hits7: 0, recent: [] as Hit[], hit: null as RuleHitDetailDto | null,
  replay: null as (ReplayDto & { k: string }) | null, versions: [] as RuleVersionDto[], staged: [] as ActionRecord[],
});
const stripDraft = (d: RuleDto): RuleDef => { const { draft: _d, displayStatus: _s, cr: _c, ...r } = d; return r as RuleDef; };
const EMPTY_RULE: RuleDef = { id: '', name: '', version: 0, status: '已发布', owner: '', desc: '', objKind: '' as RuleDef['objKind'], level: '中', output: '告警', conds: [], logic: '且', expr: '', dedupe: '', cooldown: '', trigger: { kind: '定时', text: '' }, autoStage: { enabled: false, actionTypeId: '', mapping: [], confirmer: '' }, weekly: 0, core: false };
let mounted = true;
onScopeDispose(() => { mounted = false; });

/** 新建 / 修改草稿时记当前时间 */
const nowText = () => {
  const d = new Date(); const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

const route = useRoute();
const router = useRouter();
const { readonly, reason } = useReadonly();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQ = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });

/** 规则状态取自所在变更单（同 ObjBehaviorAutomation）：草稿中 = 灰，审核中 = 橙 */
function ruleStatus(r: RuleDef): { text: string; badge: 'warning' | 'default'; cr?: string } {
  if (live.value) {
    const x = (r.id === remote.detail?.id ? remote.detail : remote.items.find((i) => i.id === r.id)) as RuleDto | undefined;
    if (x) return { text: x.displayStatus, badge: x.displayStatus === '审核中' ? 'warning' : 'default', cr: x.cr ?? undefined };
  }
  const d = draftOf('rule', r.id);
  if (!d) return { text: r.status === '变更中' ? '草稿中' : r.status, badge: 'default' };
  const st = CHANGE_REQUESTS.find((c) => c.id === d.id)?.status;
  return st === '审核中' ? { text: '审核中', badge: 'warning', cr: d.id } : { text: '草稿中', badge: 'default', cr: d.id };
}

/* 左侧列表 */
const kw = ref('');
const trig = ref('全部');
const stf = ref('全部');
const STATUS_OPTS = computed(() => (live.value ? remote.meta.statuses : ['全部', ...new Set(RULE_DEFS.map((r) => ruleStatus(r).text))]).map((v) => ({ value: v, label: v === '全部' ? '全部状态' : v })));
const filtered = computed(() => RULE_DEFS.filter((r) => (trig.value === '全部' || r.trigger.kind === trig.value)
  && (stf.value === '全部' || ruleStatus(r).text === stf.value)
  && (!kw.value || r.name.includes(kw.value) || r.id.toLowerCase().includes(kw.value.toLowerCase()))));
const LPAGE = REAL_API ? 10 : 16;
const lpage = ref(1);
watch([kw, trig, stf], () => { lpage.value = 1; });
const list = computed<RuleDef[]>(() => (live.value ? remote.items : paginate(filtered.value, { page: lpage.value, size: LPAGE }).items));
const listTotal = computed(() => (live.value ? remote.total : filtered.value.length));
/** 真接口：选中项 = ?rule= 或列表第一条；详情回来前先用列表行（同一份定义）占位 */
/** 列表为空（筛选无结果）时为空串：不请求详情 / 命中 */
const defaultRuleId = ref('');
const realId = computed(() => (route.query.rule as string) || defaultRuleId.value);
const base = computed<RuleDef>(() => {
  if (!live.value) return RULE_DEFS.find((r) => r.id === route.query.rule) ?? RULE_DEFS[0];
  if (remote.detail && remote.detail.id === realId.value) return stripDraft(remote.detail);
  return remote.items.find((r) => r.id === realId.value) ?? EMPTY_RULE;
});
// 切换规则时清掉与上一条规则相关的状态，包括抽屉（drawer / drawer2 / hit）
const RESET = { view: undefined, hpage: undefined, hsize: undefined, hrange: undefined, hit: undefined, rpage: undefined, rsize: undefined,
  rrange: undefined, rver: undefined, rmode: undefined, run: undefined, drawer: undefined, drawer2: undefined };
const pick = (id: string) => router.replace({ query: { ...route.query, ...RESET, rule: id } });

const TABS = ['cond', 'trigger', 'action', 'hits', 'replay', 'versions'];
const tab = computed({
  get: () => (TABS.includes(q('tab')) ? q('tab') : 'cond'),
  set: (v) => router.replace({ query: { ...route.query, ...RESET, view: route.query.view, tab: v === 'cond' ? undefined : v } }),
});

/* 草稿：公共 useDraftView('rule')，变更单信息取自变更单本身（R-007 在 CR-0011）；本地只保留规则草稿的改动内容 */
const { draft: draftCR, isDraft, editTip, setView, start, cancel } = useDraftView('rule', () => base.value.id);
const confirm = useConfirmStore();
const changeOpen = ref(false);
const changeTarget = ref('');
const openChange = (name: string) => { changeTarget.value = name; changeOpen.value = true; };
const NEW_RULE = '新建规则';
/**
 * 变更弹窗确认。mock：沿用公共 start()。真接口：
 * - CR_WRITE（变更单写接口）：单号以库为准。
 *   新建规则：新建空单 / 选已有单 → POST /rules 带 cr（后台同一事务开草稿 + 登记 isNew 目标和配置行，提交不会 CR_EMPTY）→ start({ join, target })；
 *   发起变更：POST /change-requests 带目标 / POST …/targets 加入（占用他单 409 → “已在 CR-xxxx 中”）→ start() → POST /rules/{id}/draft；
 *   落库失败用 useDraftView.cancel(cr, target) 撤销登记，新建规则失败时删掉刚建的空单。
 * - 未开 CR_WRITE：变更单仍是前端 mock，start() 生成单号后 POST /rules/{id}/draft 落库规则草稿。
 */
async function onChangeOk(opts: { join?: string; title?: string; desc?: string }) {
  if (!REAL_API) { start(opts); return; }
  if (changeTarget.value === NEW_RULE) { await newRule(opts); return; }
  const id = base.value.id;
  let cr = '';
  try {
    cr = CR_WRITE ? start({ join: await crOpen(opts, { kind: 'rule', id, name: base.value.name }) }) : start(opts);
  } catch (e) { fail(e); return; }
  try {
    await openRuleDraft(id, cr);
    if (realId.value === id) await loadDetail();
  } catch (e) {
    cancel(cr);
    if (CR_WRITE) { // 库里的登记一并撤掉（尽力而为）：新建的单子删掉，加入的单子去掉本规则
      if (opts.join) await removeCRTarget(cr, { kind: 'rule', id }).catch(() => undefined);
      else await crDropEmpty(cr);
    }
    fail(e);
  }
}
const PENDING = '__new_rule__';
/* 新建规则（真接口）：先选对象类型和名称，再进变更弹窗。对象类型取本体（static 真接口 GET /object-types），没有时用 mock 对象类型 */
const newOpen = ref(false);
const newForm = reactive({ name: '', objKind: '' });
const objKindOptions = computed(() => {
  void catTick.value;
  const real = OBJECT_TYPE_LIST.map((o) => ({ value: o.id, label: `${o.name}（${o.id}）` }));
  return isStaticReal() ? real : (['product', 'store', 'order', 'region'] as const).map((k) => ({ value: k, label: `${objKindLabel(k)}（${k}）` }));
});
function openNew() {
  if (!REAL_API) { openChange(NEW_RULE); return; }
  newForm.name = ''; newForm.objKind = objKindOptions.value[0]?.value ?? '';
  newOpen.value = true;
}
function onNewOk() {
  if (!newForm.objKind) { message.warning('请选择对象类型'); return; }
  newOpen.value = false;
  openChange(NEW_RULE);
}
async function newRule(opts: { join?: string; title?: string; desc?: string }) {
  let cr = '';
  let created = false;
  let rid = '';
  try {
    if (CR_WRITE) { cr = await crOpen(opts); created = !opts.join; }
    else cr = start({ ...opts, target: PENDING }); // mock 单号：先占位登记，规则建好后换成规则编号
    // 默认对象类型由后台取本体里第一个对象类型
    const d = await createRule({ cr, name: newForm.name.trim() || undefined, objKind: newForm.objKind || undefined });
    rid = d.id;
    start({ join: cr, target: d.id });
    if (!CR_WRITE) cancel(cr, PENDING);
    await loadList();
    router.replace({ query: { ...route.query, ...RESET, rule: d.id, view: 'draft' } });
    message.success(`已新建规则 ${d.id}，在 ${cr} 的草稿里`);
  } catch (e) {
    if (rid) cancel(cr, rid);
    if (!CR_WRITE && cr) cancel(cr, PENDING);
    if (created) await crDropEmpty(cr);
    fail(e);
  }
}
/** 草稿横幅：提交审核 / 放弃草稿。CR_WRITE 关时保持原 mock 行为 */
async function onDraftSubmit() {
  const cr = draftCR.value!;
  if (REAL_API && CR_WRITE) {
    try { await submitCR(cr.id); } catch (e) { fail(e); return; }
  }
  message.success(`${cr.id} 已提交审核，可在“变更与发布”查看`);
}
async function onDraftDiscard() {
  const cr = draftCR.value!;
  const id = base.value.id;
  const isNewRule = live.value && remote.detail?.id === id && remote.detail.version == null;
  const ok = await confirm.ask({
    title: '放弃草稿', target: `规则 ${base.value.name}（${id}）`, okText: '放弃草稿', danger: true, irreversible: true,
    impact: isNewRule ? `这是未发布的新规则，放弃后规则一并删除，并从变更单 ${cr.id} 中移除。`
      : `本规则在变更单 ${cr.id} 里的改动全部丢弃，已发布版本不受影响。`,
  });
  if (!ok) return;
  if (REAL_API && CR_WRITE) {
    try {
      // 一个请求、一个事务：后台先删变更单里的登记和草稿行，再删规则草稿（新规则连规则一起删），不会留下孤儿
      await discardRuleDraft(id, cr.id);
    } catch (e) { fail(e); return; }
    forgetTarget(cr.id, 'rule', id);
    delete edits[id];
    setView('published');
    await loadList();
    if (isNewRule) router.replace({ query: { ...route.query, ...RESET, rule: undefined } });
    else if (realId.value === id) await loadDetail();
    message.success(`已放弃 ${cr.id} 里的本规则草稿`);
    return;
  }
  setView('published');
}
/** 草稿里的改动（自动暂存配置、阈值） */
const edits = reactive<Record<string, { autoStage: AutoStage; thresholds: string[] }>>({});
const draftDef = computed<RuleDef | undefined>(() => {
  if (!draftCR.value) return undefined;
  if (live.value) return remote.detail?.id === base.value.id && remote.detail.draft ? remote.detail.draft : undefined;
  const p = RULE_DRAFTS[base.value.id] ?? {};
  return { ...base.value, ...p, version: p.version ?? base.value.version + 1 };
});
function ensureEdits() {
  const d = draftDef.value!;
  return (edits[d.id] ??= { autoStage: JSON.parse(JSON.stringify(d.autoStage)), thresholds: d.conds.map((c) => c.value) });
}
const rule = computed<RuleDef>(() => {
  if (!isDraft.value || !draftDef.value) return base.value;
  const e = edits[base.value.id];
  if (!e) return draftDef.value;
  return { ...draftDef.value, autoStage: e.autoStage, conds: draftDef.value.conds.map((c, i) => ({ ...c, value: e.thresholds[i] ?? c.value })) };
});
const metricBumped = computed(() => {
  const d = draftDef.value; if (!d) return [] as { name: string; from: number; to: number }[];
  return d.conds.flatMap((c, i) => {
    const from = base.value.conds[i]?.metricVer;
    return c.metric && from && c.metricVer && c.metricVer !== from ? [{ name: metricById(c.metric)?.name ?? c.metric, from, to: c.metricVer }] : [];
  }).filter((x, i, a) => a.findIndex((y) => y.name === x.name) === i);
});
async function saveDraft() {
  const cr = draftCR.value!;
  const id = base.value.id, ctx = dynamicContextKey();
  if (REAL_API) {
    const e = ensureEdits();
    try {
      const r = await saveRuleDraft(id, { cr: cr.id, autoStage: e.autoStage, thresholds: e.thresholds });
      if (!mounted || ctx !== dynamicContextKey() || id !== base.value.id) return;
      if (remote.detail?.id === base.value.id) remote.detail = { ...remote.detail, draft: r.draft };
      delete edits[base.value.id];
      // 变更单写接口：改动数以库为准（saveRuleDraft 已回写）
      if (r.crChanges == null) cr.changes += 1;
    } catch (err) { if (mounted && ctx === dynamicContextKey() && id === base.value.id) fail(err); return; }
  } else cr.changes += 1;
  cr.updatedAt = nowText();
  message.success(`已保存到草稿 ${cr.id}，发布前不影响线上规则`);
}

/* 条件 */
const condCols = [
  { title: '指标 / 字段', key: 'field' },
  { title: '口径版本', key: 'ver', width: 110 },
  { title: '比较', dataIndex: 'op', width: 80 },
  { title: '阈值', key: 'value', width: 240 },
];

/* 触发方式 */
const evType = computed(() => (rule.value.trigger.eventId ? eventTypeById(rule.value.trigger.eventId) : undefined));

/* 动作 */
const actType = computed(() => (rule.value.actionTypeId ? actionTypeById(rule.value.actionTypeId) : undefined));
/** 外部系统名由动作类型目录按 EXT_SYSTEMS 解析（发起调价审批 → 泛微 OA） */
const actSystem = computed(() => actType.value?.system ?? '--');
// 真实接口下草稿是异步拿到的：草稿未到之前先显示已发布内容，避免 ensureEdits 读到空草稿
const stageEdit = computed(() => (isDraft.value && draftDef.value ? ensureEdits().autoStage : rule.value.autoStage));
const stageType = computed(() => (stageEdit.value.actionTypeId ? actionTypeById(stageEdit.value.actionTypeId) : undefined));
const actOptions = computed(() => { void catTick.value; return actionTypes().filter((a) => a.targetKind === rule.value.objKind && a.status !== '草稿').map((a) => ({ value: a.id, label: `${a.name} · ${a.version}` })); });
const params = computed(() => (stageType.value ? paramsOf(stageType.value) : []));
if (actionApi.isReal()) watch([actionContextKey, () => stageType.value?.id], async () => {
  const id = stageType.value?.id, key = actionContextKey();
  if (!id) return;
  try { await loadActionParams(id); }
  catch (e) { if (mounted && key === actionContextKey() && id === stageType.value?.id) fail(e); }
}, { immediate: true, flush: 'sync' });
const mappingRows = computed(() => params.value.map((p) => ({ param: p.name, required: p.required, from: stageEdit.value.mapping.find((m) => m.param === p.name)?.from ?? '' })));
/** 自动暂存开启时，必填参数（*）都要有取值来源才能保存 */
const missingReq = computed(() => (isDraft.value && stageEdit.value.enabled ? mappingRows.value.filter((r) => r.required && !r.from).map((r) => r.param) : []));
const saveTip = computed(() => (missingReq.value.length ? `自动暂存动作缺少必填参数的取值来源：${missingReq.value.join('、')}` : ''));
function setMapping(param: string, from: string) {
  const m = ensureEdits().autoStage.mapping; const row = m.find((x) => x.param === param);
  if (row) row.from = from; else m.push({ param, from });
}
function onStageType(id: string) { const s = ensureEdits().autoStage; s.actionTypeId = id; s.mapping = []; }
const mapCols = [{ title: '动作参数', key: 'param', width: 160 }, { title: '取值来源', key: 'from' }];
const staged = computed(() => (live.value ? remote.staged : stagedOf(base.value.id)));
const stagedPending = computed(() => staged.value.filter((a) => a.status === '待确认').length);

/* 命中历史 */
const HRANGES = [{ label: '近 7 天', value: '7d' }, { label: '近 30 天', value: '30d' }];
const hrange = computed({ get: () => (q('hrange') === '30d' ? '30d' : '7d'), set: (v) => setQ({ hrange: v === '7d' ? undefined : v, hpage: undefined }) });
const hq = usePagedQuery({ defaultSize: 10, prefix: 'h' });
const hits7 = computed(() => (live.value ? remote.hits7 : hitsOf(base.value.id).filter((h) => h.at > '2026-10-18 23:59:59').length));
const hitsAll = computed(() => hitsOf(base.value.id).filter((h) => h.at > (hrange.value === '7d' ? '2026-10-18 23:59:59' : '2026-09-25')));
const hits = computed(() => (live.value ? remote.hits : paginate(hitsAll.value, { page: hq.page.value, size: hq.size.value })));
/**
 * 命中的上下文维度（通用 dims：后台 hit.dims；mock 命中 / 回放样本由 mock/rules 用公共 alertDims() 给出）。
 * 有维度的规则（去重键含维度，如商品×门店）单列“维度”。
 */
type DimRow = { dims?: HitDim[] };
const dimsOf = (h: DimRow): HitDim[] => h.dims ?? [];
const DIM_COL = { title: '维度', key: 'dims', width: 220, ellipsis: true };
const hasDims = (rows: DimRow[]) => rows.some((h) => dimsOf(h).length > 0);
/** 对象列标题 = 对象类型的标题字段（static GET /object-types/{id}/meta 的 titleProp）；拿不到时用对象类型名 */
const titleNames = reactive<Record<string, string>>({});
let titleSeq = 0;
watch([dynamicContextKey, () => base.value.objKind], ([ctx, k]) => {
  const seq = ++titleSeq;
  if (!k || !isStaticReal() || titleNames[k]) return;
  getObjectTypeMeta(k).then((m) => {
    const p = m.properties.find((x) => x.key === m.titleProp);
    if (mounted && seq === titleSeq && ctx === dynamicContextKey() && p) titleNames[k] = p.name;
  }, () => { /* 后台没起来 / 无该对象类型：用对象类型名 */ });
}, { immediate: true, flush: 'sync' });
const objTitle = computed(() => titleNames[base.value.objKind] ?? objKindLabel(base.value.objKind) ?? '对象');
const hitCols = computed(() => [
  { title: '命中时间', dataIndex: 'at', width: 172 },
  { title: '告警 / 命中编号', key: 'id', width: 200 },
  { title: objTitle.value, key: 'obj', ellipsis: true },
  ...(hasDims(hits.value.items) ? [DIM_COL] : []),
  { title: '命中值', dataIndex: 'value', width: 200, ellipsis: { showTitle: true } },
  { title: '阈值', dataIndex: 'threshold', width: 160, ellipsis: { showTitle: true } },
  { title: '处理状态', key: 'result', width: 110 },
]);
const hitOpen = computed<Hit | RuleHitDetailDto | undefined>(() => (q('hit')
  ? (live.value ? (remote.hit?.id === q('hit') ? remote.hit : undefined) : hitsOf(base.value.id).find((h) => h.id === q('hit'))) : undefined));
const openHit = (h: Hit) => setQ({ hit: h.id });
const hitLinked = computed(() => (live.value ? (remote.hit?.linkedAction ?? undefined) : hitOpen.value?.alertId ? ACTIONS.find((a) => a.source.alertId === hitOpen.value!.alertId) : undefined));
const hitTimeline = computed(() => {
  const h = hitOpen.value; if (!h) return [];
  if (live.value) return remote.hit?.timeline ?? [];
  if ('alert' in h && h.alert) return alertTimeline(h.alert as Alert).map((t) => ({ time: t.time, text: t.text, desc: t.who, status: t.abnormal ? 'warn' : 'done' }));
  return [{ time: h.at, text: `规则 ${h.ruleId} v${rule.value.version} 命中`, desc: '系统', status: 'done' },
    { time: h.at, text: h.result === '已去重' ? `去重键相同，冷却期（${rule.value.cooldown}）内不再通知` : `已通知负责人 ${rule.value.owner}`, desc: '系统', status: 'done' }];
});
/** 条件页下半部：最近几次命中（只读速览，完整列表在“命中历史”）；草稿多横幅和旧表达式，只放 3 条保证一屏 */
const recentHits = computed(() => (live.value ? remote.recent : [...hitsOf(base.value.id)]).sort((a, b) => b.at.localeCompare(a.at)).slice(0, isDraft.value ? 3 : 5));
const recentCols = computed(() => [
  { title: '命中时间', dataIndex: 'at', width: 172 },
  { title: '告警 / 命中编号', key: 'id', width: 200 },
  { title: objTitle.value, key: 'obj', ellipsis: true },
  { title: '命中值', dataIndex: 'value', width: 200, ellipsis: { showTitle: true } },
  { title: '处理状态', key: 'result', width: 110 },
]);
const resultBadge = (s: string) => (s === '待处理' ? 'warning' : 'default');

/* 回放试跑 */
const rrange = computed({ get: () => (q('rrange') === '30d' ? '30d' : '7d'), set: (v) => setQ({ rrange: v === '7d' ? undefined : v, run: undefined, rpage: undefined }) });
const rver = computed({ get: () => (q('rver') === 'draft' && draftDef.value ? 'draft' : 'cur'), set: (v) => setQ({ rver: v === 'cur' ? undefined : v, run: undefined, rpage: undefined }) });
const VER_OPTS = computed(() => [{ label: `当前 v${base.value.version}`, value: 'cur' }, ...(draftDef.value ? [{ label: `草稿 v${draftDef.value.version}`, value: 'draft' }] : [])]);
const ran = computed(() => q('run') === '1');
const running = ref(false);
const replayError = ref('');
const rmode = computed({ get: () => q('rmode') === 'events' ? 'events' : q('rmode') === 'snapshot' ? 'snapshot' : base.value.trigger.kind === '事件' ? 'events' : 'snapshot', set: (v) => setQ({ rmode: v, run: undefined, rpage: undefined }) });
const rq = usePagedQuery({ defaultSize: 10, prefix: 'r' });
const replayContextKey = computed(() => `${dynamicContextKey()}|${base.value.id}|${rmode.value}|${rrange.value}|${rver.value}|${JSON.stringify(rver.value === 'draft' ? draftDef.value : base.value)}`);
const replayAnchor = ref<{ context: string; token: string } | null>(null);
const replayKey = computed(() => `${replayContextKey.value}|${rq.page.value}|${rq.size.value}`);
let replaySeq = 0;
async function fetchReplay() {
  const seq = ++replaySeq;
  if (!mounted || !base.value.id || readonly.value) return;
  const k = replayKey.value, ctx = dynamicContextKey();
  const current = () => mounted && seq === replaySeq && ctx === dynamicContextKey() && k === replayKey.value;
  remote.replay = null; replayError.value = '';
  running.value = true;
  try {
    const anchor = replayAnchor.value?.context === replayContextKey.value ? replayAnchor.value.token : undefined;
    const r = await replayRule(base.value.id, rrange.value as '7d' | '30d', rver.value === 'draft' ? 'draft' : 'current', rmode.value, rq.page.value, rq.size.value, anchor);
    if (current()) { replayAnchor.value = { context: replayContextKey.value, token: r.anchor }; remote.replay = { ...r, k }; }
  } catch (e) { if (current()) { replayError.value = isApiError(e) ? crErrText(e) : String(e); readFail(e); } } finally { if (current()) running.value = false; }
}
function runReplay() {
  if (readonly.value) return;
  if (live.value) { replayAnchor.value = null; if (ran.value && rq.page.value === 1) fetchReplay(); else setQ({ run: '1', rpage: undefined }); return; }
  running.value = true; setTimeout(() => { running.value = false; setQ({ run: '1', rpage: undefined }); }, 400);
}
const result = computed(() => (!ran.value ? null : live.value ? (remote.replay?.k === replayKey.value ? remote.replay : null)
  : replay(base.value, rrange.value as '7d' | '30d', rver.value === 'draft')));
const samples = computed(() => live.value ? { items: (remote.replay?.k === replayKey.value ? remote.replay.samples : []) as (ReplaySampleDto & DimRow)[], total: remote.replay?.k === replayKey.value ? remote.replay.total : 0 } : paginate((result.value?.samples ?? []) as (ReplaySampleDto & DimRow)[], { page: rq.page.value, size: rq.size.value }));
const realReplay = computed(() => live.value && remote.replay?.k === replayKey.value ? remote.replay : null);
watch(replayContextKey, () => { replayError.value = ''; replayAnchor.value = null; }, { flush: 'sync' });
const dedupeText = computed(() => (live.value ? remote.replay?.dedupeReason ?? '' : dedupeReason(base.value)));
const sampleCols = computed(() => [
  { title: '时间', dataIndex: 'at', width: 180 },
  { title: objTitle.value, key: 'obj', width: 240, ellipsis: { showTitle: true } },
  ...(hasDims(result.value?.samples ?? []) ? [DIM_COL] : []),
  { title: live.value ? '计算输入' : '命中值', dataIndex: 'value', ellipsis: { showTitle: true } },
  { title: live.value ? '条件计算结果' : '与实际命中对比', key: 'diff', width: 220, ellipsis: { showTitle: true } },
]);

/* 版本历史 */
const versions = computed(() => (live.value ? remote.versions : ruleVersions(base.value, draftDef.value?.version)));
const confirmers = computed(() => (live.value ? remote.meta.confirmers : CONFIRMERS));
const mappingSources = computed(() => (live.value ? remote.meta.mappingSources : MAPPING_SOURCES));

// ---- 真接口：按当前选中 / 页签 / 筛选取数，失败给提示 ----
let listSeq = 0;
async function loadList() {
  const seq = ++listSeq, ctx = dynamicContextKey();
  if (!mounted) return;
  try {
    const r = await listRules({ trigger: trig.value, status: stf.value, kw: kw.value, page: lpage.value, size: LPAGE });
    if (mounted && seq === listSeq && ctx === dynamicContextKey()) { remote.items = r.items; remote.total = r.total; if (!defaultRuleId.value) defaultRuleId.value = r.items[0]?.id ?? ''; if (r.page && r.page !== lpage.value) lpage.value = r.page; }
  } catch (e) { if (mounted && seq === listSeq && ctx === dynamicContextKey()) { remote.items = []; readFail(e); } }
}
const guard = <T,>(key: () => string, run: () => Promise<T>, set: (v: T) => void, clear = () => {}) => {
  let seq = 0;
  return async () => {
    const s = ++seq, ctx = dynamicContextKey(); const k = key();
    if (!mounted) return;
    clear(); if (!k) return;
    const current = () => mounted && s === seq && ctx === dynamicContextKey() && k === key();
    try { const v = await run(); if (current()) set(v); } catch (e) { if (current()) readFail(e); }
  };
};
const loadDetail = guard(() => realId.value, () => getRule(realId.value), (v) => { remote.detail = v; }, () => { remote.detail = null; });
const loadOverview = guard(() => realId.value, async () => {
  const id = realId.value;
  const [h7, rec, vers, st] = await Promise.all([listRuleHits(id, '7d', 1, 1), listRuleHits(id, '30d', 1, 5), listRuleVersions(id), listRuleActionRuns(id)]);
  return { h7: h7.total, rec: rec.items, vers, st: st.items as unknown as ActionRecord[] };
}, (v) => { remote.hits7 = v.h7; remote.recent = v.rec; remote.versions = v.vers; remote.staged = v.st; });
const loadHits = guard(() => (tab.value === 'hits' && realId.value ? `${realId.value}|${hrange.value}|${hq.page.value}|${hq.size.value}` : ''), () => listRuleHits(realId.value, hrange.value as '7d' | '30d', hq.page.value, hq.size.value), (v) => { remote.hits = v; if (v.page && v.page !== hq.page.value) hq.page.value = v.page; });
const loadHit = guard(() => q('hit'), () => getRuleHit(q('hit')), (v) => { remote.hit = v as RuleHitDetailDto; }, () => { remote.hit = null; });
if (live.value) {
  watch(dynamicContextKey, () => { ++listSeq; ++replaySeq; for (const id of Object.keys(edits)) delete edits[id]; for (const id of Object.keys(titleNames)) delete titleNames[id]; defaultRuleId.value = ''; remote.items = []; remote.total = 0; remote.detail = null; remote.meta = { confirmers: [], mappingSources: [], statuses: [] }; remote.hits = { items: [], total: 0 }; remote.hits7 = 0; remote.recent = []; remote.hit = null; remote.replay = null; remote.versions = []; remote.staged = []; running.value = false; }, { flush: 'sync' });
  const loadMeta = guard(() => dynamicContextKey(), getRulesMeta, (m) => { remote.meta = m; });
  watch(dynamicContextKey, loadMeta, { immediate: true, flush: 'sync' });
  watch([dynamicContextKey, kw, trig, stf, lpage], loadList, { immediate: true, flush: 'sync' });
  watch([dynamicContextKey, realId], () => { remote.detail = null; remote.hits = { items: [], total: 0 }; remote.recent = []; remote.hits7 = 0; remote.hit = null; remote.replay = null; ++replaySeq; running.value = false; remote.versions = []; remote.staged = []; loadDetail(); loadOverview(); }, { immediate: true, flush: 'sync' });
  watch([dynamicContextKey, realId, tab, hrange, hq.page, hq.size], loadHits, { immediate: true, flush: 'sync' });
  watch([dynamicContextKey, () => q('hit')], loadHit, { immediate: true, flush: 'sync' });
  watch([dynamicContextKey, ran, replayKey, () => remote.detail?.id], () => { if (ran.value && remote.detail?.id === realId.value && remote.replay?.k !== replayKey.value) fetchReplay(); }, { immediate: true, flush: 'sync' });
}
const verCols = [
  { title: '版本', key: 'ver', width: 80 },
  { title: '状态', key: 'status', width: 96 },
  { title: '变更说明', dataIndex: 'change' },
  { title: '发布人', dataIndex: 'by', width: 96 },
  { title: '时间', dataIndex: 'at', width: 160 },
  { title: '操作', key: 'op', width: 110 },
];
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="reason"><a-button :disabled="readonly" @click="openNew">新建规则</a-button></a-tooltip>
  </Teleport>

  <div class="ru">
    <div class="panel ru-list">
      <div class="ru-filter">
        <a-input-search v-model:value="kw" placeholder="搜索名称" class="ru-kw" />
        <a-select v-model:value="trig" :options="['全部', '定时', '事件'].map((g) => ({ value: g, label: g === '全部' ? '全部触发' : g }))" class="ru-sel" />
        <a-select v-model:value="stf" :options="STATUS_OPTS" class="ru-sel" />
      </div>
      <ul class="ru-items">
        <li v-for="r in list" :key="r.id" :class="{ active: r.id === base.id }" @click="pick(r.id)">
          <div class="ru-row" :title="`${r.id} · ${r.trigger.kind} · ${objKindLabel(r.objKind)} · ${r.output}`">
            <span class="t-14 ru-name">{{ r.name }}</span>
            <span class="t-12 num ru-meta"><span v-if="r.status !== '已发布'" :class="ruleStatus(r).badge === 'warning' ? 'tl-attention' : 'c-2'">{{ ruleStatus(r).text }} · </span><span class="c-1">v{{ r.version }}</span></span>
          </div>
        </li>
      </ul>
      <div class="ru-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="listTotal" :size="LPAGE" unit="条规则" class="ru-pager" empty-text="没有匹配的规则" />
      </div>
    </div>

    <div v-if="!live || realId" class="panel ru-detail">
      <DraftBanner v-if="isDraft && draftCR" :draft="draftCR"
        @submit="onDraftSubmit" @discard="onDraftDiscard" />
      <div class="ru-head">
        <span class="t-16 fw-600 c-1">{{ rule.name }}</span>
        <span class="t-14 c-3 num">{{ rule.id }}</span>
        <span class="t-14 c-3 num">{{ isDraft ? `草稿 v${rule.version}（${base.version == null ? '未发布' : `已发布 v${base.version}`}）` : base.version == null ? '未发布' : `v${base.version}` }}</span>
        <a-tooltip :title="ruleStatus(base).cr ? `所在变更单 ${ruleStatus(base).cr}` : undefined"><a-badge :status="ruleStatus(base).badge" :text="ruleStatus(base).text" /></a-tooltip>
        <a-segmented v-if="draftCR && !readonly" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draftCR.id}`, value: 'draft' }]"
          @change="(v: any) => setView(v)" />
        <span class="ru-sp" />
        <span v-if="readonly" class="t-12 c-3">{{ reason }}</span>
        <a-tooltip :title="readonly ? reason : isDraft ? saveTip : ''">
          <a-button v-if="!draftCR || readonly" type="primary" :disabled="readonly" @click="openChange(`${base.name}（${base.id}）`)">发起变更</a-button>
          <a-button v-else-if="!isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
          <a-button v-else type="primary" :disabled="missingReq.length > 0" @click="saveDraft">保存到草稿</a-button>
        </a-tooltip>
        <span v-if="!readonly && missingReq.length" class="t-12 c-warning">缺少必填参数：{{ missingReq.join('、') }}</span>
      </div>
      <a-tabs v-model:activeKey="tab" class="ru-tabs">
        <a-tab-pane key="cond" tab="条件" />
        <a-tab-pane key="trigger" tab="触发方式" />
        <a-tab-pane key="action" tab="动作" />
        <a-tab-pane key="hits" tab="命中历史" />
        <a-tab-pane key="replay" tab="回放试跑" />
        <a-tab-pane key="versions" tab="版本历史" />
      </a-tabs>

      <!-- 条件 -->
      <div v-if="tab === 'cond'" class="ru-body">
        <div v-if="isDraft && metricBumped.length" class="ru-remind t-12">
          <span class="c-warning">条件引用的{{ metricBumped.map((m) => `${m.name}口径 v${m.from} → v${m.to}`).join('、') }}：口径升版后需回放试跑</span>
          <a @click="tab = 'replay'; setQ({ tab: 'replay', rver: 'draft' })">去回放试跑</a>
        </div>
        <div class="ru-sec t-14 fw-600">{{ rule.desc }}</div>
        <div class="ru-tool">
          <span class="t-12 c-2">以下条件同时满足（{{ rule.logic }}）时命中</span>
          <InfoTip text="指标引用的是本体里发布的口径，口径升版不会自动跟随" />
          <span class="ru-sp" />
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!isDraft">添加条件</a-button></a-tooltip>
        </div>
        <a-table :columns="condCols" :data-source="rule.conds" :pagination="false" size="middle" :row-key="(r: any) => r.field + r.op">
          <template #bodyCell="{ column, record, index }">
            <template v-if="column.key === 'field'">
              <HoverCard v-if="record.metric" kind="metric" :id="record.metric">{{ record.field }}</HoverCard>
              <span v-else>{{ record.field }}</span>
            </template>
            <template v-else-if="column.key === 'ver'">
              <template v-if="record.metricVer">
                <span v-if="isDraft && record.metricVer !== base.conds[index]?.metricVer" class="num c-2"><span class="c-3 ru-old">v{{ base.conds[index]?.metricVer }}</span> → 口径 v{{ record.metricVer }}<a-tag class="ru-diff">修改</a-tag></span>
                <span v-else class="num c-2">口径 v{{ record.metricVer }}</span>
              </template>
              <span v-else class="c-3">对象字段</span>
            </template>
            <template v-else-if="column.key === 'value'">
              <a-input v-if="isDraft" size="small" :value="record.value" class="ru-th" @update:value="(v: string) => (ensureEdits().thresholds[index] = v)" />
              <span v-else class="num">{{ record.value }}</span>
            </template>
          </template>
        </a-table>
        <div class="ru-sec t-14 fw-600">原始表达式</div>
        <template v-if="isDraft && rule.expr !== base.expr">
          <pre class="ru-expr ru-expr-old num t-12 c-3">{{ base.expr }}</pre>
          <pre class="ru-expr num t-12">{{ rule.expr }}<a-tag class="ru-diff">修改</a-tag></pre>
        </template>
        <pre v-else class="ru-expr num t-12">{{ rule.expr }}</pre>
        <a-descriptions :column="3" bordered size="small">
          <a-descriptions-item label="去重键">{{ rule.dedupe }}</a-descriptions-item>
          <a-descriptions-item label="冷却">{{ rule.cooldown }}</a-descriptions-item>
          <a-descriptions-item label="命中后">{{ rule.output === '告警' ? `生成${rule.level}级告警，进入告警处理` : '通知负责人（不生成告警）' }}</a-descriptions-item>
          <a-descriptions-item label="对象">{{ objKindLabel(rule.objKind) }}</a-descriptions-item>
          <a-descriptions-item label="负责人">{{ rule.owner }}</a-descriptions-item>
          <a-descriptions-item label="近 7 天命中">{{ fmtInt(hits7) }} 次</a-descriptions-item>
        </a-descriptions>
        <div class="ru-tool ru-sec">
          <span class="t-14 fw-600">最近命中</span>
          <span class="ru-sp" />
          <a class="t-12" @click="tab = 'hits'">查看全部命中历史</a>
        </div>
        <a-table :columns="recentCols" :data-source="recentHits" :pagination="false" size="small" row-key="id" :locale="{ emptyText: '还没有命中' }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'id'"><a class="num" @click="openHit(record)">{{ record.id }}</a></template>
            <template v-else-if="column.key === 'obj'"><ObjRef :kind="record.objKind" :id="record.objId">{{ record.objLabel }}</ObjRef></template>
            <template v-else-if="column.key === 'result'"><a-badge :status="resultBadge(record.result)" :text="record.result" /></template>
          </template>
        </a-table>
      </div>

      <!-- 触发方式 -->
      <div v-else-if="tab === 'trigger'" class="ru-body">
        <div class="ru-tool">
          <a-segmented :value="rule.trigger.kind" :options="['定时', '事件']" size="small" :disabled="!isDraft" />
          <span class="t-12 c-2">{{ rule.trigger.kind === '定时' ? '按计划时间批量计算' : '收到事件后逐条计算，延迟秒级' }}</span>
          <InfoTip text="规则版本发布后才按新的触发方式运行；草稿里的修改只在回放试跑中生效。" />
          <span class="ru-sp" />
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!isDraft">修改触发方式</a-button></a-tooltip>
        </div>
        <a-descriptions v-if="rule.trigger.kind === '定时'" :column="2" bordered size="small">
          <a-descriptions-item label="计划表达式"><span class="num">{{ rule.trigger.cron }}</span></a-descriptions-item>
          <a-descriptions-item label="含义">{{ rule.trigger.text }}</a-descriptions-item>
          <a-descriptions-item label="时区">Asia/Shanghai（UTC+8）</a-descriptions-item>
          <a-descriptions-item label="错过的运行">不补跑，下一次运行一并计算</a-descriptions-item>
          <a-descriptions-item label="数据依赖">等待当日数据同步完成后再计算，最晚延后 30 分钟</a-descriptions-item>
          <a-descriptions-item label="单次超时">10 分钟，超时记为运行失败并通知负责人</a-descriptions-item>
        </a-descriptions>
        <a-descriptions v-else :column="2" bordered size="small">
          <a-descriptions-item label="订阅事件">{{ evType?.name }}<span class="t-12 c-3 num ru-gap">{{ evType?.api }} · {{ evType?.version }}</span></a-descriptions-item>
          <a-descriptions-item label="事件来源">{{ evType?.sourceRef }}</a-descriptions-item>
          <a-descriptions-item label="过滤条件"><span class="num">{{ rule.trigger.filter }}</span></a-descriptions-item>
          <a-descriptions-item label="投递保证">{{ evType?.delivery }} · {{ evType?.ordering }}</a-descriptions-item>
          <a-descriptions-item label="事件去重">{{ evType?.dedupKey }}，窗口 {{ evType?.dedupWindow }}</a-descriptions-item>
          <a-descriptions-item label="近 7 天收到">{{ fmtInt(evType?.weekly) }} 条事件，命中 {{ fmtInt(hits7) }} 次</a-descriptions-item>
        </a-descriptions>
      </div>

      <!-- 动作 -->
      <div v-else-if="tab === 'action'" class="ru-body ru-act">
        <div class="ru-act-left">
          <div class="ru-sec t-14 fw-600">命中后可执行的动作</div>
          <a-descriptions :column="2" bordered size="small">
            <a-descriptions-item label="动作类型">{{ actType ? `${actType.name} · ${actType.version}` : rule.output === '告警' ? '无（只产生告警）' : '无（只通知）' }}</a-descriptions-item>
            <a-descriptions-item label="外部系统">{{ actSystem }}</a-descriptions-item>
            <a-descriptions-item label="审批">{{ actType?.approval ?? '--' }}</a-descriptions-item>
            <a-descriptions-item label="幂等键"><span class="num">{{ actType?.idemKey ?? '--' }}</span></a-descriptions-item>
          </a-descriptions>
          <div class="ru-sec t-14 fw-600 ru-sec-row">
            <span>自动暂存动作 <InfoTip text="命中后按下面的映射生成动作，放进动作中心“待确认”，由确认人逐条确认后才提交外部系统；规则不会直接执行动作。" /></span>
            <a-tooltip :title="editTip">
              <a-switch :checked="stageEdit.enabled" :disabled="!isDraft" size="small" @change="(v: any) => (ensureEdits().autoStage.enabled = !!v)" />
            </a-tooltip>
          </div>
          <div class="ru-form">
            <span class="t-12 c-3">动作类型</span>
            <a-select :value="stageEdit.actionTypeId || undefined" :options="actOptions" :disabled="!isDraft || !stageEdit.enabled" placeholder="选择动作类型" size="small" @change="(v: any) => onStageType(v)" />
            <span class="t-12 c-3">确认人</span>
            <a-select :value="stageEdit.confirmer || undefined" :options="confirmers.map((c) => ({ value: c, label: c }))" :disabled="!isDraft || !stageEdit.enabled" placeholder="选择确认人" size="small"
              @change="(v: any) => (ensureEdits().autoStage.confirmer = v)" />
          </div>
          <a-table :columns="mapCols" :data-source="mappingRows" :pagination="false" size="small" row-key="param" class="ru-map"
            :locale="{ emptyText: '先选择动作类型' }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'param'">{{ record.param }}<span v-if="record.required" class="c-error"> *</span></template>
              <template v-else-if="column.key === 'from'">
                <a-select v-if="isDraft && stageEdit.enabled" size="small" :value="record.from || undefined" placeholder="选择取值来源" class="ru-mapsel" allow-clear
                  :options="[...new Set([record.from, ...mappingSources].filter(Boolean))].map((m) => ({ value: m, label: m }))" @change="(v: any) => setMapping(record.param, v ?? '')" />
                <span v-else :class="record.from ? 'c-1' : 'c-3'">{{ record.from || '未映射' }}</span>
              </template>
            </template>
          </a-table>
        </div>
        <div class="ru-act-right">
          <div class="ru-sec t-14 fw-600 ru-sec-row"><span>已暂存的动作 <InfoTip text="在左侧导航“运行 · 动作中心”的“待确认”里确认或撤回。" /></span><span class="t-12 c-2 fw-400">待确认 {{ stagedPending }} 条 · 共 {{ staged.length }} 条</span></div>
          <div v-for="a in staged.slice(0, 9)" :key="a.id" class="ru-st">
            <span class="t-12 num c-2">{{ a.id }}</span>
            <span class="t-12 c-1 ru-st-o" :title="a.objLabel">{{ a.objLabel }}</span>
            <span v-if="a.status === '结果未知'" class="t-12 tl-attention"><QuestionCircleOutlined /> 结果未知 · 待对账</span>
            <a-badge v-else :status="a.status === '待确认' ? 'warning' : a.status === '失败' ? 'error' : 'default'" :text="a.status" />
          </div>
          <div v-if="!staged.length" class="t-12 c-3">暂无。开启后命中生成的动作会出现在这里和动作中心“待确认”。</div>
        </div>
      </div>

      <!-- 命中历史 -->
      <div v-else-if="tab === 'hits'" class="ru-body">
        <div class="ru-tool">
          <a-segmented v-model:value="hrange" :options="HRANGES" size="small" />
          <span class="ru-sp" />
          <span class="t-12 c-3">{{ rule.output === '告警' ? '点告警编号在当前页查看告警与处理记录' : '该规则命中只发通知，点编号查看命中详情' }}</span>
        </div>
        <div class="paged-scroll">
          <a-table :columns="hitCols" :data-source="hits.items" :pagination="false" size="middle" row-key="id" :locale="{ emptyText: '该时间范围内没有命中' }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'id'"><a class="num" @click="openHit(record)">{{ record.id }}</a></template>
              <template v-else-if="column.key === 'obj'"><ObjRef :kind="record.objKind" :id="record.objId">{{ record.objLabel }}</ObjRef></template>
              <template v-else-if="column.key === 'dims'"><template v-if="dimsOf(record).length"><template v-for="(dm, i) in dimsOf(record)" :key="`${dm.objKind}:${dm.id}`">{{ i ? '、' : '' }}<ObjRef :kind="dm.objKind" :id="dm.id">{{ dm.label ?? dm.id }}</ObjRef></template></template><span v-else class="c-3">--</span></template>
              <template v-else-if="column.key === 'result'"><a-badge :status="resultBadge(record.result)" :text="record.result" /></template>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="hq.page.value" v-model:size="hq.size.value" variant="full" :total="hits.total" :default-size="hq.defaultSize" unit="次命中" />
      </div>

      <!-- 回放试跑 -->
      <div v-else-if="tab === 'replay'" class="ru-body">
        <div v-if="metricBumped.length" class="ru-remind t-12">
          <span class="c-warning">草稿把{{ metricBumped.map((m) => `${m.name}口径 v${m.from} → v${m.to}`).join('、') }}：口径升版后需回放试跑，确认命中变化再提交审核</span>
        </div>
        <div class="ru-tool">
          <a-segmented v-if="live" v-model:value="rmode" :options="[{ label: '当前数据推演', value: 'snapshot' }, { label: '历史事件回放', value: 'events' }]" size="small" />
          <template v-if="!live || rmode === 'events'">
            <span class="t-12 c-3">时间范围</span>
            <a-segmented v-model:value="rrange" :options="HRANGES" size="small" />
          </template>
          <span class="t-12 c-3">规则版本</span>
          <a-segmented v-model:value="rver" :options="VER_OPTS" size="small" />
          <a-tooltip :title="reason"><a-button size="small" :loading="running" :disabled="readonly" @click="runReplay">开始试跑</a-button></a-tooltip>
          <span class="ru-sp" />
          <span class="t-12 c-3">只计算条件，不生成告警或动作</span>
        </div>
        <a-alert v-if="replayError" type="error" show-icon :message="replayError" />
        <template v-if="result">
          <template v-if="realReplay">
            <a-alert v-if="realReplay.unknown" type="warning" show-icon :message="`${realReplay.unknown} 个样本缺少有效输入，详见列表`" />
            <div class="ru-stats">
              <div><span class="t-12 c-2">可见样本</span><span class="t-16 fw-600 c-1 num">{{ realReplay.candidates }}</span></div>
              <div><span class="t-12 c-2">已求值</span><span class="t-16 fw-600 c-1 num">{{ realReplay.evaluated }}</span></div>
              <div><span class="t-12 c-2">条件满足</span><span class="t-16 fw-600 c-1 num">{{ realReplay.would }}</span></div>
              <div><span class="t-12 c-2">无法求值</span><span class="t-16 fw-600 c-1 num">{{ realReplay.unknown }}</span></div>
              <div v-if="realReplay.filtered"><span class="t-12 c-2">事件筛选排除</span><span class="t-16 fw-600 c-1 num">{{ realReplay.filtered }}</span></div>
            </div>
            <span class="t-12 c-3">计算于 {{ realReplay.ranAt }} · {{ rver === 'draft' ? '已保存草稿' : '当前规则' }} v{{ realReplay.ruleVersion }} <InfoTip :text="`${realReplay.notice} 分页固定本次计算时间，有效期10分钟；输入变化需重新试跑。`" /></span>
          </template>
          <div v-else class="ru-stats">
            <div><span class="t-12 c-2">实际命中（v{{ base.version }}）</span><span class="t-16 fw-600 c-1 num">{{ result.actual }}</span></div>
            <div><span class="t-12 c-2">试跑会命中（{{ rver === 'draft' ? `草稿 v${draftDef?.version}` : `当前 v${base.version}` }}）</span><span class="t-16 fw-600 c-1 num">{{ result.would }}</span></div>
            <div><span class="t-12 c-2">新增命中</span><span class="t-16 fw-600 num c-1">{{ result.added }}</span></div>
            <div><span class="t-12 c-2">不再命中</span><span class="t-16 fw-600 num c-1">{{ result.removed }}</span></div>
            <div><span class="t-12 c-2">被去重 <InfoTip :text="`命中数只算会生成告警 / 通知的命中；去重键相同、在冷却期（${base.cooldown}）内的样本列为“被去重”，不计入`" /></span><span class="t-16 fw-600 num c-1">{{ result.deduped }}</span></div>
          </div>
          <div class="paged-scroll">
            <a-table :columns="sampleCols" :data-source="samples.items" :pagination="false" size="middle" row-key="key" :locale="{ emptyText: realReplay?.candidates === 0 ? '没有可见的真实数据样本，无法据此判断规则效果' : '没有条件满足或无法求值的样本' }">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'obj'"><ObjRef :kind="record.objKind" :id="record.objId">{{ record.objLabel }}</ObjRef></template>
                <template v-else-if="column.key === 'dims'"><template v-if="dimsOf(record).length"><template v-for="(dm, i) in dimsOf(record)" :key="`${dm.objKind}:${dm.id}`">{{ i ? '、' : '' }}<ObjRef :kind="dm.objKind" :id="dm.id">{{ dm.label ?? dm.id }}</ObjRef></template></template><span v-else class="c-3">--</span></template>
                <template v-else-if="column.key === 'diff'"><a-badge status="default" :text="record.diff === '被去重' ? `被去重 · ${dedupeText}` : record.diff" /></template>
              </template>
            </a-table>
          </div>
          <AppPagination v-model:page="rq.page.value" v-model:size="rq.size.value" variant="full" :total="samples.total" :default-size="rq.defaultSize" unit="个样本" />
        </template>
        <a-empty v-else class="ru-empty" description="选择时间范围和版本后开始试跑" />
      </div>

      <!-- 版本历史 -->
      <div v-else class="ru-body">
        <div class="ru-tool">
          <span class="t-12 c-2">共 {{ versions.length }} 个版本</span>
          <InfoTip text="规则版本随本体整版发布；当前尚未提供单条规则的版本对比和回滚接口。" />
        </div>
        <a-table :columns="verCols" :data-source="versions" :pagination="false" size="middle" row-key="ver">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'ver'"><span class="num">v{{ record.ver }}</span></template>
            <template v-else-if="column.key === 'status'"><a-badge :status="record.status === '草稿' ? 'warning' : 'default'" :text="record.status" /></template>
            <template v-else-if="column.key === 'op'">
              <a-space><a-tooltip title="尚未提供规则版本对比接口"><a-button type="link" size="small" disabled>对比</a-button></a-tooltip><a-tooltip v-if="record.status === '已替代'" title="尚未提供规则版本回滚接口"><a-button type="link" size="small" disabled>回滚</a-button></a-tooltip></a-space>
            </template>
          </template>
        </a-table>
      </div>
    </div>
  </div>

  <a-drawer :open="!!hitOpen" :width="520" :title="hitOpen ? (hitOpen.alertId ? `告警 ${hitOpen.id}` : `命中 ${hitOpen.id}`) : ''" @close="setQ({ hit: undefined })">
    <template v-if="hitOpen">
      <a-descriptions :column="1" bordered size="small">
        <a-descriptions-item label="规则">{{ base.id }} {{ base.name }} · v{{ base.version }}</a-descriptions-item>
        <a-descriptions-item label="对象"><ObjRef :kind="hitOpen.objKind" :id="hitOpen.objId">{{ hitOpen.objLabel }}</ObjRef></a-descriptions-item>
        <a-descriptions-item v-if="dimsOf(hitOpen).length" label="维度"><template v-for="(dm, i) in dimsOf(hitOpen)" :key="`${dm.objKind}:${dm.id}`">{{ i ? '、' : '' }}<ObjRef :kind="dm.objKind" :id="dm.id">{{ dm.label ?? dm.id }}</ObjRef></template></a-descriptions-item>
        <a-descriptions-item label="命中值">{{ hitOpen.value }}，阈值 {{ hitOpen.threshold }}</a-descriptions-item>
        <a-descriptions-item label="命中时间"><span class="num">{{ hitOpen.at }}</span></a-descriptions-item>
        <a-descriptions-item v-if="hitOpen.alert" label="级别 / 处理人">{{ hitOpen.alert.level }} · {{ hitOpen.alert.assignee ?? '未认领' }}</a-descriptions-item>
        <a-descriptions-item label="状态"><a-badge :status="resultBadge(hitOpen.result)" :text="hitOpen.result" /></a-descriptions-item>
        <a-descriptions-item v-if="hitOpen.alertId" label="关联动作">
          <template v-if="hitLinked">
            <span class="num">{{ hitLinked.id }}</span> {{ hitLinked.action }} ·
            <span v-if="hitLinked.status === '结果未知'" class="tl-attention"><QuestionCircleOutlined /> 结果未知 · 待对账</span>
            <span v-else :class="hitLinked.status === '失败' ? 'c-error' : hitLinked.status === '待确认' ? 'c-warning' : 'c-3'">{{ hitLinked.status }}</span>
          </template>
          <span v-else class="c-3">无</span>
        </a-descriptions-item>
      </a-descriptions>
      <div class="ru-sec t-14 fw-600">处理记录</div>
      <StatusTimeline :items="hitTimeline" />
    </template>
  </a-drawer>
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTarget" @ok="onChangeOk" />
  <a-modal v-model:open="newOpen" title="新建规则" ok-text="下一步：选择变更单" @ok="onNewOk">
    <a-form layout="vertical">
      <a-form-item label="对象类型" required><a-select v-model:value="newForm.objKind" :options="objKindOptions" show-search option-filter-prop="label" /></a-form-item>
      <a-form-item label="规则名称"><a-input v-model:value="newForm.name" placeholder="未命名规则" :maxlength="60" /></a-form-item>
    </a-form>
  </a-modal>
</template>

<style scoped>
.ru { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.ru-list { width: 300px; flex: none; }
.ru-filter { padding: 12px 12px 8px; display: flex; flex-wrap: wrap; gap: 8px; flex: none; }
.ru-kw { flex: 1 1 100%; min-width: 0; }
.ru-sel { flex: 1; min-width: 0; }
.ru-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; }
.ru-items li { padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.ru-items li:hover { background: var(--g3); }
.ru-items li.active { background: var(--brand-bg); }
.ru-items li.active .ru-name { color: var(--brand); }
.ru-name { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ru-meta { flex: none; }
.ru-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.ru-pager { flex: 1; min-height: 0; }
.ru-foot { padding: 8px 12px 8px 16px; border-top: var(--border); flex: none; display: flex; align-items: center; }
.ru-detail { flex: 1; min-width: 0; }
.ru-head { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; flex: none; }
.ru-sp { flex: 1; }
.ru-tabs { padding: 0 16px; flex: none; }
.ru-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.ru-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
.ru-sec { margin-top: 4px; }
.ru-sec-row { display: flex; justify-content: space-between; align-items: center; }
.ru-tool { display: flex; align-items: center; gap: 8px; flex: none; }
.ru-remind { display: flex; align-items: center; gap: 12px; padding: 6px 12px; border: var(--border); border-radius: var(--radius); background: var(--warning-bg); flex: none; }
.ru-old { text-decoration: line-through; }
.ru-diff { margin-left: 8px; }
.ru-expr-old { text-decoration: line-through; margin-bottom: -4px; }
.ru-expr { margin: 0; padding: 8px 12px; background: var(--g2); border: var(--border); border-radius: var(--radius); white-space: pre-wrap; }
.ru-th { width: 200px; }
.ru-gap { margin-left: 8px; }
.ru-act { flex-direction: row; gap: 16px; }
.ru-act-left { flex: 1; min-width: 0; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.ru-act-right { width: 420px; flex: none; display: flex; flex-direction: column; gap: 6px; padding-left: 16px; border-left: var(--border); }
.ru-form { display: grid; grid-template-columns: auto 1fr auto 1fr; gap: 8px; align-items: center; }
.ru-mapsel { width: 100%; }
.ru-st { display: grid; grid-template-columns: 150px 1fr auto; gap: 8px; align-items: center; padding: 5px 0; border-bottom: var(--border); }
.ru-st-o { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ru-stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; flex: none; }
.ru-stats > div { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; white-space: nowrap; overflow: hidden; padding: 8px 12px; border: var(--border); border-radius: var(--radius); }
.ru-empty { margin-top: 48px; }
.ru-dis { color: var(--g7); cursor: not-allowed; }
</style>
