<script setup lang="ts">
/**
 * 对象类型：左侧对象类型列表（简洁分页），右侧详情按 v5 分三组页签（定义 / 行为 / 使用与治理）。
 * 选中类型写进 ?type=，页签写进 ?tab=。属性、概览、映射都按对象类型各自取数（src/mock/objectProps.ts 的 OBJECT_PROPS / objectMeta，
 * 负责人、数据源、数据质量取 src/mock/quality.ts）。状态从变更单推：审核中 → 橙色；只有草稿 → 灰色“草稿中”；其余灰色。
 */
import { computed, ref, watch, type Component } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import GroupedTabs from '@/components/GroupedTabs.vue';
import HoverCard from '@/components/HoverCard.vue';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import { useDraftView, draftOf } from './parts/useDraftView';
import { message } from 'ant-design-vue';
import { LINKS as ALL_LINKS, LINK_OBJECTS, PATH_GROUPS, defaultLabel, objName } from '@/mock/links';
import { OBJECT_PROPS, draftPropDiff, objectMeta, dbOf, type ObjProp } from '@/mock/objectProps';
import { qObjType, QUALITY_CHECKS } from '@/mock/quality';
import { PERM_OBJECTS } from '@/mock/roles';
import { RULE_DEFS } from '@/mock/rules';
import { legacyRevs } from '@/mock/objectHistory';
import MetricsTab from './parts/object/MetricsTab.vue';
import ViewsTab from './parts/object/ViewsTab.vue';
import PermissionsTab from './parts/object/PermissionsTab.vue';
import HistoryTab from './parts/object/HistoryTab.vue';
import AppPagination from '@/components/AppPagination.vue';
import { crsOf, metricsOf } from './parts/object/objectGov';

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
  { key: 'lifecycle', label: '生命周期', phase: 'behavior' },
  { key: 'actions', label: '动作', phase: 'behavior' },
  { key: 'automation', label: '自动化', phase: 'behavior' },
  { key: 'metrics', label: '指标', phase: 'governance', component: MetricsTab },
  { key: 'views', label: '视图', phase: 'governance', component: ViewsTab },
  { key: 'permissions', label: '权限', phase: 'governance', component: PermissionsTab },
  { key: 'history', label: '版本历史', phase: 'governance', component: HistoryTab },
];
const PHASES = [{ key: 'def', title: '定义' }, { key: 'behavior', title: '行为' }, { key: 'governance', title: '使用与治理' }] as const;
const TAB_GROUPS = PHASES.map((g) => ({ key: g.key, title: g.title, tabs: TABS.filter((t) => t.phase === g.key).map((t) => ({ key: t.key, title: t.label })) }))
  .filter((g) => g.tabs.length);

const TYPES = [
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

const route = useRoute();
const router = useRouter();
const selected = computed(() => (route.query.type as string) || 'order');
const tab = computed({
  get: () => (route.query.tab as string) || 'overview',
  set: (v) => router.replace({ query: { ...route.query, tab: v, p: undefined } }),
});
const curTab = computed(() => TABS.find((t) => t.key === tab.value));
const tabTitle = computed(() => curTab.value?.label);
const current = computed(() => TYPES.find((t) => t.id === selected.value) ?? TYPES[0]);
/** 显示状态：未发布的新对象类型按自身状态；有审核中的变更单 → 审核中（橙）；只有草稿 → 草稿中（灰）；其余已发布（灰） */
const shownStatus = (t: (typeof TYPES)[number]) => {
  if (t.status !== '已发布') return t.status;
  if (crsOf(t.id).some((u) => u.cr.status === '审核中')) return '审核中';
  return draftOf('object', t.id) ? '草稿中' : '已发布';
};
const statusColor = (t: (typeof TYPES)[number]) => (shownStatus(t) === '审核中' ? 'warning' : 'default');
const pick = (id: string) => router.replace({ query: { ...route.query, type: id, view: undefined, p: undefined, lcpage: undefined, acpage: undefined, aupage: undefined } });
const { draft, isDraft, canEdit, editTip, readonly, reason, setView, start } = useDraftView('object', () => current.value.id);
const changeOpen = ref(false);
const changeTarget = ref('');
const openChange = (name: string) => { changeTarget.value = name; changeOpen.value = true; };

// 左侧列表：搜索 + 简洁分页
const kw = ref('');
const LPAGE = 14;
const lpage = ref(1);
watch(kw, () => { lpage.value = 1; });
const filtered = computed(() => TYPES.filter((t) => !kw.value || t.name.includes(kw.value) || t.api.toLowerCase().includes(kw.value.toLowerCase())));
const list = computed(() => filtered.value.slice((lpage.value - 1) * LPAGE, lpage.value * LPAGE));

// —— 属性（每个对象类型各一份，src/mock/objectProps.ts）——
type P = ObjProp;
const PROPS = computed<P[]>(() => OBJECT_PROPS[current.value.id] ?? []);
/** 草稿视图里的差异：按草稿单号从 changes.ts 的 diffs 取（门店 CR-0009、会员 CR-0012） */
const draftDiff = computed(() => (draft.value ? draftPropDiff(draft.value.id, current.value.name, PROPS.value) : { map: {}, other: 0 }));
const DIFF = computed(() => (isDraft.value ? draftDiff.value.map : {}));
const pkw = ref('');
const ppage = ref(1);
const PPAGE = 12;
watch(pkw, () => { ppage.value = 1; });
watch(() => [current.value.id, isDraft.value], () => { ppage.value = 1; mpage.value = 1; });
// 草稿视图里有改动的属性排在最前，第一页就能看到
const srcProps = computed(() => [...PROPS.value].sort((x, y) => Number(!!DIFF.value[y.api]) - Number(!!DIFF.value[x.api])));
const pfiltered = computed(() => srcProps.value.filter((p) => !pkw.value || p.name.includes(pkw.value) || p.api.includes(pkw.value)));
const prows = computed(() => pfiltered.value.slice((ppage.value - 1) * PPAGE, ppage.value * PPAGE));
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
const LINKS = computed(() => ALL_LINKS.filter((l) => l.from === current.value.id && l.status === '已发布').map((l) => ({
  name: l.name, target: objName(l.to), card: l.card, key: l.key, reverse: l.reverse, isDefault: defaultLabel(l.id) !== '需用户指明',
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
const MAP_ROWS = [
  { prop: '订单号', col: 'order_id', srcType: 'VARCHAR2(32)', transform: '' },
  { prop: '渠道', col: 'channel_code', srcType: 'CHAR(2)', transform: '字典映射：01→门店，02→小程序，03→外卖平台，04→社区团购' },
  { prop: '订单状态', col: 'status', srcType: 'NUMBER(2)', transform: '字典映射：10→待支付 … 50→已出库' },
  { prop: '出库时间', col: 'out_time', srcType: 'DATE', transform: '按 Asia/Shanghai 解释' },
  { prop: '应付金额（不含税）', col: 'total_amt_notax', srcType: 'NUMBER(14,2)', transform: '' },
  { prop: '收货电话', col: 'recv_mobile', srcType: 'VARCHAR2(20)', transform: '敏感字段：中间 4 位脱敏' },
  { prop: '是否测试单', col: 'test_flag', srcType: 'CHAR(1)', transform: "'Y' → 是，其余 → 否" },
];
const SRC_TYPE: Record<string, string> = { string: 'VARCHAR2(64)', enum: 'NUMBER(2)', timestamp: 'DATE', decimal: 'NUMBER(14,2)', integer: 'NUMBER(6)', boolean: 'CHAR(1)', json: 'CLOB' };
const OVERRIDE = Object.fromEntries(MAP_ROWS.map((r) => [r.prop, r]));
const srcTable = computed(() => LINK_OBJECTS.find((o) => o.id === current.value.id)?.table ?? '--');
const pkProp = computed(() => PROPS.value.find((p) => p.pk));
const MAP_ALL = computed(() => PROPS.value.map((p) => (current.value.id === 'order' && OVERRIDE[p.name]) || { prop: p.name, col: p.source, srcType: SRC_TYPE[p.type] ?? 'VARCHAR2(64)', transform: p.sensitive ? '敏感字段：脱敏显示' : '' }));
// —— 概览（按对象类型取数）——
const meta = computed(() => objectMeta(current.value.id, current.value.name, current.value.group));
const qo = computed(() => qObjType(current.value.id));
const titleProp = computed(() => PROPS.value.find((p) => p.title));
/** 当前版本 / 最近修改：最近一张已发布的变更单；没有就取迁移前最后一次修订 */
const lastRev = computed(() => {
  const cr = crsOf(current.value.id).find((u) => u.cr.release)?.cr;
  if (cr) return { ver: cr.release!, by: cr.owner, time: cr.updatedAt };
  const h = legacyRevs(current.value.id, current.value.name)[0];
  return { ver: h.version, by: h.author, time: h.time };
});
const usedMetrics = computed(() => metricsOf(current.value.id).map((u) => u.m));
const actionCount = computed(() => PERM_OBJECTS.find((o) => o.name === current.value.name)?.actions.length ?? 0);
const ruleCount = computed(() => RULE_DEFS.filter((r) => r.objKind === current.value.id).length);
const checks = computed(() => QUALITY_CHECKS.filter((c) => c.typeKey === current.value.id));
const fullSync = computed(() => meta.value.sync.includes('全量'));
const freshFail = computed(() => checks.value.find((c) => c.kind === '新鲜度' && c.result === '失败'));
const lastSync = computed(() => (fullSync.value ? '2026-10-25 02:00' : '2026-10-25 15:00'));
const mpage = ref(1);
const MPAGE = 12;
const mrows = computed(() => MAP_ALL.value.slice((mpage.value - 1) * MPAGE, mpage.value * MPAGE));
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-button :disabled="readonly" @click="!readonly && openChange('从数据源导入对象类型')">从数据源导入</a-button>
    <a-button :disabled="readonly" @click="!readonly && openChange('新建对象类型')">新建对象类型</a-button>
  </Teleport>

  <div class="ot">
    <div class="panel ot-list">
      <div class="ot-search"><a-input-search v-model:value="kw" placeholder="搜索名称或 API 名" /></div>
      <ul class="ot-items">
        <li v-for="t in list" :key="t.id" :class="{ active: t.id === current.id }" @click="pick(t.id)">
          <div class="ot-row">
            <span class="t-14 ot-name">{{ t.name }}</span>
            <span class="t-12 num" :class="shownStatus(t) === '审核中' ? 'c-warning' : 'c-3'">{{ shownStatus(t) === '已发布' ? '' : shownStatus(t) }}</span>
          </div>
          <div class="ot-row">
            <span class="t-12 c-3 num">{{ t.api }}</span>
            <span class="t-12 c-3">{{ t.group }}</span>
          </div>
        </li>
      </ul>
      <div class="ot-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="filtered.length" :size="LPAGE" unit="条" />
      </div>
    </div>

    <div class="panel ot-detail">
      <DraftBanner v-if="isDraft && draft" :draft="draft"
        @submit="message.success(`${draft.id} 已提交审核，可在“变更与发布”查看`)" @discard="setView('published')" />
      <div class="ot-head">
        <span class="t-16 fw-600 c-1">{{ current.name }}</span>
        <span class="t-14 c-3 num">{{ current.api }} · {{ PROPS.length }} 个属性 · {{ current.count }} 条</span>
        <a-tag v-if="!(shownStatus(current) === '草稿中' && draft && !readonly)" :color="statusColor(current)">{{ shownStatus(current) }}</a-tag>
        <a-segmented v-if="draft && !readonly" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => setView(v)" />
        <span class="ot-sp" />
        <span class="t-12 c-3">数据截至 {{ qo?.cutoff ?? '--' }}</span>
        <a-button>预览数据</a-button>
        <a-tooltip v-if="readonly" :title="reason"><a-button disabled>发起变更</a-button></a-tooltip>
        <a-button v-else-if="!draft" type="primary" @click="openChange(`${current.name}（${current.api}）`)">发起变更</a-button>
        <a-button v-else-if="!isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
      </div>
      <GroupedTabs v-model="tab" :groups="TAB_GROUPS" />

      <!-- 配置了 component 的页签：统一传 objectId / readonly -->
      <component :is="curTab.component" v-if="curTab?.component" :key="`${current.id}:${curTab.key}`" :object-id="current.id" :readonly="!canEdit" />

      <!-- 概览 -->
      <div v-else-if="tab === 'overview'" class="ot-body ot-ov">
        <div class="ot-ov-left">
          <a-descriptions :column="2" bordered size="small">
            <a-descriptions-item label="显示名">{{ current.name }}</a-descriptions-item>
            <a-descriptions-item label="API 名"><span class="num">{{ current.api }}</span></a-descriptions-item>
            <a-descriptions-item label="业务域">{{ meta.domain }}</a-descriptions-item>
            <a-descriptions-item label="负责人">{{ qo?.owner ?? '--' }}</a-descriptions-item>
            <a-descriptions-item label="主键">{{ pkProp ? `${pkProp.name}（${pkProp.api}）` : '--' }}</a-descriptions-item>
            <a-descriptions-item label="标题属性">{{ titleProp?.name ?? '--' }}</a-descriptions-item>
            <a-descriptions-item label="数据源">{{ qo?.source ?? '--' }}（{{ dbOf(qo?.source) }}）</a-descriptions-item>
            <a-descriptions-item label="同步方式">{{ meta.sync }}</a-descriptions-item>
            <a-descriptions-item label="冲突策略">{{ meta.conflict }}</a-descriptions-item>
            <a-descriptions-item label="生命周期">{{ meta.lifecycle }}</a-descriptions-item>
            <a-descriptions-item label="当前版本">随本体发布 <span class="num">{{ lastRev.ver }}</span> 生效</a-descriptions-item>
            <a-descriptions-item label="最近修改">{{ lastRev.by }} · <span class="num">{{ lastRev.time }}</span></a-descriptions-item>
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
          <div class="t-14 fw-600">使用情况</div>
          <dl class="ot-stat">
            <dt class="t-14 c-3">关系</dt><dd class="t-14 num">{{ LINKS.length }} 条</dd>
            <dt class="t-14 c-3">引用的指标</dt><dd class="t-14 num">
              <template v-if="usedMetrics.length">
                <template v-for="(m, i) in usedMetrics.slice(0, 2)" :key="m.id"><template v-if="i">、</template><HoverCard kind="metric" :id="m.id">{{ m.name }}</HoverCard></template>
                <template v-if="usedMetrics.length > 2"> 等 {{ usedMetrics.length }} 个</template>
              </template>
              <span v-else class="c-3">无</span></dd>
            <dt class="t-14 c-3">动作类型</dt><dd class="t-14 num">{{ actionCount }} 个<span v-if="actionCount" class="c-3">（第三期启用）</span></dd>
            <dt class="t-14 c-3">规则</dt><dd class="t-14 num">{{ ruleCount }} 条</dd>
            <dt class="t-14 c-3">看板卡片</dt><dd class="t-14 num">{{ meta.cards }} 张</dd>
            <dt class="t-14 c-3">近 30 天问数</dt><dd class="t-14 num">{{ meta.qa30.toLocaleString('zh-CN') }} 次</dd>
          </dl>
          <div class="t-14 fw-600">数据质量</div>
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
        <div class="ot-tool">
          <a-input-search v-model:value="pkw" placeholder="搜索属性名或 API 名" class="ot-tool-search" />
          <span class="t-12 c-3">主键 1 · 维度 {{ srcProps.filter((p) => p.dim).length }} · 敏感 {{ srcProps.filter((p) => p.sensitive).length }}</span>
          <span v-if="isDraft && draft" class="t-12 c-3">草稿 {{ draft.id }}：属性改动 {{ Object.keys(DIFF).length }} 处<template v-if="draftDiff.other">，其余 {{ draftDiff.other }} 处（对象类型、关系、映射、指标等）见<router-link :to="`/build/ontology/releases?cr=${draft.id}`">变更单</router-link></template></span>
          <span class="ot-sp" />
          <a-tooltip :title="editTip"><a-button :disabled="!canEdit">批量编辑</a-button></a-tooltip>
          <a-tooltip :title="editTip"><a-button :type="canEdit ? 'primary' : 'default'" :disabled="!canEdit">新增属性</a-button></a-tooltip>
        </div>
        <a-table :columns="propCols" :data-source="prows" :pagination="false" size="small" row-key="api">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <a>{{ record.name }}</a>
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
                <a-tag v-if="record.sensitive" color="warning">敏感</a-tag>
              </span>
            </template>
          </template>
        </a-table>
        <div class="ot-pager">
          <AppPagination v-model:page="ppage" variant="simple" :total="pfiltered.length" :size="PPAGE" unit="条" />
        </div>
      </div>

      <!-- 关系 -->
      <div v-else-if="tab === 'links'" class="ot-body">
        <div class="ot-tool">
          <span class="t-12 c-3">{{ linkTip }}</span>
          <span class="ot-sp" />
          <a-tooltip :title="editTip"><a-button type="primary" :disabled="!canEdit">新增关系</a-button></a-tooltip>
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
        <a-descriptions :column="4" bordered size="small">
          <a-descriptions-item label="数据源">{{ qo?.source ?? '--' }}</a-descriptions-item>
          <a-descriptions-item label="数据库">{{ dbOf(qo?.source) }}</a-descriptions-item>
          <a-descriptions-item label="源表">{{ srcTable }}</a-descriptions-item>
          <a-descriptions-item label="主键映射">{{ pkProp ? `${pkProp.source} → ${pkProp.name}` : '--' }}</a-descriptions-item>
          <a-descriptions-item label="同步方式">{{ meta.sync }}</a-descriptions-item>
          <a-descriptions-item label="增量字段">{{ fullSync ? '--（全量同步）' : 'update_time' }}</a-descriptions-item>
          <a-descriptions-item label="最近同步">
            <span v-if="freshFail" class="c-error">{{ lastSync }} · 延迟（{{ freshFail.actual }}）</span>
            <span v-else class="num">{{ lastSync }} · 成功</span>
          </a-descriptions-item>
          <a-descriptions-item label="数据截至"><span class="num">{{ qo?.cutoff ?? '--' }}</span></a-descriptions-item>
        </a-descriptions>
        <div class="ot-tool">
          <span class="t-14 fw-600">字段映射</span>
          <span class="t-12 c-3">已映射 {{ MAP_ALL.length }} / 源表 {{ MAP_ALL.length + meta.unused }} 个字段，{{ meta.unused }} 个未使用</span>
          <span class="ot-sp" />
          <a-button>重新采集元数据</a-button>
        </div>
        <a-table :columns="MAP_COLS" :data-source="mrows" size="small" row-key="prop" :pagination="false" />
        <div class="ot-pager">
          <AppPagination v-model:page="mpage" variant="simple" :total="MAP_ALL.length" :size="MPAGE" unit="条" />
        </div>
      </div>

      <div v-else class="ot-body ot-empty">
        <div class="t-14 c-3">{{ tabTitle }} · 待设计</div>
      </div>
    </div>
  </div>
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTarget" @ok="start" />
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
