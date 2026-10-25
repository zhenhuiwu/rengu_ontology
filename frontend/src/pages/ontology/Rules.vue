<script setup lang="ts">
/**
 * 规则与自动化：左侧规则列表，右侧详情。页签：条件 / 触发方式 / 动作 / 命中历史 / 回放试跑 / 版本历史。
 * URL：?rule= 选中项，?tab= 页签，?view=draft 草稿，?ver= 历史版本只读；
 *      命中历史 ?hrange= + ?hpage/hsize，命中详情抽屉 ?hit=；回放试跑 ?rrange= ?rver= ?run=1 + ?rpage/rsize。
 * 已发布版本只读，改规则只能“发起变更”进草稿。自动暂存动作只会把动作放进动作中心“待确认”，从不自动执行。
 */
import { computed, reactive, ref, watch } from 'vue';
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
import { metricById } from '@/mock/metrics';
import { ACTIONS } from '@/mock/actions';
import { ACTION_TYPES, actionTypeById, eventTypeById, paramsOf } from '@/mock/actionEvent';
import {
  RULE_DEFS, RULE_DRAFTS, CONFIRMERS, MAPPING_SOURCES, hitsOf, stagedOf, replay, ruleVersions, objKindLabel, dedupeReason,
  type RuleDef, type AutoStage, type Hit,
} from '@/mock/rules';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import { useDraftView } from './parts/useDraftView';

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

/* 左侧列表 */
const kw = ref('');
const trig = ref('全部');
const filtered = computed(() => RULE_DEFS.filter((r) => (trig.value === '全部' || r.trigger.kind === trig.value)
  && (!kw.value || r.name.includes(kw.value) || r.id.toLowerCase().includes(kw.value.toLowerCase()))));
const LPAGE = 12;
const lpage = ref(1);
watch([kw, trig], () => { lpage.value = 1; });
const list = computed(() => paginate(filtered.value, { page: lpage.value, size: LPAGE }).items);
const base = computed<RuleDef>(() => RULE_DEFS.find((r) => r.id === route.query.rule) ?? RULE_DEFS[0]);
// 切换规则时清掉与上一条规则相关的状态，包括抽屉（drawer / drawer2 / hit）
const RESET = { view: undefined, hpage: undefined, hsize: undefined, hrange: undefined, hit: undefined, rpage: undefined, rsize: undefined,
  rrange: undefined, rver: undefined, run: undefined, drawer: undefined, drawer2: undefined };
const pick = (id: string) => router.replace({ query: { ...route.query, ...RESET, rule: id } });

const TABS = ['cond', 'trigger', 'action', 'hits', 'replay', 'versions'];
const tab = computed({
  get: () => (TABS.includes(q('tab')) ? q('tab') : 'cond'),
  set: (v) => router.replace({ query: { ...route.query, ...RESET, view: route.query.view, tab: v === 'cond' ? undefined : v } }),
});

/* 草稿：公共 useDraftView('rule')，变更单信息取自变更单本身（R-007 在 CR-0011）；本地只保留规则草稿的改动内容 */
const { draft: draftCR, isDraft, editTip, setView, start } = useDraftView('rule', () => base.value.id);
const changeOpen = ref(false);
const changeTarget = ref('');
const openChange = (name: string) => { changeTarget.value = name; changeOpen.value = true; };
/** 草稿里的改动（自动暂存配置、阈值） */
const edits = reactive<Record<string, { autoStage: AutoStage; thresholds: string[] }>>({});
const draftDef = computed<RuleDef | undefined>(() => {
  if (!draftCR.value) return undefined;
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
function saveDraft() {
  const cr = draftCR.value!;
  cr.changes += 1; cr.updatedAt = nowText();
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
const stageEdit = computed(() => (isDraft.value ? ensureEdits().autoStage : rule.value.autoStage));
const stageType = computed(() => (stageEdit.value.actionTypeId ? actionTypeById(stageEdit.value.actionTypeId) : undefined));
const actOptions = computed(() => ACTION_TYPES.filter((a) => a.targetKind === rule.value.objKind && a.status !== '草稿').map((a) => ({ value: a.id, label: `${a.name} · ${a.version}` })));
const params = computed(() => (stageType.value ? paramsOf(stageType.value) : []));
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
const staged = computed(() => stagedOf(base.value.id));
const stagedPending = computed(() => staged.value.filter((a) => a.status === '待确认').length);

/* 命中历史 */
const HRANGES = [{ label: '近 7 天', value: '7d' }, { label: '近 30 天', value: '30d' }];
const hrange = computed({ get: () => (q('hrange') === '30d' ? '30d' : '7d'), set: (v) => setQ({ hrange: v === '7d' ? undefined : v, hpage: undefined }) });
const hq = usePagedQuery({ defaultSize: 12, prefix: 'h' });
const hits7 = computed(() => hitsOf(base.value.id).filter((h) => h.at > '2026-10-18 23:59:59').length);
const hitsAll = computed(() => hitsOf(base.value.id).filter((h) => h.at > (hrange.value === '7d' ? '2026-10-18 23:59:59' : '2026-09-25')));
const hits = computed(() => paginate(hitsAll.value, { page: hq.page.value, size: hq.size.value }));
/** 商品类规则（R-007 / R-012）去重键含门店，命中与回放样本单列门店 */
const STORE_COL = { title: '门店', key: 'store', width: 220, ellipsis: true };
const hitCols = computed(() => [
  { title: '命中时间', dataIndex: 'at', width: 172 },
  { title: '告警 / 命中编号', key: 'id', width: 200 },
  { title: base.value.objKind === 'product' ? '商品' : '对象', key: 'obj', ellipsis: true },
  ...(base.value.objKind === 'product' ? [STORE_COL] : []),
  { title: '命中值', dataIndex: 'value', width: 200, ellipsis: { showTitle: true } },
  { title: '阈值', dataIndex: 'threshold', width: 160, ellipsis: { showTitle: true } },
  { title: '处理状态', key: 'result', width: 110 },
]);
const hitOpen = computed(() => (q('hit') ? hitsOf(base.value.id).find((h) => h.id === q('hit')) : undefined));
const openHit = (h: Hit) => setQ({ hit: h.id });
const hitLinked = computed(() => (hitOpen.value?.alertId ? ACTIONS.find((a) => a.source.alertId === hitOpen.value!.alertId) : undefined));
const hitTimeline = computed(() => {
  const h = hitOpen.value; if (!h) return [];
  if (h.alert) return alertTimeline(h.alert).map((t) => ({ time: t.time, text: t.text, desc: t.who, status: t.abnormal ? 'warn' : 'done' }));
  return [{ time: h.at, text: `规则 ${h.ruleId} v${rule.value.version} 命中`, desc: '系统', status: 'done' },
    { time: h.at, text: h.result === '已去重' ? `去重键相同，冷却期（${rule.value.cooldown}）内不再通知` : `已通知负责人 ${rule.value.owner}`, desc: '系统', status: 'done' }];
});
const resultBadge = (s: string) => (s === '待处理' ? 'warning' : 'default');

/* 回放试跑 */
const rrange = computed({ get: () => (q('rrange') === '30d' ? '30d' : '7d'), set: (v) => setQ({ rrange: v === '7d' ? undefined : v, run: undefined, rpage: undefined }) });
const rver = computed({ get: () => (q('rver') === 'draft' && draftDef.value ? 'draft' : 'cur'), set: (v) => setQ({ rver: v === 'cur' ? undefined : v, run: undefined, rpage: undefined }) });
const VER_OPTS = computed(() => [{ label: `当前 v${base.value.version}`, value: 'cur' }, ...(draftDef.value ? [{ label: `草稿 v${draftDef.value.version}`, value: 'draft' }] : [])]);
const ran = computed(() => q('run') === '1');
const running = ref(false);
function runReplay() { running.value = true; setTimeout(() => { running.value = false; setQ({ run: '1', rpage: undefined }); }, 400); }
const result = computed(() => (ran.value ? replay(base.value, rrange.value as '7d' | '30d', rver.value === 'draft') : null));
const rq = usePagedQuery({ defaultSize: 9, prefix: 'r' });
const samples = computed(() => paginate(result.value?.samples ?? [], { page: rq.page.value, size: rq.size.value }));
const dedupeText = computed(() => dedupeReason(base.value));
const sampleCols = computed(() => [
  { title: '时间', dataIndex: 'at', width: 172 },
  { title: base.value.objKind === 'product' ? '商品' : '对象', key: 'obj', ellipsis: true },
  ...(base.value.objKind === 'product' ? [STORE_COL] : []),
  { title: '命中值', dataIndex: 'value', width: 220, ellipsis: { showTitle: true } },
  { title: '与实际命中对比', key: 'diff', width: 300, ellipsis: true },
]);

/* 版本历史 */
const versions = computed(() => ruleVersions(base.value, draftDef.value?.version));
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
    <a-tooltip :title="reason"><a-button :disabled="readonly" @click="openChange('新建规则')">新建规则</a-button></a-tooltip>
  </Teleport>

  <div class="ru">
    <div class="panel ru-list">
      <div class="ru-filter">
        <a-input-search v-model:value="kw" placeholder="搜索名称" class="ru-kw" />
        <a-select v-model:value="trig" :options="['全部', '定时', '事件'].map((g) => ({ value: g, label: g === '全部' ? '全部触发' : g }))" class="ru-sel" />
      </div>
      <ul class="ru-items">
        <li v-for="r in list" :key="r.id" :class="{ active: r.id === base.id }" @click="pick(r.id)">
          <div class="ru-row">
            <span class="t-14 ru-name">{{ r.name }}</span>
            <span class="t-12 num c-3">{{ r.status === '已发布' ? '' : r.status + ' · ' }}v{{ r.version }}</span>
          </div>
          <div class="ru-row">
            <span class="t-12 c-3 num">{{ r.id }}</span>
            <span class="t-12 c-3">{{ r.trigger.kind }} · {{ objKindLabel(r.objKind) }} · {{ r.output }}</span>
          </div>
        </li>
      </ul>
      <div class="ru-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="filtered.length" :size="LPAGE" unit="条规则" class="ru-pager" empty-text="没有匹配的规则" />
      </div>
    </div>

    <div class="panel ru-detail">
      <DraftBanner v-if="isDraft && draftCR" :draft="draftCR"
        @submit="message.success(`${draftCR.id} 已提交审核，可在“变更与发布”查看`)" @discard="setView('published')" />
      <div class="ru-head">
        <span class="t-16 fw-600 c-1">{{ rule.name }}</span>
        <span class="t-14 c-3 num">{{ rule.id }}</span>
        <span class="t-14 c-3 num">{{ isDraft ? `草稿 v${rule.version}（已发布 v${base.version}）` : `v${rule.version}` }}</span>
        <a-badge status="default" :text="rule.status" />
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
          <span class="t-12 c-3">以下条件同时满足（{{ rule.logic }}）时命中；指标引用的是本体里发布的口径，口径升版不会自动跟随</span>
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
      </div>

      <!-- 触发方式 -->
      <div v-else-if="tab === 'trigger'" class="ru-body">
        <div class="ru-tool">
          <a-segmented :value="rule.trigger.kind" :options="['定时', '事件']" size="small" :disabled="!isDraft" />
          <span class="t-12 c-3">{{ rule.trigger.kind === '定时' ? '按计划时间批量计算' : '收到事件后逐条计算，延迟秒级' }}</span>
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
        <div class="t-12 c-3">规则版本发布后才按新的触发方式运行；草稿里的修改只在回放试跑中生效。</div>
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
            <span>自动暂存动作</span>
            <a-tooltip :title="editTip">
              <a-switch :checked="stageEdit.enabled" :disabled="!isDraft" size="small" @change="(v: any) => (ensureEdits().autoStage.enabled = !!v)" />
            </a-tooltip>
          </div>
          <div class="t-12 c-3">命中后按下面的映射生成动作，放进动作中心“待确认”，由确认人逐条确认后才提交外部系统；规则不会直接执行动作。</div>
          <div class="ru-form">
            <span class="t-12 c-3">动作类型</span>
            <a-select :value="stageEdit.actionTypeId || undefined" :options="actOptions" :disabled="!isDraft || !stageEdit.enabled" placeholder="选择动作类型" size="small" @change="(v: any) => onStageType(v)" />
            <span class="t-12 c-3">确认人</span>
            <a-select :value="stageEdit.confirmer || undefined" :options="CONFIRMERS.map((c) => ({ value: c, label: c }))" :disabled="!isDraft || !stageEdit.enabled" placeholder="选择确认人" size="small"
              @change="(v: any) => (ensureEdits().autoStage.confirmer = v)" />
          </div>
          <a-table :columns="mapCols" :data-source="mappingRows" :pagination="false" size="small" row-key="param" class="ru-map"
            :locale="{ emptyText: '先选择动作类型' }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'param'">{{ record.param }}<span v-if="record.required" class="c-error"> *</span></template>
              <template v-else-if="column.key === 'from'">
                <a-select v-if="isDraft && stageEdit.enabled" size="small" :value="record.from || undefined" placeholder="选择取值来源" class="ru-mapsel" allow-clear
                  :options="[...new Set([record.from, ...MAPPING_SOURCES].filter(Boolean))].map((m) => ({ value: m, label: m }))" @change="(v: any) => setMapping(record.param, v ?? '')" />
                <span v-else :class="record.from ? 'c-1' : 'c-3'">{{ record.from || '未映射' }}</span>
              </template>
            </template>
          </a-table>
        </div>
        <div class="ru-act-right">
          <div class="ru-sec t-14 fw-600 ru-sec-row"><span>已暂存的动作</span><span class="t-12 c-3 fw-400">待确认 {{ stagedPending }} 条 · 共 {{ staged.length }} 条</span></div>
          <div v-for="a in staged.slice(0, 9)" :key="a.id" class="ru-st">
            <span class="t-12 num c-2">{{ a.id }}</span>
            <span class="t-12 c-3 ru-st-o">{{ a.objLabel }}</span>
            <span v-if="a.status === '结果未知'" class="t-12 tl-attention"><QuestionCircleOutlined /> 结果未知 · 待对账</span>
            <a-badge v-else :status="a.status === '待确认' ? 'warning' : a.status === '失败' ? 'error' : 'default'" :text="a.status" />
          </div>
          <div v-if="!staged.length" class="t-12 c-3">暂无。开启后命中生成的动作会出现在这里和动作中心“待确认”。</div>
          <div class="t-12 c-3">在左侧导航“运行 · 动作中心”的“待确认”里确认或撤回。</div>
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
              <template v-else-if="column.key === 'store'"><ObjRef v-if="record.storeId" kind="store" :id="record.storeId">{{ record.storeLabel }}</ObjRef><span v-else class="c-3">--</span></template>
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
          <span class="t-12 c-3">时间范围</span>
          <a-segmented v-model:value="rrange" :options="HRANGES" size="small" />
          <span class="t-12 c-3">规则版本</span>
          <a-segmented v-model:value="rver" :options="VER_OPTS" size="small" />
          <a-tooltip :title="reason"><a-button size="small" :loading="running" :disabled="readonly" @click="runReplay">开始试跑</a-button></a-tooltip>
          <span class="ru-sp" />
          <span class="t-12 c-3">用历史数据重新计算，不生成告警、不暂存动作</span>
        </div>
        <template v-if="result">
          <div class="ru-stats">
            <div><div class="t-12 c-3">实际命中（v{{ base.version }}）</div><div class="t-20 fw-600 c-1 num">{{ result.actual }}</div></div>
            <div><div class="t-12 c-3">试跑会命中（{{ rver === 'draft' ? `草稿 v${draftDef?.version}` : `当前 v${base.version}` }}）</div><div class="t-20 fw-600 c-1 num">{{ result.would }}</div></div>
            <div><div class="t-12 c-3">新增命中</div><div class="t-20 fw-600 num c-1">{{ result.added }}</div></div>
            <div><div class="t-12 c-3">不再命中</div><div class="t-20 fw-600 num c-1">{{ result.removed }}</div></div>
          </div>
          <div class="t-12 c-3">命中数只算会生成告警 / 通知的命中；另有 <span class="num">{{ result.deduped }}</span> 个样本去重键相同、在冷却期（{{ base.cooldown }}）内，列为“被去重”，不计入</div>
          <div class="paged-scroll">
            <a-table :columns="sampleCols" :data-source="samples.items" :pagination="false" size="middle" row-key="key" :locale="{ emptyText: '该时间范围内没有命中样本' }">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'obj'"><ObjRef :kind="record.objKind" :id="record.objId">{{ record.objLabel }}</ObjRef></template>
                <template v-else-if="column.key === 'store'"><ObjRef v-if="record.storeId" kind="store" :id="record.storeId">{{ record.storeLabel }}</ObjRef><span v-else class="c-3">--</span></template>
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
        <a-table :columns="verCols" :data-source="versions" :pagination="false" size="middle" row-key="ver">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'ver'"><span class="num">v{{ record.ver }}</span></template>
            <template v-else-if="column.key === 'status'"><a-badge :status="record.status === '草稿' ? 'warning' : 'default'" :text="record.status" /></template>
            <template v-else-if="column.key === 'op'">
              <a-space><a>对比</a><a-tooltip v-if="record.status === '已替代'" :title="reason"><a :class="{ 'ru-dis': readonly }">回滚</a></a-tooltip></a-space>
            </template>
          </template>
        </a-table>
        <div class="t-12 c-3">规则版本随本体整版发布；回滚会生成一张新的变更单，审核通过后生效。</div>
      </div>
    </div>
  </div>

  <a-drawer :open="!!hitOpen" :width="520" :title="hitOpen ? (hitOpen.alertId ? `告警 ${hitOpen.id}` : `命中 ${hitOpen.id}`) : ''" @close="setQ({ hit: undefined })">
    <template v-if="hitOpen">
      <a-descriptions :column="1" bordered size="small">
        <a-descriptions-item label="规则">{{ base.id }} {{ base.name }} · v{{ base.version }}</a-descriptions-item>
        <a-descriptions-item label="对象"><ObjRef :kind="hitOpen.objKind" :id="hitOpen.objId">{{ hitOpen.objLabel }}</ObjRef></a-descriptions-item>
        <a-descriptions-item v-if="hitOpen.storeId" label="门店"><ObjRef kind="store" :id="hitOpen.storeId">{{ hitOpen.storeLabel }}</ObjRef></a-descriptions-item>
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
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTarget" @ok="start" />
</template>

<style scoped>
.ru { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.ru-list { width: 300px; flex: none; }
.ru-filter { padding: 12px 12px 8px; display: flex; gap: 8px; flex: none; }
.ru-kw { flex: 1; min-width: 0; }
.ru-sel { width: 108px; flex: none; }
.ru-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; }
.ru-items li { padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.ru-items li:hover { background: var(--g3); }
.ru-items li.active { background: var(--brand-bg); }
.ru-items li.active .ru-name { color: var(--brand); }
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
.ru-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; flex: none; }
.ru-stats > div { padding: 8px 12px; border: var(--border); border-radius: var(--radius); }
.ru-empty { margin-top: 48px; }
.ru-dis { color: var(--g7); cursor: not-allowed; }
</style>
