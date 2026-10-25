<script setup lang="ts">
/**
 * 对象类型：左侧对象类型列表（简洁分页），右侧详情按 v5 分三组页签（定义 / 行为 / 使用与治理）。
 * 选中类型写进 ?type=，页签写进 ?tab=。示例数据以“订单”为准写全，其余类型共用。
 */
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import GroupedTabs from '@/components/GroupedTabs.vue';
import HoverCard from '@/components/HoverCard.vue';
import { OBJECT_TYPE_TAB_GROUPS } from '@/nav';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import { useDraftView } from './parts/useDraftView';
import { message } from 'ant-design-vue';

const TYPES = [
  { id: 'order', name: '订单', api: 'SalesOrder', group: '交易', props: 31, count: '1,286,402', status: '已发布' },
  { id: 'order_line', name: '订单行', api: 'SalesOrderLine', group: '交易', props: 14, count: '4,917,355', status: '已发布' },
  { id: 'refund', name: '退款单', api: 'Refund', group: '交易', props: 16, count: '38,914', status: '已发布' },
  { id: 'refund_line', name: '退款行', api: 'RefundLine', group: '交易', props: 11, count: '52,307', status: '已发布' },
  { id: 'member', name: '会员', api: 'Member', group: '客户', props: 22, count: '863,027', status: '变更中' },
  { id: 'member_level', name: '会员等级', api: 'MemberLevel', group: '客户', props: 6, count: '5', status: '已发布' },
  { id: 'store', name: '门店', api: 'Store', group: '组织', props: 18, count: '486', status: '变更中' },
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
const tabTitle = computed(() => OBJECT_TYPE_TAB_GROUPS.flatMap((g) => g.tabs).find((t) => t.key === tab.value)?.title);
const current = computed(() => TYPES.find((t) => t.id === selected.value) ?? TYPES[0]);
const pick = (id: string) => router.replace({ query: { ...route.query, type: id, view: undefined } });
const { draft, isDraft, canEdit, editTip, readonly, reason, setView, start } = useDraftView('object', () => current.value.id);
const changeOpen = ref(false);
const changeTarget = ref('');
const openChange = (name: string) => { changeTarget.value = name; changeOpen.value = true; };
/** 草稿视图里的差异（示例：订单属性） */
const DIFF: Record<string, '新增' | '修改'> = { 渠道: '修改', 履约门店: '新增' };

// 左侧列表：搜索 + 简洁分页
const kw = ref('');
const LPAGE = 14;
const lpage = ref(1);
watch(kw, () => { lpage.value = 1; });
const filtered = computed(() => TYPES.filter((t) => !kw.value || t.name.includes(kw.value) || t.api.toLowerCase().includes(kw.value.toLowerCase())));
const list = computed(() => filtered.value.slice((lpage.value - 1) * LPAGE, lpage.value * LPAGE));

// —— 属性（订单，31 个）——
interface P { api: string; name: string; type: string; group: string; pk?: boolean; title?: boolean; dim?: boolean; sensitive?: boolean; enumName?: string; source: string; desc: string }
const PROPS: P[] = [
  { api: 'order_id', name: '订单号', type: 'string', group: '标识', pk: true, source: 'order_id', desc: '业务主键，全局唯一' },
  { api: 'order_no_display', name: '展示单号', type: 'string', group: '标识', title: true, source: 'order_no', desc: '小票和客服使用的单号' },
  { api: 'channel', name: '渠道', type: 'enum', group: '交易', dim: true, enumName: '销售渠道', source: 'channel_code', desc: '门店、小程序、外卖平台、社区团购' },
  { api: 'status', name: '订单状态', type: 'enum', group: '交易', dim: true, enumName: '订单状态', source: 'status', desc: '待支付、待出库、拣货中、已出库、已签收、已取消' },
  { api: 'store_id', name: '门店', type: 'string', group: '交易', dim: true, source: 'store_code', desc: '外键 → 门店' },
  { api: 'member_id', name: '会员', type: 'string', group: '交易', source: 'member_id', desc: '外键 → 会员；散客为空' },
  { api: 'warehouse_id', name: '发货仓', type: 'string', group: '交易', dim: true, source: 'wh_code', desc: '外键 → 仓库' },
  { api: 'created_at', name: '下单时间', type: 'timestamp', group: '时间', dim: true, source: 'create_time', desc: '' },
  { api: 'paid_at', name: '支付时间', type: 'timestamp', group: '时间', source: 'pay_time', desc: '' },
  { api: 'shipped_at', name: '出库时间', type: 'timestamp', group: '时间', dim: true, source: 'out_time', desc: '财务口径的时间字段' },
  { api: 'signed_at', name: '签收时间', type: 'timestamp', group: '时间', source: 'sign_time', desc: '' },
  { api: 'cancelled_at', name: '取消时间', type: 'timestamp', group: '时间', source: 'cancel_time', desc: '' },
  { api: 'amount_gross', name: '应付金额（含税）', type: 'decimal', group: '金额', source: 'total_amt', desc: '单位：元' },
  { api: 'amount_net', name: '应付金额（不含税）', type: 'decimal', group: '金额', source: 'total_amt_notax', desc: '单位：元' },
  { api: 'discount_amount', name: '优惠金额', type: 'decimal', group: '金额', source: 'discount_amt', desc: '单位：元' },
  { api: 'paid_amount', name: '实付金额', type: 'decimal', group: '金额', source: 'pay_amt', desc: '单位：元' },
  { api: 'freight_amount', name: '运费', type: 'decimal', group: '金额', source: 'freight_amt', desc: '单位：元' },
  { api: 'tax_amount', name: '税额', type: 'decimal', group: '金额', source: 'tax_amt', desc: '单位：元' },
  { api: 'pay_method', name: '支付方式', type: 'enum', group: '交易', dim: true, enumName: '支付方式', source: 'pay_type', desc: '' },
  { api: 'line_count', name: '商品行数', type: 'integer', group: '交易', source: 'item_cnt', desc: '' },
  { api: 'is_test', name: '是否测试单', type: 'boolean', group: '交易', source: 'test_flag', desc: '默认过滤掉' },
  { api: 'receiver_name', name: '收货人', type: 'string', group: '收货', sensitive: true, source: 'recv_name', desc: '脱敏显示' },
  { api: 'receiver_phone', name: '收货电话', type: 'string', group: '收货', sensitive: true, source: 'recv_mobile', desc: '脱敏显示' },
  { api: 'receiver_address', name: '收货地址', type: 'string', group: '收货', sensitive: true, source: 'recv_addr', desc: '脱敏显示' },
  { api: 'receiver_city', name: '收货城市', type: 'string', group: '收货', dim: true, source: 'recv_city', desc: '' },
  { api: 'delivery_type', name: '配送方式', type: 'enum', group: '收货', dim: true, enumName: '配送方式', source: 'deliver_type', desc: '自提、同城配送、快递' },
  { api: 'promotion_ids', name: '参与活动', type: 'json', group: '交易', source: 'promo_list', desc: '数组；一单可参与多个活动' },
  { api: 'coupon_code', name: '优惠券码', type: 'string', group: '交易', source: 'coupon_no', desc: '' },
  { api: 'remark', name: '买家备注', type: 'string', group: '其他', source: 'buyer_memo', desc: '' },
  { api: 'source_updated_at', name: '源数据更新时间', type: 'timestamp', group: '其他', source: 'update_time', desc: '冲突策略按此列判断最新值' },
  { api: 'etl_batch', name: '同步批次', type: 'string', group: '其他', source: 'etl_batch_id', desc: '系统字段' },
];
const pkw = ref('');
const ppage = ref(1);
const PPAGE = 12;
watch(pkw, () => { ppage.value = 1; });
const NEW_PROP: P = { api: 'fulfill_store_id', name: '履约门店', type: 'string', group: '组织', dim: true, source: 'fulfill_store_id', desc: '实际出库发货的门店' };
const srcProps = computed(() => (isDraft.value ? [...PROPS, NEW_PROP] : PROPS));
const pfiltered = computed(() => srcProps.value.filter((p) => !pkw.value || p.name.includes(pkw.value) || p.api.includes(pkw.value)));
const prows = computed(() => pfiltered.value.slice((ppage.value - 1) * PPAGE, ppage.value * PPAGE));
const propCols = [
  { title: '显示名', key: 'name', width: 200 },
  { title: 'API 名', dataIndex: 'api', width: 180 },
  { title: '类型', key: 'type', width: 140 },
  { title: '分组', dataIndex: 'group', width: 72 },
  { title: '标记', key: 'flags', width: 200 },
  { title: '源字段', dataIndex: 'source', width: 150 },
  { title: '说明', dataIndex: 'desc', ellipsis: true },
];

// —— 关系 ——
const LINKS = [
  { name: '包含订单行', target: '订单行', card: '1 : N', key: 'order_id = order_line.order_id', reverse: '所属订单', isDefault: true },
  { name: '下单门店', target: '门店', card: 'N : 1', key: 'store_id = store.store_id', reverse: '门店订单', isDefault: true },
  { name: '履约门店', target: '门店', card: 'N : 1', key: 'fulfill_store_id = store.store_id', reverse: '履约订单', isDefault: false },
  { name: '下单会员', target: '会员', card: 'N : 1', key: 'member_id = member.member_id', reverse: '会员订单', isDefault: true },
  { name: '发货仓', target: '仓库', card: 'N : 1', key: 'warehouse_id = warehouse.wh_id', reverse: '仓库订单', isDefault: true },
  { name: '退款单', target: '退款单', card: '1 : N', key: 'order_id = refund.order_id', reverse: '原订单', isDefault: true },
  { name: '出库单', target: '出库单', card: '1 : N', key: 'order_id = shipment.order_id', reverse: '来源订单', isDefault: true },
  { name: '参与活动', target: '促销活动', card: 'N : M', key: '经 order_promotion 中间表', reverse: '活动订单', isDefault: false },
];
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
const MAP_ALL = PROPS.map((p) => OVERRIDE[p.name] ?? { prop: p.name, col: p.source, srcType: SRC_TYPE[p.type] ?? 'VARCHAR2(64)', transform: p.sensitive ? '敏感字段：脱敏显示' : '' });
const QS = [
  { q: '上个月各大区的有效订单数是多少？', cert: true, query: 'metric: valid_order_count\nbreakdown: [组织.大区]\ntime: 出库时间 = 上月\nrelease: v2026.10.3' },
  { q: '华东大区 9 月小程序渠道的订单，出库及时率是多少？', cert: true, query: 'metric: ship_ontime_rate\nfilters: [大区 = 华东, 渠道 = 小程序]\ntime: 出库时间 = 2026-09\nrelease: v2026.10.3' },
  { q: '本周取消的订单里，哪些门店最多？', cert: false, query: '' },
];
const mpage = ref(1);
const MPAGE = 12;
const mrows = computed(() => MAP_ALL.slice((mpage.value - 1) * MPAGE, mpage.value * MPAGE));
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-button :disabled="readonly" @click="!readonly && openChange('从数据源导入对象类型')">从数据源导入</a-button>
    <a-button type="primary" :disabled="readonly" @click="!readonly && openChange('新建对象类型')">新建对象类型</a-button>
  </Teleport>

  <div class="ot">
    <div class="panel ot-list">
      <div class="ot-search"><a-input-search v-model:value="kw" placeholder="搜索名称或 API 名" /></div>
      <ul class="ot-items">
        <li v-for="t in list" :key="t.id" :class="{ active: t.id === current.id }" @click="pick(t.id)">
          <div class="ot-row">
            <span class="t-14 ot-name">{{ t.name }}</span>
            <span class="t-12 num" :class="t.status === '变更中' ? 'c-warning' : 'c-3'">{{ t.status === '已发布' ? '' : t.status }}</span>
          </div>
          <div class="ot-row">
            <span class="t-12 c-3 num">{{ t.api }}</span>
            <span class="t-12 c-3">{{ t.group }}</span>
          </div>
        </li>
      </ul>
      <div class="ot-foot">
        <span class="t-12 c-3">共 {{ filtered.length }} 条</span>
        <a-pagination v-model:current="lpage" simple size="small" :total="filtered.length" :page-size="LPAGE" />
      </div>
    </div>

    <div class="panel ot-detail">
      <DraftBanner v-if="isDraft && draft" :draft="draft"
        @submit="message.success(`${draft.id} 已提交审核，可在“变更与发布”查看`)" @discard="setView('published')" />
      <div class="ot-head">
        <span class="t-16 fw-600 c-1">{{ current.name }}</span>
        <span class="t-14 c-3 num">{{ current.api }} · {{ current.props }} 个属性 · {{ current.count }} 条</span>
        <a-tag :color="current.status === '已发布' ? 'success' : current.status === '变更中' ? 'warning' : 'default'">{{ current.status }}</a-tag>
        <a-segmented v-if="draft && !readonly" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => setView(v)" />
        <span class="ot-sp" />
        <span class="t-12 c-3">数据截至 2026-10-24 23:00</span>
        <a-button>预览数据</a-button>
        <a-tooltip v-if="readonly" :title="reason"><a-button type="primary" disabled>发起变更</a-button></a-tooltip>
        <a-button v-else-if="!draft" type="primary" @click="openChange(`${current.name}（${current.api}）`)">发起变更</a-button>
        <a-button v-else-if="!isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
      </div>
      <GroupedTabs v-model="tab" :groups="OBJECT_TYPE_TAB_GROUPS" />

      <!-- 概览 -->
      <div v-if="tab === 'overview'" class="ot-body ot-ov">
        <div class="ot-ov-left">
          <a-descriptions :column="2" bordered size="small">
            <a-descriptions-item label="显示名">订单</a-descriptions-item>
            <a-descriptions-item label="API 名">SalesOrder</a-descriptions-item>
            <a-descriptions-item label="业务域">零售交易</a-descriptions-item>
            <a-descriptions-item label="负责人">陈立军 · 财务数据组</a-descriptions-item>
            <a-descriptions-item label="主键">订单号（order_id）</a-descriptions-item>
            <a-descriptions-item label="标题属性">展示单号</a-descriptions-item>
            <a-descriptions-item label="数据源">ERP 生产库（达梦 DM8 · Oracle 兼容）</a-descriptions-item>
            <a-descriptions-item label="同步方式">每小时增量，按源数据更新时间</a-descriptions-item>
            <a-descriptions-item label="冲突策略">用户编辑优先</a-descriptions-item>
            <a-descriptions-item label="生命周期">正式</a-descriptions-item>
            <a-descriptions-item label="当前版本">随本体发布 v2026.10.3 生效</a-descriptions-item>
            <a-descriptions-item label="最近修改">陈立军 · 2026-10-24 21:17</a-descriptions-item>
            <a-descriptions-item label="描述" :span="2">顾客在门店、小程序、外卖平台、社区团购下的一笔交易。一单可以有多行商品，按出库时间计入销售。测试单默认不参与统计。</a-descriptions-item>
          </a-descriptions>
          <div class="t-14 fw-600">问数示例问题</div>
          <ul class="ot-qs t-14 c-2">
            <li v-for="q in QS" :key="q.q">
              <span>{{ q.q }}</span>
              <a-popover v-if="q.cert" placement="right">
                <template #content><pre class="ot-sq t-12">{{ q.query }}</pre></template>
                <a-tag color="success" class="ot-cert">已认证</a-tag>
              </a-popover>
              <span v-else class="t-12 c-3">未认证</span>
            </li>
          </ul>
        </div>
        <div class="ot-ov-right">
          <div class="t-14 fw-600">使用情况</div>
          <dl class="ot-stat">
            <dt class="t-14 c-3">关系</dt><dd class="t-14 num">8 条</dd>
            <dt class="t-14 c-3">引用的指标</dt><dd class="t-14 num">
              <HoverCard kind="metric" id="order_cnt">有效订单数</HoverCard>、<HoverCard kind="metric" id="atv">客单价（不含税）</HoverCard> 等 6 个</dd>
            <dt class="t-14 c-3">动作类型</dt><dd class="t-14 num">3 个（第三期启用）</dd>
            <dt class="t-14 c-3">规则</dt><dd class="t-14 num">2 条</dd>
            <dt class="t-14 c-3">看板卡片</dt><dd class="t-14 num">14 张</dd>
            <dt class="t-14 c-3">近 30 天问数</dt><dd class="t-14 num">2,318 次</dd>
          </dl>
          <div class="t-14 fw-600">数据质量</div>
          <dl class="ot-stat">
            <dt class="t-14 c-3">主键唯一</dt><dd class="t-14 c-success">通过</dd>
            <dt class="t-14 c-3">外键可关联</dt><dd class="t-14">门店 100%，会员 99.82%</dd>
            <dt class="t-14 c-3">最近同步</dt><dd class="t-14 num">2026-10-25 15:00 · 3,912 条</dd>
          </dl>
        </div>
      </div>

      <!-- 属性 -->
      <div v-else-if="tab === 'properties'" class="ot-body">
        <div class="ot-tool">
          <a-input-search v-model:value="pkw" placeholder="搜索属性名或 API 名" class="ot-tool-search" />
          <span class="t-12 c-3">主键 1 · 维度 {{ isDraft ? 12 : 11 }} · 敏感 3<template v-if="isDraft"> · 草稿改动 2</template></span>
          <span class="ot-sp" />
          <a-tooltip :title="editTip"><a-button :disabled="!canEdit">批量编辑</a-button></a-tooltip>
          <a-tooltip :title="editTip"><a-button type="primary" :disabled="!canEdit">新增属性</a-button></a-tooltip>
        </div>
        <a-table :columns="propCols" :data-source="prows" :pagination="false" size="small" row-key="api">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <a>{{ record.name }}</a>
              <a-tag v-if="isDraft && DIFF[record.name]" :color="DIFF[record.name] === '新增' ? 'success' : 'warning'" class="ot-diff">{{ DIFF[record.name] }}</a-tag>
            </template>
            <template v-else-if="column.key === 'type'">{{ record.type }}<span v-if="record.enumName" class="c-3"> · {{ record.enumName }}</span></template>
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
          <span class="t-12 c-3">共 {{ pfiltered.length }} 条</span>
          <a-pagination v-model:current="ppage" simple size="small" :total="pfiltered.length" :page-size="PPAGE" />
        </div>
      </div>

      <!-- 关系 -->
      <div v-else-if="tab === 'links'" class="ot-body">
        <div class="ot-tool">
          <span class="t-12 c-3">订单到门店有两条路径：问“各门店订单数”默认按下单门店，按履约门店统计需在问题里指明（发布校验 V13）</span>
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
          <a-descriptions-item label="数据源">ERP 生产库</a-descriptions-item>
          <a-descriptions-item label="数据库">达梦 DM8 · Oracle 兼容模式</a-descriptions-item>
          <a-descriptions-item label="源表">ERP.T_SALE_ORDER</a-descriptions-item>
          <a-descriptions-item label="主键映射">order_id → 订单号</a-descriptions-item>
          <a-descriptions-item label="同步方式">每小时增量</a-descriptions-item>
          <a-descriptions-item label="增量字段">update_time</a-descriptions-item>
          <a-descriptions-item label="最近同步">2026-10-25 15:00 · 成功</a-descriptions-item>
          <a-descriptions-item label="数据截至">2026-10-24 23:00</a-descriptions-item>
        </a-descriptions>
        <div class="ot-tool">
          <span class="t-14 fw-600">字段映射</span>
          <span class="t-12 c-3">已映射 31 / 源表 38 个字段，7 个未使用</span>
          <span class="ot-sp" />
          <a-button>重新采集元数据</a-button>
        </div>
        <a-table :columns="MAP_COLS" :data-source="mrows" size="small" row-key="prop" :pagination="false" />
        <div class="ot-pager">
          <span class="t-12 c-3">共 {{ MAP_ALL.length }} 条</span>
          <a-pagination v-model:current="mpage" simple size="small" :total="MAP_ALL.length" :page-size="MPAGE" />
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
.ot-pager { display: flex; align-items: center; justify-content: flex-end; gap: 12px; }
</style>
