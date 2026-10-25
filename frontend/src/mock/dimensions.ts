/**
 * 维度与字典（静态本体负责人）。问数用的分析维度（层级、取值、同义词）和属性取值码表。
 * 与其他 mock 对齐：
 *   组织维度 = 大区 › 门店（CR-0005，v2026.10.2 起门店直属考核大区，不经过省份）；省份是行政维度，经“所在省份”关系；
 *   业态“前置仓 FWH”只在 CR-0009 草稿里；订单状态“已发货”自 v2026.10.1（CR-0004）起是“已出库”的同义词；
 *   会员等级 5 个（L0–L4），CR-0001“黑金 L5”已驳回；渠道与 orders.ts CHANNELS 一致（启用 6 个）；
 *   仓库与 orders.ts WAREHOUSES 一致；门店取值来自 stores.ts ALL_STORES；大区 REG-EC 与 qa.ts REGION_EC 一致。
 *   使用情况里的指标按 metrics.ts 的 dims 自动生成（metricDims），与指标页“允许的维度”和关系类型页的使用情况一致。
 */
import { METRICS } from './metrics';
import { ALL_STORES } from './stores';

export type DimKind = '维度' | '码表';
export type DimStatus = '已发布' | '草稿' | '已驳回';
export type ValueStatus = '启用' | '停用';
export type DiffTag = '新增' | '修改';
export interface DimLevel { name: string; api: string; source: string; count: number; note?: string }
export interface DimValue {
  key: string; code: string; name: string; level: string; parent?: string; status: ValueStatus;
  synonyms: string[]; count?: number; src?: string; note?: string;
  /** 名称可悬停的对象（ObjRef） */
  ref?: { kind: string; id: string };
  /** 生效区间：since ≤ 版本 < until（查看历史版本时用） */
  since?: string; until?: string;
  /** 只在某张草稿单里（新增） */
  draft?: string; diff?: DiffTag;
  /** 草稿里改过的值，pub 是已发布版的原值 */
  pub?: Partial<DimValue>;
}
export type MapMatch = '精确' | '包含' | '多值';
export type MapSource = '取值同义词' | '取值名称' | '业务知识' | '问数反馈';
export interface DimMapping {
  key: string; phrase: string; targets: string[]; match: MapMatch; source: MapSource; hits: number;
  /** 歧义说明（橙色提示）；resolve 为当前的处理方式 */
  conflict?: string; resolve?: string; draft?: string; diff?: DiffTag;
}
export interface DimUsage { type: '指标' | '规则' | '动作' | '认证问答' | '视图' | '评测题' | '权限'; name: string; note: string; hover?: { kind: string; id: string } }
export interface DimHistory { ver: string; cr?: string; status: '生效中' | '已替代' | '草稿' | '已驳回'; by: string; at: string; change: string }
export interface DimQa { defaultLevel: string; unspecified: string; nullAs: string; sort: string; note: string }
export interface DimItem {
  id: string; name: string; api: string; kind: DimKind; group: string; status: DimStatus; owner: string;
  /** 来源对象（Q_OBJECT_TYPES key，可悬停）与属性 */
  object: string; objectName: string; attr: string; column: string;
  /** 取值来源（码表、主数据表）与同步方式 */
  valueSrc: string; sync: string;
  desc: string; levels: DimLevel[]; qa: DimQa;
  /** CR-0005 之前的层级（查看历史版本时显示） */
  levelsBefore?: { ver: string; levels: DimLevel[]; qa: Partial<DimQa>; desc?: string; attr?: string; column?: string; footnote?: string };
  values: DimValue[]; mappings: DimMapping[];
  /** metrics.ts 里 dims 的叫法，用来生成指标使用情况 */
  metricDims: string[]; metricNote?: Record<string, string>;
  usage: DimUsage[]; history: DimHistory[]; since: string;
  /** 取值表“记录数”列的叫法；取值只列了一部分时的说明 */
  countLabel?: string; valuesNote?: string;
}

const BASE = (by: string, change = '首次发布'): DimHistory => ({ ver: 'v2026.03.2', status: '生效中', by, at: '2026-03-18 10:06', change });
const V = (code: string, name: string, level: string, more: Partial<DimValue> = {}): DimValue =>
  ({ key: `${level}:${code}`, code, name, level, status: '启用', synonyms: [], ...more });
const MP = (phrase: string, targets: string[], match: MapMatch, source: MapSource, hits: number, more: Partial<DimMapping> = {}): DimMapping =>
  ({ key: `${phrase}>${targets.join('|')}`, phrase, targets, match, source, hits, ...more });
const R = (id: string, name: string, note: string): DimUsage => ({ type: '规则', name: `${id} ${name}`, note, hover: { kind: 'rule', id } });

// —— 时间：2025-01 至 2026-12 的月份（财务结账到 2026-09）——
const MONTHS: DimValue[] = [];
for (let y = 2026; y >= 2025; y--) {
  for (let m = 12; m >= 1; m--) {
    const code = `${y}-${String(m).padStart(2, '0')}`;
    const closed = code <= '2026-09';
    const syn = code === '2026-10' ? ['本月', '10 月'] : code === '2026-09' ? ['上月', '9 月'] : code === '2025-10' ? ['去年同期'] : [];
    MONTHS.push(V(code, `${y} 年 ${m} 月`, '月', { parent: `${y}Q${Math.ceil(m / 3)}`, synonyms: syn, note: closed ? '财务已结账' : code === '2026-10' ? '进行中，数据截至 2026-10-24' : '未开始' }));
  }
}

// —— 组织：大区 6 个（门店主数据 ORG.T_REGION），门店来自 ALL_STORES ——
const REGIONS: [string, string, number, number, string][] = [
  ['REG-EC', '华东大区', 486, 7, '陈杰'], ['REG-NC', '华北大区', 0, 8, '--'], ['REG-SC', '华南大区', 0, 3, '--'],
  ['REG-CC', '华中大区', 0, 3, '--'], ['REG-SW', '西南大区', 0, 5, '--'], ['REG-NW', '西北大区', 0, 5, '--'],
];
const STORE_SYN: Record<string, string[]> = {
  SH0031: ['张江店', '张江高科'], SH0102: ['七宝店', '七宝万科'], SH0133: ['大悦城店'], HZ0012: ['文三路店'], HZ0058: ['星光大道店'],
  NJ0076: ['百家湖店'], SZ0009: ['湖西店', '园区店'], WX0011: ['崇安寺店'], NB0015: ['鄞州万达店'],
};
const ORG_VALUES: DimValue[] = [
  ...REGIONS.map(([code, name, stores, prov, owner]) => V(code, name, '大区', {
    count: stores, synonyms: code === 'REG-EC' ? ['华东区', '华东'] : [name.replace('大区', '区')],
    status: stores ? '启用' : '停用', ref: code === 'REG-EC' ? { kind: 'region', id: code } : undefined,
    note: stores ? `负责人 ${owner} · 下辖 ${prov} 个省份的门店` : `已建档，暂无门店（行政上辖 ${prov} 个省份）`,
  })),
  ...ALL_STORES.map((s) => V(s.id, s.name, '门店', {
    parent: '华东大区', synonyms: STORE_SYN[s.id] ?? [], ref: { kind: 'store', id: s.id },
    status: s.status === '已闭店' ? '停用' : '启用', note: `${s.city} · ${s.region} · ${s.format}${s.status === '营业中' ? '' : ` · ${s.status}`}`,
  })),
];

// —— 行政区域：31 个省份（ORG.T_PROVINCE），有门店的 10 个城市 ——
const PROVINCES: [string, string, string, number, string[]][] = [
  ['31', '上海市', '华东大区', 132, ['上海', '沪']], ['32', '江苏省', '华东大区', 121, ['江苏', '苏']], ['33', '浙江省', '华东大区', 98, ['浙江', '浙']],
  ['34', '安徽省', '华东大区', 54, ['安徽', '皖']], ['35', '福建省', '华东大区', 27, ['福建', '闽']], ['36', '江西省', '华东大区', 24, ['江西', '赣']],
  ['37', '山东省', '华东大区', 30, ['山东', '鲁']],
  ['11', '北京市', '华北大区', 0, ['北京']], ['12', '天津市', '华北大区', 0, ['天津']], ['13', '河北省', '华北大区', 0, ['河北']], ['14', '山西省', '华北大区', 0, ['山西']],
  ['15', '内蒙古自治区', '华北大区', 0, ['内蒙古']], ['21', '辽宁省', '华北大区', 0, ['辽宁']], ['22', '吉林省', '华北大区', 0, ['吉林']], ['23', '黑龙江省', '华北大区', 0, ['黑龙江']],
  ['44', '广东省', '华南大区', 0, ['广东']], ['45', '广西壮族自治区', '华南大区', 0, ['广西']], ['46', '海南省', '华南大区', 0, ['海南']],
  ['41', '河南省', '华中大区', 0, ['河南']], ['42', '湖北省', '华中大区', 0, ['湖北']], ['43', '湖南省', '华中大区', 0, ['湖南']],
  ['50', '重庆市', '西南大区', 0, ['重庆']], ['51', '四川省', '西南大区', 0, ['四川']], ['52', '贵州省', '西南大区', 0, ['贵州']], ['53', '云南省', '西南大区', 0, ['云南']],
  ['54', '西藏自治区', '西南大区', 0, ['西藏']],
  ['61', '陕西省', '西北大区', 0, ['陕西']], ['62', '甘肃省', '西北大区', 0, ['甘肃']], ['63', '青海省', '西北大区', 0, ['青海']], ['64', '宁夏回族自治区', '西北大区', 0, ['宁夏']],
  ['65', '新疆维吾尔自治区', '西北大区', 0, ['新疆']],
];
const CITIES: [string, string, string, number][] = [
  ['3101', '上海', '上海市', 132], ['3201', '南京', '江苏省', 58], ['3205', '苏州', '江苏省', 41], ['3202', '无锡', '江苏省', 22], ['3301', '杭州', '浙江省', 71],
  ['3302', '宁波', '浙江省', 27], ['3401', '合肥', '安徽省', 54], ['3501', '福州', '福建省', 27], ['3601', '南昌', '江西省', 24], ['3701', '济南', '山东省', 30],
];
const GEO_VALUES: DimValue[] = [
  ...PROVINCES.map(([code, name, reg, n, syn]) => V(code, name, '省份', { count: n, synonyms: syn, note: `行政大区：${reg}${n ? '' : ' · 暂无门店'}` })),
  ...CITIES.map(([code, name, prov, n]) => V(code, `${name}市`, '城市', { parent: prov, count: n, synonyms: [name] })),
];

// —— 商品品类：大类 12 个，列出与商品 mock 对应的中类 ——
const CATS: [string, string, number, string[], ValueStatus?, string?][] = [
  ['C01', '酒水饮料', 6120, ['饮料', '酒水']], ['C02', '粮油调味', 5482, ['粮油']], ['C03', '乳品', 2316, ['奶制品']], ['C04', '休闲食品', 7905, ['零食']],
  ['C05', '日化家清', 4877, ['日化', '家清']], ['C06', '生鲜', 3654, ['生鲜果蔬']], ['C07', '冷冻食品', 2208, ['冻品']], ['C08', '个护美妆', 5133, ['个护']],
  ['C09', '母婴用品', 2861, ['母婴']], ['C10', '家居百货', 4102, ['百货']], ['C11', '速食冲调', 3555, ['速食']], ['C12', '烟草', 0, ['香烟'], '停用', '2025-12 起不再经营，历史销售保留'],
];
const SUBS: [string, string, string, number, string[]][] = [
  ['C0101', '饮用水', '酒水饮料', 412, ['矿泉水', '纯净水']], ['C0102', '碳酸饮料', '酒水饮料', 538, ['汽水']], ['C0103', '啤酒', '酒水饮料', 621, []],
  ['C0201', '食用油', '粮油调味', 486, []], ['C0202', '大米', '粮油调味', 305, []], ['C0203', '酱油', '粮油调味', 274, []],
  ['C0301', '常温奶', '乳品', 388, ['纯牛奶']], ['C0302', '低温奶', '乳品', 296, ['鲜奶', '巴氏奶']], ['C0303', '乳饮料', '乳品', 211, []],
  ['C0401', '坚果', '休闲食品', 642, ['坚果炒货']], ['C0402', '膨化食品', '休闲食品', 915, ['薯片']],
  ['C0501', '洗衣', '日化家清', 433, ['洗衣液']], ['C0502', '纸品', '日化家清', 368, ['纸巾', '抽纸']],
  ['C0601', '水果', '生鲜', 702, []], ['C0602', '肉禽', '生鲜', 531, ['肉类']], ['C0603', '蔬菜', '生鲜', 846, []],
];
const CAT_VALUES: DimValue[] = [
  ...CATS.map(([code, name, n, syn, st, note]) => V(code, name, '大类', { count: n, synonyms: syn, status: st ?? '启用', note })),
  ...SUBS.map(([code, name, p, n, syn]) => V(code, name, '中类', { parent: p, count: n, synonyms: syn })),
];

export const DIM_ITEMS: DimItem[] = [
  // ———————————— 维度 ————————————
  {
    id: 'time', name: '时间', api: 'dim_time', kind: '维度', group: '时间', status: '已发布', owner: '王敏',
    object: 'order', objectName: '订单', attr: '订单.下单时间（各对象的时间属性）', column: 'DIM.T_CALENDAR.cal_date',
    valueSrc: '公共日历表 DIM.T_CALENDAR（含财务结账月、节假日）', sync: '每年 12 月生成下一年日历',
    desc: '所有对象的时间属性都关联到公共日历表。周按 ISO 周（周一开始）；“月”默认自然月，财务结账以财务月为准。',
    levels: [
      { name: '年', api: 'year', source: 'cal_year', count: 8 }, { name: '季', api: 'quarter', source: 'cal_quarter', count: 32 },
      { name: '月', api: 'month', source: 'cal_month', count: 96 }, { name: '周', api: 'week', source: 'iso_week', count: 418, note: 'ISO 周，周一开始' },
      { name: '日', api: 'day', source: 'cal_date', count: 2922 },
    ],
    qa: { defaultLevel: '日', unspecified: '时间范围 ≤ 31 天按日，≤ 1 年按月，超过 1 年按年', nullAs: '--', sort: '按时间升序', note: '问题里没有时间时默认“近 7 天”，并在回答里写明' },
    values: MONTHS,
    mappings: [
      MP('近 7 天', ['2026-10-18 至 2026-10-24'], '精确', '业务知识', 3820, { resolve: '不含今天' }),
      MP('上周', ['2026-W43（10-19 至 10-25）'], '精确', '业务知识', 1264),
      MP('今年', ['2026 年'], '精确', '业务知识', 902),
      MP('Q3', ['2026 年第 3 季度'], '精确', '业务知识', 318),
      MP('国庆', ['2026-10-01 至 2026-10-07'], '精确', '业务知识', 211),
      MP('双 11', ['2026-11-11'], '精确', '问数反馈', 146, { conflict: '与促销活动“双 11 大促”（11-01 至 11-11）同名', resolve: '默认按 11-11 当天；问活动效果时追问' }),
      MP('上个财务月', ['2026-09（财务月）'], '精确', '业务知识', 74, { resolve: '财务月与自然月一致，结账日 次月 5 日' }),
    ],
    metricDims: ['日期'], metricNote: { gmv: '按下单时间', ship_ontime: '按应出库时间', atv: '按下单时间' },
    usage: [
      R('R-021', '门店销售额环比下降', '按周（ISO 周）比较'), R('R-015', '出库超 48 小时未签收', '按出库时间计时长'),
      { type: '认证问答', name: '上月各大区销售额', note: '“上月”= 2026-09' }, { type: '评测题', name: '时间解析 14 题', note: '每次发布回放' },
    ],
    history: [BASE('王敏')], since: 'v2026.03.2',
  },
  {
    id: 'org', name: '组织', api: 'dim_org', kind: '维度', group: '组织', status: '已发布', owner: '陈立军',
    object: 'store', objectName: '门店', attr: '门店（所属大区）', column: 'ORG.T_STORE.region_code',
    valueSrc: '门店主数据 ORG.T_REGION、ORG.T_STORE', sync: '每日 02:00 全量同步',
    desc: '考核组织。门店经“所属大区”直属大区，不经过省份（CR-0005）；按省份统计用“行政区域”维度。门店的“区域”（华东一区—五区）是运营片区，不是本维度的层级。',
    levels: [
      { name: '大区', api: 'region', source: '门店.所属大区 → 大区', count: 6, note: '其中 1 个有门店' },
      { name: '门店', api: 'store', source: '门店', count: 486 },
    ],
    qa: { defaultLevel: '大区', unspecified: '问“各大区”按所属大区；只说“各门店”按门店，超过 20 家时只列前 20 并给出总数', nullAs: '--', sort: '按指标值降序', note: '订单按下单门店归属；履约门店需用户指明' },
    levelsBefore: {
      ver: 'v2026.10.2',
      levels: [
        { name: '省份', api: 'province', source: '门店.所在省份 → 省份', count: 31 },
        { name: '门店', api: 'store', source: '门店', count: 486 },
      ],
      qa: { defaultLevel: '省份', unspecified: '问“各大区”时按省份归属汇总（经省份所属大区）', sort: '按指标值降序', note: '订单按下单门店归属' },
      desc: '考核组织。门店经“所在省份”归属省份，省份再归属大区；大区不作为本维度的层级（v2026.10.2 由 CR-0005 改为 大区 › 门店）。',
      attr: '门店（所在省份）', column: 'ORG.T_STORE.province_code',
      footnote: '该版本里省份既是行政区划又是考核口径；v2026.10.2 起省份拆为独立的“行政区域”维度。',
    },
    values: ORG_VALUES,
    mappings: [
      MP('华东', ['华东大区'], '包含', '取值同义词', 2894, { conflict: '包含匹配还会命中运营片区“华东一区”—“华东五区”', resolve: '精确命中优先：按华东大区；问“华东一区”等按片区' }),
      MP('总部', ['全部大区'], '精确', '业务知识', 96, { resolve: '不加组织筛选' }),
      MP('文三路店', ['杭州西湖文三路店'], '精确', '取值同义词', 188, { conflict: '前置仓“杭州西湖文三”（QZ0004）名称相近，CR-0009 建档后会同时出现', resolve: '默认指门店 HZ0012；前置仓需写明“前置仓”' }),
      MP('各店', ['门店（全部）'], '精确', '业务知识', 1408),
      MP('我的门店', ['当前用户可见门店'], '精确', '业务知识', 362, { resolve: '按数据权限的行级范围' }),
    ],
    metricDims: ['大区', '门店'], metricNote: { gmv: '组织维度默认路径：下单门店 → 所属大区', order_cnt: '按门店、大区拆分（下单门店）', ship_ontime: '按门店拆分；履约门店需指明' },
    usage: [
      R('R-021', '门店销售额环比下降', '按门店判断，按大区汇总告警'),
      { type: '视图', name: '华东大区销售周报', note: '随 CR-0005 新增' }, { type: '视图', name: '华东大区经营日报', note: '4 个卡片' },
      { type: '认证问答', name: '上月各大区销售额', note: '已认证' }, { type: '认证问答', name: '上个月各大区的有效订单数是多少？', note: '已认证' },
      { type: '权限', name: '门店店长', note: '行级范围：本店' }, { type: '权限', name: '大区经理（华东）', note: '行级范围：华东大区' },
      { type: '评测题', name: '大区相关 6 题', note: 'v2026.10.2 发布前回放全部通过' },
    ],
    history: [
      { ver: 'v2026.10.2', cr: 'CR-0005', status: '生效中', by: '陈立军', at: '2026-10-17 20:45', change: '层级改为 大区 → 门店；省份改为行政维度' },
      { ...BASE('陈立军', '首次发布：省份 → 门店'), status: '已替代' },
    ],
    since: 'v2026.03.2',
  },
  {
    id: 'geo', name: '行政区域', api: 'dim_geo', kind: '维度', group: '组织', status: '已发布', owner: '陈立军',
    object: 'province', objectName: '省份', attr: '门店.所在省份、门店.城市', column: 'ORG.T_STORE.province_code',
    valueSrc: '国家行政区划代码（GB/T 2260）ORG.T_PROVINCE', sync: '每季度核对一次',
    desc: '门店所在的行政省份和城市，经“所在省份”关系。行政大区（省份所属大区）与考核大区可能不同，按省份归属统计需用户指明。',
    levels: [
      { name: '省份', api: 'province', source: '门店.所在省份 → 省份', count: 31, note: '有门店 7 个' },
      { name: '城市', api: 'city', source: '门店.城市', count: 10, note: '有门店的城市' },
    ],
    qa: { defaultLevel: '省份', unspecified: '说“各省”按省份，说“各城市”按城市', nullAs: '--', sort: '按指标值降序', note: '只列有数据的省份；“按省份归属的大区”经省份所属大区，需写明“按省份”' },
    values: GEO_VALUES,
    mappings: [
      MP('江浙沪', ['上海市', '江苏省', '浙江省'], '多值', '业务知识', 734),
      MP('苏', ['江苏省'], '精确', '取值同义词', 41, { conflict: '单字“苏”也可能指苏州市', resolve: '单独出现按江苏省；“苏锡常”按城市' }),
      MP('苏锡常', ['苏州市', '无锡市', '常州市'], '多值', '业务知识', 58, { conflict: '常州市暂无门店，取值里没有', resolve: '按苏州、无锡回答，并说明常州无门店' }),
      MP('长三角', ['上海市', '江苏省', '浙江省', '安徽省'], '多值', '业务知识', 129),
    ],
    metricDims: ['省份'], metricNote: { gmv: '省份维度（经所在省份）' },
    usage: [
      { type: '视图', name: '门店经营月报', note: '省份筛选' },
      { type: '认证问答', name: '按省份归属统计各大区门店数', note: '已认证，问题里写明“按省份”' },
    ],
    history: [
      { ver: 'v2026.10.2', cr: 'CR-0005', status: '生效中', by: '陈立军', at: '2026-10-17 20:45', change: '从组织维度拆出，作为独立的行政维度（此前是组织维度的上级层级）' },
    ],
    since: 'v2026.10.2',
  },
  {
    id: 'area', name: '运营片区', api: 'dim_store_area', kind: '维度', group: '组织', status: '已发布', owner: '陈立军',
    object: 'store', objectName: '门店', attr: '门店.区域', column: 'ORG.T_STORE.area_name',
    valueSrc: '门店主数据（门店属性，无单独主表）', sync: '每日 02:00 随门店同步',
    desc: '华东大区内部的运营片区，门店的“区域”属性。不是组织层级；问“各区域”“一区”时用本维度。',
    levels: [{ name: '片区', api: 'area', source: '门店.区域', count: 5 }],
    qa: { defaultLevel: '片区', unspecified: '说“各区域”“各片区”按片区', nullAs: '--', sort: '按片区编号', note: '“华东”精确命中华东大区，不按片区' },
    values: [
      V('EC1', '华东一区', '片区', { count: 132, synonyms: ['一区', '上海片区'], note: '上海' }),
      V('EC2', '华东二区', '片区', { count: 98, synonyms: ['二区'], note: '杭州、宁波' }),
      V('EC3', '华东三区', '片区', { count: 121, synonyms: ['三区'], note: '南京、苏州、无锡' }),
      V('EC4', '华东四区', '片区', { count: 54, synonyms: ['四区'], note: '合肥' }),
      V('EC5', '华东五区', '片区', { count: 81, synonyms: ['五区'], note: '福州、南昌、济南' }),
    ],
    mappings: [MP('区域', ['运营片区（全部）'], '精确', '业务知识', 517, { conflict: '用户常把“区域”和“大区”混用', resolve: '“各区域”按片区；“各大区”按组织维度' })],
    metricDims: ['区域'],
    usage: [{ type: '视图', name: '片区经营周报', note: '2 个卡片' }, { type: '评测题', name: '片区相关 3 题', note: '每次发布回放' }],
    history: [BASE('陈立军')], since: 'v2026.03.2',
  },
  {
    id: 'category', name: '商品品类', api: 'dim_category', kind: '维度', group: '商品', status: '已发布', owner: '王敏',
    object: 'category', objectName: '品类', attr: '商品.所属品类 → 品类（上级品类）', column: 'MDM.T_CATEGORY.category_id',
    valueSrc: '商品主数据 MDM.T_CATEGORY（三级）', sync: '每日 01:30 增量同步',
    desc: '三级品类加 SKU。商品经“所属品类”挂到小类，小类经“上级品类”逐级上卷到中类、大类。',
    levels: [
      { name: '大类', api: 'category_l1', source: '品类（level = 1）', count: 12 }, { name: '中类', api: 'category_l2', source: '品类（level = 2）', count: 86 },
      { name: '小类', api: 'category_l3', source: '品类（level = 3）', count: 1106 }, { name: 'SKU', api: 'sku', source: '商品', count: 48213 },
    ],
    qa: { defaultLevel: '大类', unspecified: '说“各品类”按大类；点名中类、小类时按点名的层级', nullAs: '未分类', sort: '按指标值降序', note: '小类、SKU 取值很多，这里只列大类和中类；问数按名称和同义词检索全部层级' },
    values: CAT_VALUES,
    mappings: [
      MP('牛奶', ['常温奶', '低温奶'], '多值', '取值同义词', 466, { conflict: '同时对应 2 个中类', resolve: '默认合并两个中类；说“纯牛奶”只按常温奶' }),
      MP('水', ['饮用水'], '精确', '问数反馈', 82),
      MP('烟', ['烟草'], '精确', '业务知识', 9, { resolve: '烟草已停用，回答时说明 2025-12 起不再经营' }),
    ],
    metricDims: ['品类'], metricNote: { gmv: '商品维度：大类 › 中类 › 小类', gross_margin: '商品、品类维度' },
    usage: [R('R-007', '商品毛利率越线', '按品类设阈值'), { type: '视图', name: '品类毛利看板', note: '3 个卡片' }, { type: '评测题', name: '品类相关 8 题', note: '每次发布回放' }],
    history: [BASE('王敏')], since: 'v2026.03.2',
  },
  {
    id: 'channel', name: '渠道', api: 'dim_channel', kind: '维度', group: '交易', status: '已发布', owner: '陈立军',
    object: 'order', objectName: '订单', attr: '订单.渠道', column: 'ERP.T_SALE_ORDER.channel_code',
    valueSrc: '码表 MDM.T_DICT（dict_type = SALE_CHANNEL）', sync: '码表变更时实时同步',
    desc: '订单的下单渠道，两级：渠道类型 › 渠道。启用 6 个渠道，停用的渠道保留历史订单。',
    levels: [
      { name: '渠道类型', api: 'channel_type', source: '码表上级', count: 3 },
      { name: '渠道', api: 'channel', source: '订单.渠道', count: 9, note: '启用 6 个' },
    ],
    qa: { defaultLevel: '渠道', unspecified: '说“各渠道”按渠道；说“线上线下”按渠道类型', nullAs: '--', sort: '按指标值降序', note: '停用渠道只在历史时间范围里出现' },
    values: [
      V('OFFLINE', '线下门店', '渠道类型', { synonyms: ['线下'] }), V('OWN', '自营线上', '渠道类型', { synonyms: ['自营'] }), V('PLATFORM', '第三方平台', '渠道类型', { synonyms: ['平台'] }),
      V('POS', '门店POS', '渠道', { parent: '线下门店', count: 702318, synonyms: ['门店收银', '线下收银'] }),
      V('MINI', '小程序到家', '渠道', { parent: '自营线上', count: 196402, synonyms: ['小程序', '到家'] }),
      V('GROUP', '社群团购', '渠道', { parent: '自营线上', count: 61877, synonyms: ['社群', '团购'] }),
      V('MT', '美团闪购', '渠道', { parent: '第三方平台', count: 158210, synonyms: ['美团'] }),
      V('ELE', '饿了么', '渠道', { parent: '第三方平台', count: 104655, synonyms: ['饿百'] }),
      V('JD', '京东到家', '渠道', { parent: '第三方平台', count: 62940, synonyms: ['京东'] }),
      V('DY', '抖音小时达', '渠道', { parent: '第三方平台', count: 0, status: '停用', note: '2026-06 试点结束' }),
      V('DMALL', '多点', '渠道', { parent: '第三方平台', count: 0, status: '停用', note: '2025-08 停止合作' }),
      V('TEL', '电话订购', '渠道', { parent: '自营线上', count: 0, status: '停用', note: '2025-03 下线' }),
    ],
    mappings: [
      MP('线上', ['自营线上', '第三方平台'], '多值', '业务知识', 1937, { conflict: '财务报表里“线上”只算自营线上', resolve: '默认两类都算；问“财务口径”时只算自营线上' }),
      MP('到家', ['小程序到家'], '包含', '取值同义词', 845, { conflict: '包含匹配还会命中“京东到家”', resolve: '精确命中优先：小程序到家；“到家业务”按自营线上 + 第三方平台' }),
      MP('门店', ['门店POS'], '精确', '问数反馈', 38, { conflict: '与组织维度“门店”同名', resolve: '默认理解为组织维度；“门店渠道”“线下”才指本值' }),
    ],
    metricDims: ['渠道'],
    usage: [{ type: '视图', name: '门店经营月报', note: '渠道占比卡片' }, { type: '认证问答', name: '本月各渠道销售额占比', note: '已认证' }, { type: '评测题', name: '渠道相关 5 题', note: '每次发布回放' }],
    history: [BASE('陈立军')], since: 'v2026.03.2',
  },
  {
    id: 'member_level', name: '会员等级', api: 'dim_member_level', kind: '维度', group: '客户', status: '已发布', owner: '吴佳',
    object: 'member_level', objectName: '会员等级', attr: '会员.会员等级 → 会员等级', column: 'CRM.T_MEMBER.level_code',
    valueSrc: '会员中心 CRM.T_MEMBER_LEVEL', sync: '每日 03:00 全量同步',
    desc: '会员当前所在等级（5 个，L0–L4）。订单没有会员的归入“散客”。CR-0001 新增“黑金”已驳回。',
    levels: [{ name: '会员等级', api: 'level', source: '会员等级', count: 5 }, { name: '会员', api: 'member', source: '会员', count: 863027 }],
    qa: { defaultLevel: '会员等级', unspecified: '说“各等级”按会员等级；含散客', nullAs: '散客', sort: '按等级编码', note: '按订单统计时用下单时的会员等级快照' },
    values: [
      V('GUEST', '散客', '会员等级', { synonyms: ['非会员'], src: '会员为空', note: '不是 CRM 等级，统计时补的取值' }),
      V('L0', '普卡', '会员等级', { count: 512330, synonyms: ['普通会员'], src: 'CRM L0' }),
      V('L1', '银卡', '会员等级', { count: 201486, synonyms: ['银卡会员'], src: 'CRM L1' }),
      V('L2', '金卡', '会员等级', { count: 104215, synonyms: ['金卡会员'], src: 'CRM L2' }),
      V('L3', '铂金卡', '会员等级', { count: 37902, synonyms: ['铂金', '白金'], src: 'CRM L3' }),
      V('L4', '钻石卡', '会员等级', { count: 7094, synonyms: ['钻石', '钻卡'], src: 'CRM L4' }),
    ],
    mappings: [
      MP('高等级会员', ['铂金卡', '钻石卡'], '多值', '业务知识', 213),
      MP('黑金', [], '精确', '问数反馈', 17, { conflict: '没有对应取值（CR-0001 已驳回）', resolve: '回答“没有黑金等级”，列出现有 5 个等级' }),
    ],
    metricDims: ['会员等级'], metricNote: { gmv: '会员等级维度' },
    usage: [{ type: '指标', name: '新增会员数', note: '按等级拆分' }, { type: '视图', name: '会员等级分布', note: '1 个卡片' }, { type: '评测题', name: '会员相关 6 题', note: '每次发布回放' }],
    history: [
      { ver: '—', cr: 'CR-0001', status: '已驳回', by: '吴佳', at: '2026-09-12 10:02', change: '新增取值“黑金”（L5）；CRM 没有 L5 等级码' },
      BASE('吴佳'),
    ],
    since: 'v2026.03.2',
  },
  {
    id: 'format', name: '业态', api: 'dim_store_format', kind: '维度', group: '组织', status: '已发布', owner: '陈立军',
    object: 'store', objectName: '门店', attr: '门店.业态', column: 'ORG.T_STORE.format_code',
    valueSrc: '取值字典“门店业态” MDM.T_DICT（dict_type = STORE_FORMAT）', sync: '码表变更时实时同步',
    desc: '门店的经营业态。CR-0009 草稿新增“前置仓”，用于 6 家前置仓建档后按业态单独统计。',
    levels: [{ name: '业态', api: 'format', source: '门店.业态', count: 4, note: '启用 3 个' }],
    qa: { defaultLevel: '业态', unspecified: '说“各业态”按业态', nullAs: '--', sort: '按指标值降序', note: '业态是门店属性，按订单的下单门店取值' },
    values: [
      V('SQ', '社区店', '业态', { count: 268, synonyms: ['社区'] }),
      V('SC', '商场店', '业态', { count: 141, synonyms: ['购物中心店'] }),
      V('XZL', '写字楼店', '业态', { count: 77, synonyms: ['写字楼', '办公楼店'] }),
      V('BLD', '便利店', '业态', { count: 0, status: '停用', note: '2024-07 并入社区店' }),
      V('FWH', '前置仓', '业态', { count: 6, synonyms: [], note: '6 家待门店主数据建档（QZ0001 等）', draft: 'CR-0009', diff: '新增' }),
    ],
    mappings: [
      MP('前置仓', ['前置仓'], '精确', '取值同义词', 0, { draft: 'CR-0009', diff: '新增', conflict: '与“仓库”对象名称相近（V9 信息）', resolve: '优先理解为门店业态；问库存、出库时按仓库' }),
      MP('便利店', ['社区店'], '精确', '业务知识', 64, { resolve: '停用取值“便利店”并入社区店' }),
    ],
    metricDims: ['业态'],
    usage: [{ type: '视图', name: '门店经营月报', note: '业态筛选' }, { type: '视图', name: '门店业态分布', note: '1 个卡片' }, { type: '评测题', name: '业态相关 2 题', note: '发布前自动回放' }],
    history: [
      { ver: '—', cr: 'CR-0009', status: '草稿', by: '陈立军', at: '2026-10-21 10:26', change: '新增取值“前置仓”（FWH）' },
      BASE('陈立军'),
    ],
    since: 'v2026.03.2',
  },
  {
    id: 'warehouse', name: '仓库', api: 'dim_warehouse', kind: '维度', group: '供应链', status: '已发布', owner: '孙浩然',
    object: 'warehouse', objectName: '仓库', attr: '出库单.出库仓 → 仓库', column: 'WMS.T_WAREHOUSE.wh_code',
    valueSrc: '供应链 WMS 库 WMS.T_WAREHOUSE', sync: '每日 02:30 全量同步',
    desc: '实际出库的仓库。订单按出库单的出库仓归属；门店自提订单没有仓库。',
    levels: [{ name: '仓库', api: 'warehouse', source: '仓库', count: 4 }],
    qa: { defaultLevel: '仓库', unspecified: '说“各仓”按仓库', nullAs: '门店自提', sort: '按指标值降序', note: '“补货仓”和“实际出库仓”是两条路径，问补货时需写明' },
    values: [
      V('WH-SH01', '上海嘉定常温仓', '仓库', { count: 412880, synonyms: ['嘉定仓'] }),
      V('WH-SH02', '上海青浦冷链仓', '仓库', { count: 168204, synonyms: ['冷链仓', '青浦仓'] }),
      V('WH-HZ01', '杭州萧山常温仓', '仓库', { count: 301557, synonyms: ['萧山仓'] }),
      V('WH-NJ01', '南京江宁中心仓', '仓库', { count: 314585, synonyms: ['江宁仓'] }),
    ],
    mappings: [MP('仓', ['仓库（全部）'], '包含', '问数反馈', 122, { conflict: '“前置仓”草稿通过后也会命中', resolve: '“前置仓”精确命中业态，其余按仓库' })],
    metricDims: ['仓库'], metricNote: { ship_ontime: '按仓库拆分的默认路径' },
    usage: [{ type: '指标', name: '平均出库时长', note: '按实际出库仓拆分' }, { type: '视图', name: '仓配日报', note: '3 个卡片' }, R('R-012', '可用库存低于安全线', '通知补货仓')],
    history: [BASE('孙浩然')], since: 'v2026.03.2',
  },

  // ———————————— 码表 ————————————
  {
    id: 'order_status', name: '订单状态', api: 'dict_order_status', kind: '码表', group: '交易', status: '已发布', owner: '王敏',
    object: 'order', objectName: '订单', attr: '订单.状态', column: 'ERP.T_SALE_ORDER.status',
    valueSrc: 'ERP 订单状态码 + WMS 出库状态，统一到 MDM.T_DICT（ORDER_STATUS）', sync: '码表变更时实时同步',
    desc: '订单生命周期状态，与状态机 SM-ORD（订单生命周期 v4）一致。CR-0004 起 ERP“已发货”和 WMS“OUTBOUND”统一为“已出库”。',
    levels: [{ name: '状态', api: 'status', source: '订单.状态', count: 6 }],
    qa: { defaultLevel: '状态', unspecified: '“订单数”默认不含已取消', nullAs: '--', sort: '按生命周期顺序', note: '“已发货”是“已出库”的同义词，不是独立状态' },
    values: [
      V('WAIT_PAY', '待支付', '状态', { count: 3214, synonyms: ['待付款'], src: 'ERP S0' }),
      V('WAIT_OUT', '待出库', '状态', { count: 8906, synonyms: ['待发货'], src: 'ERP S1' }),
      V('PICKING', '拣货中', '状态', { count: 5127, synonyms: ['备货中'], src: 'ERP S2 · WMS PICKING' }),
      V('OUTBOUND', '已出库', '状态', { count: 498310, synonyms: ['已发货', '已发出'], src: 'ERP S3 · WMS OUTBOUND', since: 'v2026.10.1' }),
      V('S3', '已发货', '状态', { count: 498310, synonyms: [], src: 'ERP S3', until: 'v2026.10.1', note: 'WMS OUTBOUND 未映射' }),
      V('SIGNED', '已签收', '状态', { count: 712640, synonyms: ['已收货'], src: 'ERP S4' }),
      V('CANCELLED', '已取消', '状态', { count: 58205, synonyms: ['取消单'], src: 'ERP S9' }),
    ],
    mappings: [
      MP('未发货', ['待出库', '拣货中'], '多值', '业务知识', 412),
      MP('已完成', ['已签收'], '精确', '问数反馈', 286, { conflict: '退款单也有“已完成”状态', resolve: '按问题主体判断；没有主体时按订单' }),
    ],
    metricDims: [],
    usage: [
      { type: '指标', name: '有效订单数', note: '状态 ≠ 已取消', hover: { kind: 'metric', id: 'order_cnt' } },
      { type: '指标', name: '出库及时率', note: '分子按变为“已出库”计', hover: { kind: 'metric', id: 'ship_ontime' } },
      { type: '指标', name: '销售额（不含税）', note: '经订单行取订单状态过滤', hover: { kind: 'metric', id: 'gmv' } },
      R('SM-ORD', '订单状态非法迁移', '状态机 v4 的状态集合'), R('R-015', '出库超 48 小时未签收', '状态 = 已出库'),
      { type: '动作', name: '提交状态修正', note: '目标状态取自本码表', hover: { kind: 'action', id: 'correct_order_status' } },
      { type: '评测题', name: '订单状态相关 4 题', note: 'CR-0004 发布前回放，全部通过' },
    ],
    history: [
      { ver: 'v2026.10.1', cr: 'CR-0004', status: '生效中', by: '王敏', at: '2026-10-09 22:03', change: '“已发货”统一为“已出库”；WMS OUTBOUND 映射到已出库；“已发货”改为同义词' },
      { ...BASE('王敏'), status: '已替代' },
    ],
    since: 'v2026.03.2',
  },
  {
    id: 'refund_reason', name: '退款原因', api: 'dict_refund_reason', kind: '码表', group: '交易', status: '已发布', owner: '刘婷',
    object: 'refund', objectName: '退款单', attr: '退款单.退款原因', column: 'ERP.T_REFUND.reason_code',
    valueSrc: '码表 MDM.T_DICT（dict_type = REFUND_REASON）', sync: '码表变更时实时同步',
    desc: '顾客或门店提交退款时选择的原因，分商品、履约、顾客三类责任。',
    levels: [{ name: '原因', api: 'reason', source: '退款单.退款原因', count: 12, note: '启用 11 个' }],
    qa: { defaultLevel: '原因', unspecified: '说“退款原因分布”按原因，前 10 项之外合并为“其他”', nullAs: '其他', sort: '按退款单数降序', note: '按退款单计数；按退款行计数需写明' },
    values: [
      V('RR01', '商品破损', '原因', { count: 6382, synonyms: ['破损', '坏了'], note: '商品责任' }),
      V('RR02', '质量问题', '原因', { count: 5127, synonyms: ['质量差'], note: '商品责任' }),
      V('RR03', '少件漏发', '原因', { count: 4410, synonyms: ['漏发', '少发'], note: '履约责任' }),
      V('RR04', '发错商品', '原因', { count: 2266, synonyms: ['发错'], note: '履约责任' }),
      V('RR05', '商品临期或过期', '原因', { count: 1953, synonyms: ['过期', '临期'], note: '商品责任' }),
      V('RR06', '配送超时', '原因', { count: 3871, synonyms: ['送太慢', '超时'], note: '履约责任' }),
      V('RR07', '缺货取消', '原因', { count: 2894, synonyms: ['缺货'], note: '履约责任' }),
      V('RR08', '不想要了', '原因', { count: 7206, synonyms: ['买错了', '七天无理由'], note: '顾客原因' }),
      V('RR09', '价格变动', '原因', { count: 1388, synonyms: ['降价'], note: '顾客原因' }),
      V('RR10', '地址填错', '原因', { count: 905, synonyms: [], note: '顾客原因' }),
      V('RR11', '七天无理由', '原因', { count: 0, status: '停用', note: '2025-11 合并到“不想要了”' }),
      V('RR99', '其他', '原因', { count: 2512, synonyms: [] }),
    ],
    mappings: [
      MP('买错了', ['不想要了'], '精确', '取值同义词', 37, { conflict: '与“发错商品”容易混淆', resolve: '“买错”归顾客原因，“发错”归履约责任' }),
      MP('门店原因', ['少件漏发', '发错商品', '配送超时', '缺货取消'], '多值', '业务知识', 92),
    ],
    metricDims: [],
    usage: [
      { type: '指标', name: '退款率', note: '可按原因拆分（CR-0010 审核中，改按退款完成时间）' },
      { type: '动作', name: '退款审核', note: '审核单展示退款原因', hover: { kind: 'action', id: 'approve_refund' } },
      { type: '认证问答', name: '上周退款率最高的门店', note: '附原因分布' },
    ],
    history: [BASE('刘婷')], since: 'v2026.03.2',
  },
  {
    id: 'store_status', name: '门店状态', api: 'dict_store_status', kind: '码表', group: '组织', status: '已发布', owner: '陈立军',
    object: 'store', objectName: '门店', attr: '门店.状态', column: 'ORG.T_STORE.status',
    valueSrc: '门店主数据（ERP 组织状态）', sync: '每日 02:00 随门店同步',
    desc: '门店经营状态，由“变更门店状态”动作写回组织主数据。',
    levels: [{ name: '状态', api: 'status', source: '门店.状态', count: 4, note: '启用 3 个' }],
    qa: { defaultLevel: '状态', unspecified: '统计门店数默认只算营业中', nullAs: '--', sort: '按状态编码', note: '已闭店门店的历史数据仍参与历史时间范围的统计' },
    values: [
      V('OPEN', '营业中', '状态', { count: 471, synonyms: ['在营', '营业'] }),
      V('RENOV', '装修中', '状态', { count: 9, synonyms: ['装修'] }),
      V('CLOSED', '已闭店', '状态', { count: 6, synonyms: ['闭店', '关店'] }),
      V('PREP', '筹备中', '状态', { count: 0, status: '停用', note: '2025 年起筹备门店不在主数据建档' }),
    ],
    mappings: [MP('停业', ['装修中', '已闭店'], '多值', '业务知识', 23, { conflict: '“停业”可能是临时停业或闭店', resolve: '两个状态都算，并在回答里分开列出' })],
    metricDims: [],
    usage: [
      { type: '动作', name: '变更门店状态', note: '营业中 / 装修中 / 已闭店之间切换', hover: { kind: 'action', id: 'change_store_status' } },
      R('R-021', '门店销售额环比下降', '只看营业中的门店'),
      { type: '视图', name: '门店经营月报', note: '状态筛选' },
    ],
    history: [BASE('陈立军')], since: 'v2026.03.2',
  },
  {
    id: 'pay_method', name: '支付方式', api: 'dict_pay_method', kind: '码表', group: '交易', status: '已发布', owner: '陈立军',
    object: 'order', objectName: '订单', attr: '订单.支付方式', column: 'ERP.T_SALE_ORDER.pay_type',
    valueSrc: '码表 MDM.T_DICT（dict_type = PAY_TYPE）', sync: '码表变更时实时同步',
    desc: '订单的主支付方式；组合支付按金额最大的一笔。',
    levels: [{ name: '支付方式', api: 'pay_type', source: '订单.支付方式', count: 7 }],
    qa: { defaultLevel: '支付方式', unspecified: '按主支付方式', nullAs: '未知', sort: '按订单数降序', note: '' },
    values: [
      V('WX', '微信支付', '支付方式', { count: 628104, synonyms: ['微信'] }), V('ALI', '支付宝', '支付方式', { count: 301446, synonyms: [] }),
      V('UNION', '银联卡', '支付方式', { count: 96233, synonyms: ['刷卡', '银行卡'] }), V('CASH', '现金', '支付方式', { count: 41290, synonyms: [] }),
      V('SVC', '储值卡', '支付方式', { count: 88315, synonyms: ['会员卡支付'] }), V('ECNY', '数字人民币', '支付方式', { count: 6120, synonyms: ['数币'] }),
      V('PLAT', '平台代收', '支付方式', { count: 124894, synonyms: [], note: '美团闪购、饿了么、京东到家' }),
    ],
    mappings: [],
    metricDims: [],
    usage: [{ type: '视图', name: '门店经营月报', note: '支付方式占比卡片' }],
    history: [BASE('陈立军')], since: 'v2026.03.2',
  },
  {
    id: 'delivery_type', name: '配送方式', api: 'dict_delivery_type', kind: '码表', group: '交易', status: '已发布', owner: '周晓峰',
    object: 'order', objectName: '订单', attr: '订单.配送方式', column: 'ERP.T_SALE_ORDER.delivery_type',
    valueSrc: '码表 MDM.T_DICT（dict_type = DELIVERY_TYPE）', sync: '码表变更时实时同步',
    desc: '线上订单的履约方式；门店POS 订单为“到店购买”。',
    levels: [{ name: '配送方式', api: 'delivery_type', source: '订单.配送方式', count: 5 }],
    qa: { defaultLevel: '配送方式', unspecified: '按配送方式', nullAs: '到店购买', sort: '按订单数降序', note: '' },
    values: [
      V('INSTORE', '到店购买', '配送方式', { count: 702318, synonyms: [] }), V('PICKUP', '到店自提', '配送方式', { count: 58410, synonyms: ['自提'] }),
      V('STORE', '门店配送', '配送方式', { count: 199869, synonyms: ['自配送'] }), V('RIDER', '平台骑手', '配送方式', { count: 325805, synonyms: ['骑手配送'] }),
      V('EXPRESS', '快递', '配送方式', { count: 0, status: '停用', note: '2025-05 停止' }),
    ],
    mappings: [],
    metricDims: [],
    usage: [{ type: '视图', name: '履约时效看板', note: '配送方式筛选' }],
    history: [BASE('周晓峰')], since: 'v2026.03.2',
  },
  {
    id: 'shipment_status', name: '出库单状态', api: 'dict_shipment_status', kind: '码表', group: '供应链', status: '已发布', owner: '孙浩然',
    object: 'shipment', objectName: '出库单', attr: '出库单.状态', column: 'TMS.T_SHIPMENT.status',
    valueSrc: 'WMS / TMS 运单状态，统一到 MDM.T_DICT（SHIP_STATUS）', sync: '码表变更时实时同步',
    desc: '出库单在仓内和运输中的状态。与订单状态不是同一套，“已签收”两边都有。',
    levels: [{ name: '状态', api: 'status', source: '出库单.状态', count: 6 }],
    qa: { defaultLevel: '状态', unspecified: '按状态', nullAs: '--', sort: '按流程顺序', note: '' },
    values: [
      V('WAIT_PICK', '待拣货', '状态', { count: 6230, synonyms: [] }), V('PICKING', '拣货中', '状态', { count: 4118, synonyms: [] }),
      V('OUT', '已出库', '状态', { count: 18406, synonyms: [] }), V('TRANSIT', '运输中', '状态', { count: 9972, synonyms: ['在途'] }),
      V('SIGNED', '已签收', '状态', { count: 1151290, synonyms: [] }), V('REJECT', '已拒收', '状态', { count: 7210, synonyms: ['拒收'] }),
    ],
    mappings: [],
    metricDims: [],
    usage: [{ type: '指标', name: '平均出库时长', note: '状态 = 已出库 的时间点' }],
    history: [BASE('孙浩然')], since: 'v2026.03.2',
  },
  {
    id: 'product_status', name: '商品状态', api: 'dict_product_status', kind: '码表', group: '商品', status: '已发布', owner: '王敏',
    object: 'product', objectName: '商品', attr: '商品.状态', column: 'MDM.T_SKU.status',
    valueSrc: '商品主数据 MDM.T_SKU', sync: '每日 01:30 增量同步',
    desc: '商品的经营状态。只有“在售”的商品参与缺货率、毛利率预警。',
    levels: [{ name: '状态', api: 'status', source: '商品.状态', count: 4 }],
    qa: { defaultLevel: '状态', unspecified: '商品数默认只算在售', nullAs: '--', sort: '按状态编码', note: '' },
    values: [
      V('ON', '在售', '状态', { count: 41862, synonyms: ['上架'] }), V('PENDING', '待上架', '状态', { count: 1307, synonyms: [] }),
      V('OFF', '停售', '状态', { count: 3911, synonyms: ['下架'] }), V('ELIM', '淘汰', '状态', { count: 1133, synonyms: [] }),
    ],
    mappings: [],
    metricDims: [],
    usage: [R('R-007', '商品毛利率越线', '只看在售商品'), R('R-012', '可用库存低于安全线', '只看在售商品')],
    history: [BASE('王敏')], since: 'v2026.03.2',
  },
  {
    id: 'alert_level', name: '告警级别', api: 'dict_alert_level', kind: '码表', group: '自动化', status: '已发布', owner: '孙浩然',
    object: 'order', objectName: '订单', attr: '告警.级别', column: 'OPS.T_ALERT.level',
    valueSrc: '平台内置码表', sync: '随本体发布',
    desc: '规则命中后生成的告警级别，决定通知方式和处理时限。',
    levels: [{ name: '级别', api: 'level', source: '告警.级别', count: 3 }],
    qa: { defaultLevel: '级别', unspecified: '按级别', nullAs: '--', sort: '高 → 低', note: '' },
    values: [
      V('HIGH', '高', '级别', { synonyms: ['严重', '紧急'], note: '电话 + 站内通知，2 小时内处理' }),
      V('MID', '中', '级别', { synonyms: ['一般'], note: '站内通知，当天处理' }),
      V('LOW', '低', '级别', { synonyms: ['提示'], note: '只记录' }),
    ],
    mappings: [],
    metricDims: [],
    usage: [R('SM-ORD', '订单状态非法迁移', '固定为高'), R('R-015', '出库超 48 小时未签收', '固定为中')],
    history: [BASE('孙浩然')], since: 'v2026.03.2',
  },
];

const EXTRA: Record<string, Pick<DimItem, 'countLabel' | 'valuesNote'>> = {
  time: { valuesNote: '取值按月列出 2025—2026 年；年、季、周、日由日历表生成' },
  org: { countLabel: '门店数', valuesNote: '门店共 486 家，这里列出示例数据中的 38 家；其余按名称和同义词检索' },
  geo: { countLabel: '门店数', valuesNote: '城市只列有门店的 10 个' }, area: { countLabel: '门店数' },
  category: { countLabel: 'SKU 数', valuesNote: '小类 1,106 个、SKU 48,213 个，这里只列大类和示例中类' },
  channel: { countLabel: '订单数' }, member_level: { countLabel: '会员数' }, format: { countLabel: '门店数' }, warehouse: { countLabel: '出库单数' },
  order_status: { countLabel: '订单数' }, refund_reason: { countLabel: '退款单数' }, store_status: { countLabel: '门店数' },
  pay_method: { countLabel: '订单数' }, delivery_type: { countLabel: '订单数' }, shipment_status: { countLabel: '出库单数' }, product_status: { countLabel: 'SKU 数' },
};
DIM_ITEMS.forEach((d) => Object.assign(d, EXTRA[d.id] ?? {}));

/** 指标使用情况：按 metrics.ts 的 dims 生成 */
export function metricUsage(d: DimItem): DimUsage[] {
  return METRICS.filter((m) => m.dims.some((x) => d.metricDims.includes(x))).map((m) => ({
    type: '指标', name: m.name, hover: { kind: 'metric', id: m.id },
    note: d.metricNote?.[m.id] ?? `允许按“${m.dims.filter((x) => d.metricDims.includes(x)).join('”“')}”拆分${m.status === '草稿' ? '（指标草稿）' : ''}`,
  }));
}
