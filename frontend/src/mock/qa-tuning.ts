/**
 * 问数调优（/build/ontology/qa-tuning）mock：认证问答、评测题库（题目 + 运行记录 + 失败用例）、反馈与纠错、高频问题推荐。
 * 路线 B：问数不写 SQL，回答由“本体查询模块”按语义计划（指标 + 维度 + 时间 + 过滤 + 排序）生成，这里只展示语义计划摘要。
 * 与 src/mock/changes.ts 对齐（只读引用）：
 *  - CR-0010 退款率 v2→v3（审核中）：认证问答“上个月各大区的退款率”待重新认证；评测“退款相关 4 题”发布前自动回放
 *  - CR-0011 毛利率 v2→v3（草稿）：认证问答“各品类毛利率排名”待重新认证；规则 R-007 阈值需重估
 *  - CR-0008 同义词“发货及时率”→出库及时率（已通过）：评测“履约相关 3 题”召回率预计提升；对应反馈“待发布生效”
 *  - CR-0015 同义词“成交单量”→有效订单数（已通过）：“订单量相关 2 题”；与 CR-0008 一起进 v2026.10.4，发布前自动回放 5 题（ER-0025）
 */
import { CHANGE_REQUESTS, NEXT_VERSION, type ChangeRequest } from './changes';
import { DATA_CUTOFF } from './quality';
import { QA_METRIC } from './qa';

export const TUNING_CUTOFF = DATA_CUTOFF;
export const CUR_VERSION = 'v2026.10.3';
export { NEXT_VERSION };
/**
 * 本页自带的变更单补充：与 static 分支 changes.ts（81a413d）的同号变更单名称/状态一致；
 * master 合入新版 changes.ts 后优先取 CHANGE_REQUESTS，这里只做兜底。
 */
const LOCAL_CRS: ChangeRequest[] = [
  { id: 'CR-0015', title: '有效订单数补充同义词“成交单量”', owner: '何静', reviewer: '刘婷', status: '已通过', level: '修订', changes: 2, updatedAt: '2026-10-25 10:18', reason: '财务和电商常说“成交单量”，问数召回不到' },
];
export const crById = (id: string): ChangeRequest | undefined => CHANGE_REQUESTS.find((c) => c.id === id) ?? LOCAL_CRS.find((c) => c.id === id);
/** 待发布 v2026.10.4 包含的变更单（与“变更与发布”的整版发布一致） */
export const NEXT_RELEASE_CRS = ['CR-0008', 'CR-0015'];

/**
 * 未发布变更单 → 受影响指标（只列改口径的单；同义词单不影响已认证答案）。
 * master 的 changes.ts 用 touches（metric:refund_rate / metric:gross_margin）表达同样的关系；
 * qa 分支的 changes.ts 还没有 touches，所以这里放一个小映射，metricIds 与 touches 的写法一致。
 */
export const CR_METRIC_TOUCHES: Record<string, { metricIds: string[]; note: string }> = {
  'CR-0010': { metricIds: ['refund_rate'], note: '退款率将由 v2（申请时间）改为 v3（退款完成时间），发布后需按 v3 重新认证' },
  'CR-0011': { metricIds: ['gross_margin'], note: '毛利率将扣除促销补贴（v2 → v3），发布后需重新认证' },
};
/** 本页指标 key → touches 里的指标 id */
const METRIC_IDS: Record<string, string> = { gmv: 'gmv', gmv_tax: 'gmv_tax', margin: 'gross_margin', orders: 'order_cnt', ship: 'ship_ontime', atv: 'atv', repeat: 'repeat_sales', repeat_tax: 'repeat_sales_tax', refund: 'refund_rate' };
const isPending = (id: string) => { const st = crById(id)?.status; return !!st && st !== '已发布' && st !== '已驳回'; };
/** 指标当前受哪张未发布的改口径变更单影响 */
export const pendingCrOf = (metricKey: string): string | undefined =>
  Object.keys(CR_METRIC_TOUCHES).find((id) => isPending(id) && CR_METRIC_TOUCHES[id].metricIds.includes(METRIC_IDS[metricKey] ?? metricKey));

/** 问数可用的指标（名称 + 口径版本）；drawerId 有值的在公共 METRICS 里能打开指标抽屉；cr 由 pendingCrOf 推导 */
export interface TMetric { key: string; name: string; version: string; drawerId?: string; cr?: string }
export const T_METRICS: Record<string, TMetric> = {
  gmv: { key: 'gmv', name: '销售额（不含税）', version: 'v3', drawerId: 'gmv' },
  gmv_tax: { key: 'gmv_tax', name: '销售额（含税）', version: 'v2', drawerId: 'gmv_tax' },
  margin: { key: 'margin', name: '毛利率', version: 'v2', drawerId: 'gross_margin' },
  orders: { key: 'orders', name: '有效订单数', version: 'v1', drawerId: 'order_cnt' },
  ship: { key: 'ship', name: '出库及时率', version: 'v1', drawerId: 'ship_ontime' },
  atv: { key: 'atv', name: '客单价（不含税）', version: 'v1', drawerId: 'atv' },
  repeat: { key: 'repeat', name: QA_METRIC.ex.name, version: QA_METRIC.ex.version },
  repeat_tax: { key: 'repeat_tax', name: QA_METRIC.in.name, version: QA_METRIC.in.version },
  refund: { key: 'refund', name: '退款率', version: 'v2' },
};
Object.values(T_METRICS).forEach((m) => { m.cr = pendingCrOf(m.key); });

export const metricLabel = (k: string) => `${T_METRICS[k].name} ${T_METRICS[k].version}`;

export interface SemPlan { metric: string; dims: string[]; time: string; filter?: string; sort?: string }
export const planText = (p: SemPlan) =>
  [metricLabel(p.metric), p.dims.length ? `按 ${p.dims.join('、')}` : '汇总', p.time, p.filter, p.sort].filter(Boolean).join(' · ');

export const PEOPLE_T = ['王敏', '陈立军', '刘婷', '周晓峰', '何静', '孙浩然'];

/* ───────── 认证问答 ───────── */
export type CertStatus = '已认证' | '待重新认证' | '待认证' | '已停用';
export interface CertQa {
  id: string; question: string; variants: string[]; plan: SemPlan; status: CertStatus; cr?: string;
  certifier: string; certifiedAt: string; hits: number; source: string; note?: string;
}
export const certStatusText = (c: CertQa) => (c.status === '待重新认证' && c.cr ? `待重新认证 · 受 ${c.cr} 影响` : c.status);

type CertSeed = [string, SemPlan, string, string, number, string[]?];
const P = (metric: string, dims: string[], time: string, filter?: string, sort?: string): SemPlan => ({ metric, dims, time, filter, sort });
const CERT_SEEDS: CertSeed[] = [
  ['上个月各大区的退款率', P('refund', ['大区'], '上个月'), '刘婷', '2026-09-08 10:12', 412, ['上月大区退款率', '各大区退款率是多少']],
  ['各品类毛利率排名', P('margin', ['品类'], '本月至今', undefined, '毛利率降序'), '何静', '2026-09-15 16:40', 538, ['哪个品类毛利最高', '品类毛利率从高到低']],
  ['华东大区上周销售额', P('gmv', [], '上周', '大区 = 华东大区'), '王敏', '2026-10-08 09:30', 1265, ['华东上周卖了多少', '华东大区上周营业额']],
  ['各大区本月销售额及环比', P('gmv', ['大区'], '本月至今', undefined, '环比'), '王敏', '2026-10-08 09:42', 986],
  ['近 6 个月复购客户销售额趋势', P('repeat', ['月份'], '近 6 个月'), '孙浩然', '2026-10-12 14:05', 744, ['复购客户销售额走势', '老客销售额趋势']],
  ['华东一区复购客户销售额（含税）', P('repeat_tax', [], '本月至今', '大区 = 华东一区'), '孙浩然', '2026-10-12 14:20', 231],
  ['各门店复购客户销售额 Top 10', P('repeat', ['门店'], '上个月', undefined, '降序 · 前 10'), '孙浩然', '2026-10-13 11:02', 402],
  ['昨天各仓库出库及时率', P('ship', ['仓库'], '昨天'), '周晓峰', '2026-09-26 18:30', 655, ['昨日仓库出库及时率']],
  ['出库及时率低于 95% 的门店', P('ship', ['门店'], '近 7 天', '出库及时率 < 95%'), '周晓峰', '2026-09-26 18:44', 389],
  ['本周有效订单数按渠道', P('orders', ['渠道'], '本周'), '陈立军', '2026-09-20 10:00', 577],
  ['上海各区县门店销售额', P('gmv', ['区县', '门店'], '上个月', '城市 = 上海'), '王敏', '2026-10-09 15:16', 318],
  ['双 11 备货期间日销售额', P('gmv', ['日期'], '2026-10-20 至今'), '王敏', '2026-10-20 09:12', 271],
  ['各大区销售额（含税）', P('gmv_tax', ['大区'], '上个月'), '陈立军', '2026-10-24 21:30', 205, ['含税口径大区销售']],
  ['杭州门店客单价', P('atv', ['门店'], '上个月', '城市 = 杭州'), '刘婷', '2026-10-18 13:20', 96],
  ['毛利率低于 18% 的商品', P('margin', ['商品'], '近 30 天', '毛利率 < 18%'), '何静', '2026-09-30 17:05', 284],
  ['各渠道退款率', P('refund', ['渠道'], '近 30 天'), '刘婷', '2026-09-08 10:30', 173],
  ['华东三区本月销售额', P('gmv', [], '本月至今', '大区 = 华东三区'), '王敏', '2026-10-08 10:05', 352],
  ['新店开业 30 天销售额', P('gmv', ['门店'], '开业后 30 天', '门店状态 = 新开'), '陈立军', '2026-10-02 11:48', 143],
  ['会员复购客户销售额占比', P('repeat', ['会员等级'], '上个月'), '孙浩然', '2026-10-14 10:10', 167],
  ['各城市有效订单数排名', P('orders', ['城市'], '上周', undefined, '降序'), '陈立军', '2026-09-21 09:40', 226],
  ['合肥门店上周销售额', P('gmv', ['门店'], '上周', '城市 = 合肥'), '王敏', '2026-10-10 16:22', 189],
  ['生鲜品类毛利率趋势', P('margin', ['周'], '近 12 周', '品类 = 生鲜'), '何静', '2026-10-05 15:00', 121],
  ['各大区出库及时率', P('ship', ['大区'], '上周'), '周晓峰', '2026-09-27 08:50', 302],
  ['昨天销售额最高的 5 家门店', P('gmv', ['门店'], '昨天', undefined, '降序 · 前 5'), '王敏', '2026-10-11 09:00', 845],
  ['复购客户销售额（含税）按月', P('repeat_tax', ['月份'], '今年'), '孙浩然', '2026-10-15 10:35', 88],
  ['上海门店客单价对比', P('atv', ['门店'], '上周', '城市 = 上海'), '刘婷', '2026-10-18 13:40', 74],
  ['退款金额最高的门店', P('refund', ['门店'], '上个月', undefined, '降序 · 前 10'), '刘婷', '2026-09-09 14:12', 64],
  ['线上渠道本月有效订单数', P('orders', [], '本月至今', '渠道 = 线上'), '陈立军', '2026-09-22 10:20', 203],
  ['各品类销售额占比', P('gmv', ['品类'], '上个月'), '王敏', '2026-10-09 11:05', 433],
  ['南京门店出库及时率', P('ship', ['门店'], '近 7 天', '城市 = 南京'), '周晓峰', '2026-09-28 17:15', 112],
];
function buildCerts(): CertQa[] {
  return CERT_SEEDS.map(([question, plan, certifier, certifiedAt, hits, variants], i) => {
    const m = T_METRICS[plan.metric];
    // 按绑定指标推导：绑定了“未发布改口径变更单”所改指标的问答都要重新认证（已停用的除外）
    const disabled = i === 26;
    const affected = disabled ? undefined : m.cr;
    const status: CertStatus = disabled ? '已停用' : affected ? '待重新认证' : i === 13 || i === 25 ? '待认证' : '已认证';
    const note = affected ? CR_METRIC_TOUCHES[affected].note
      : status === '已停用' ? '与“各渠道退款率”重复，已合并' : status === '待认证' ? `${m.name}仍是草稿口径，发布后再认证` : undefined;
    return {
      id: `CQ-${String(i + 1).padStart(3, '0')}`, question, variants: variants ?? [], plan, status, cr: affected,
      certifier, certifiedAt, hits, source: i % 4 === 1 ? '反馈纠错' : i % 4 === 2 ? '高频问题推荐' : '手动新建', note,
    };
  });
}
export const CERT_QAS: CertQa[] = buildCerts();

/* ───────── 评测题库 ───────── */
export type EvalGroup = '销售' | '退款' | '履约' | '毛利' | '复购' | '订单';
export interface EvalQ { id: string; question: string; group: EvalGroup; expect: SemPlan; last: '通过' | '失败'; cr?: string }
const EQ_SEEDS: [string, EvalGroup, SemPlan, boolean?][] = [
  ['华东大区上周销售额', '销售', P('gmv', [], '上周', '大区 = 华东大区')],
  ['上周华东卖了多少钱', '销售', P('gmv', [], '上周', '大区 = 华东大区')],
  ['各大区本月营业额', '销售', P('gmv', ['大区'], '本月至今')],
  ['上个月销售额最高的门店', '销售', P('gmv', ['门店'], '上个月', undefined, '降序 · 前 1')],
  ['杭州各门店昨天的销售', '销售', P('gmv', ['门店'], '昨天', '城市 = 杭州')],
  ['各品类销售额占比', '销售', P('gmv', ['品类'], '上个月')],
  ['今年每月销售额（含税）', '销售', P('gmv_tax', ['月份'], '今年'), true],
  ['上海门店 GMV', '销售', P('gmv', ['门店'], '本月至今', '城市 = 上海'), true],
  ['双 11 备货期间日销售额', '销售', P('gmv', ['日期'], '2026-10-20 至今')],
  ['合肥政务区天鹅湖店本周销售额', '销售', P('gmv', [], '本周', '门店 = 合肥政务区天鹅湖店')],
  ['线上渠道上周销售额', '销售', P('gmv', [], '上周', '渠道 = 线上')],
  ['南京门店销售额环比', '销售', P('gmv', ['门店'], '本月至今', '城市 = 南京', '环比')],
  ['上个月各大区的退款率', '退款', P('refund', ['大区'], '上个月')],
  ['各渠道退款率', '退款', P('refund', ['渠道'], '近 30 天')],
  ['退款率最高的 10 家门店', '退款', P('refund', ['门店'], '上个月', undefined, '降序 · 前 10')],
  ['华东一区本周退款率', '退款', P('refund', [], '本周', '大区 = 华东一区'), true],
  ['昨天各仓库出库及时率', '履约', P('ship', ['仓库'], '昨天')],
  ['上周各门店发货及时率', '履约', P('ship', ['门店'], '上周'), true],
  ['华东一区发货及时率是多少', '履约', P('ship', [], '本周', '大区 = 华东一区'), true],
  ['各品类毛利率排名', '毛利', P('margin', ['品类'], '本月至今', undefined, '毛利率降序')],
  ['毛利率低于 18% 的商品', '毛利', P('margin', ['商品'], '近 30 天', '毛利率 < 18%')],
  ['生鲜毛利率近 12 周', '毛利', P('margin', ['周'], '近 12 周', '品类 = 生鲜')],
  ['各大区毛利率', '毛利', P('margin', ['大区'], '上个月')],
  ['哪个门店毛利最低', '毛利', P('margin', ['门店'], '上个月', undefined, '升序 · 前 1'), true],
  ['近 6 个月复购客户销售额趋势', '复购', P('repeat', ['月份'], '近 6 个月')],
  ['老客销售额走势', '复购', P('repeat', ['月份'], '近 6 个月'), true],
  ['复购客户销售额（含税）按大区', '复购', P('repeat_tax', ['大区'], '上个月')],
  ['各门店复购客户销售额 Top 10', '复购', P('repeat', ['门店'], '上个月', undefined, '降序 · 前 10')],
  ['会员复购客户销售额占比', '复购', P('repeat', ['会员等级'], '上个月')],
  ['华东一区复购客户销售额（含税）', '复购', P('repeat_tax', [], '本月至今', '大区 = 华东一区')],
  ['本周有效订单数按渠道', '订单', P('orders', ['渠道'], '本周')],
  ['各城市上周成交单量排名', '订单', P('orders', ['城市'], '上周', undefined, '降序'), true],
  ['昨天一共多少单', '订单', P('orders', [], '昨天')],
  ['线上渠道本月成交单量', '订单', P('orders', [], '本月至今', '渠道 = 线上'), true],
  ['取消订单占比', '订单', P('orders', ['订单状态'], '近 30 天'), true],
  ['杭州门店客单价', '订单', P('atv', ['门店'], '上个月', '城市 = 杭州')],
  ['上海门店客单价对比', '订单', P('atv', ['门店'], '上周', '城市 = 上海')],
  ['各大区销售额排名', '销售', P('gmv', ['大区'], '上个月', undefined, '降序')],
  ['新店开业 30 天销售额', '销售', P('gmv', ['门店'], '开业后 30 天', '门店状态 = 新开')],
  ['各城市销售额（含税）', '销售', P('gmv_tax', ['城市'], '上个月')],
];
// 题数与 changes.ts 对齐：退款相关 4 题（CR-0010）、履约相关 3 题（CR-0008）、订单量相关 2 题（CR-0015，问法含“成交单量”）
export const EVAL_QS: EvalQ[] = EQ_SEEDS.map(([question, group, expect, fail], i) => ({
  id: `EQ-${String(i + 1).padStart(3, '0')}`, question, group, expect, last: fail ? '失败' : '通过',
  cr: pendingCrOf(expect.metric) ?? (question.includes('成交单量') ? 'CR-0015' : group === '履约' ? 'CR-0008' : undefined),
}));
export const EVAL_GROUPS: EvalGroup[] = ['销售', '退款', '履约', '毛利', '复购', '订单'];

export type FailKind = '未召回指标' | '指标选错' | '维度不匹配' | '时间解析错误' | '过滤条件缺失';
export interface FailCase { qid: string; question: string; expect: string; actual: string; kind: FailKind }
export type RunStatus = '已完成' | '运行中' | '待运行';
export interface EvalRun {
  id: string; time: string; trigger: string; version: string; scope: string; total: number; passed: number;
  status: RunStatus; operator: string; fails: FailCase[]; cr?: string; note?: string;
  /** 整版发布前回放涉及多张变更单时用 */
  crs?: string[];
}
const FAIL_ACTUAL: Record<string, [FailKind, string]> = {
  'EQ-007': ['指标选错', `${metricLabel('gmv')} · 按 月份 · 今年`],
  'EQ-008': ['未召回指标', '未识别“GMV”，按 有效订单数 v1 回答'],
  'EQ-016': ['时间解析错误', `${metricLabel('refund')} · 汇总 · 上周 · 大区 = 华东一区`],
  'EQ-018': ['未召回指标', '未识别“发货及时率”，按 有效订单数 v1 回答'],
  'EQ-019': ['未召回指标', '未识别“发货及时率”，追问用户选择指标'],
  'EQ-024': ['维度不匹配', `${metricLabel('margin')} · 按 品类 · 上个月 · 升序 · 前 1`],
  'EQ-026': ['未召回指标', '未识别“老客”，按 销售额（不含税）v3 回答'],
  'EQ-032': ['未召回指标', '未识别“成交单量”，追问用户选择指标'],
  'EQ-034': ['未召回指标', '未识别“成交单量”，按 销售额（不含税）v3 回答'],
  'EQ-035': ['过滤条件缺失', `${metricLabel('orders')} · 汇总 · 近 30 天`],
  'EQ-012': ['维度不匹配', `${metricLabel('gmv')} · 按 城市 · 本月至今 · 环比`],
  'EQ-030': ['过滤条件缺失', `${metricLabel('repeat_tax')} · 汇总 · 本月至今`],
  'EQ-022': ['时间解析错误', `${metricLabel('margin')} · 按 周 · 近 12 个月 · 品类 = 生鲜`],
  'EQ-033': ['时间解析错误', `${metricLabel('orders')} · 汇总 · 今天`],
};
const failCase = (id: string): FailCase => {
  const q = EVAL_QS.find((x) => x.id === id)!;
  const [kind, actual] = FAIL_ACTUAL[id];
  return { qid: id, question: q.question, expect: planText(q.expect), actual, kind };
};
const LAST_FAILS = EVAL_QS.filter((q) => q.last === '失败').map((q) => q.id);
const OLDER_FAILS = ['EQ-012', 'EQ-030', 'EQ-022', 'EQ-033'];
function buildRuns(): EvalRun[] {
  const all = EVAL_QS.length;
  const nOf = (id: string) => EVAL_QS.filter((q) => q.cr === id).length;
  const nRel = NEXT_RELEASE_CRS.reduce((n, id) => n + nOf(id), 0);
  const runs: EvalRun[] = [
    { id: 'ER-0025', time: '发布前自动', trigger: '整版发布前回放', version: `${NEXT_VERSION}（候选）`, scope: `履约 ${nOf('CR-0008')} + 订单量 ${nOf('CR-0015')} 题`, total: nRel, passed: 0, status: '待运行', operator: '系统', fails: [], crs: NEXT_RELEASE_CRS, note: `整版发布 ${NEXT_VERSION}（CR-0008 + CR-0015）前自动回放 ${nRel} 道评测题；当前版本 ${EVAL_QS.filter((q) => q.cr && NEXT_RELEASE_CRS.includes(q.cr) && q.last === '失败').length} 题未召回，同义词生效后预计全部通过` },
    { id: 'ER-0024', time: '审核通过后', trigger: `CR-0010 发布前回放`, version: '待定', scope: `退款相关 ${nOf('CR-0010')} 题`, total: nOf('CR-0010'), passed: 0, status: '待运行', operator: '系统', fails: [], cr: 'CR-0010', note: `CR-0010 审核中，不在 ${NEXT_VERSION}；审核通过、纳入整版发布前自动回放，按 v3（退款完成时间）比对` },
    { id: 'ER-0023', time: '2026-10-25 14:30', trigger: '手动运行', version: CUR_VERSION, scope: `全部 ${all} 题`, total: all, passed: all - LAST_FAILS.length, status: '已完成', operator: '王敏', fails: LAST_FAILS.map(failCase) },
    { id: 'ER-0022', time: '2026-10-25 02:00', trigger: '每日定时', version: CUR_VERSION, scope: `全部 ${all} 题`, total: all, passed: all - LAST_FAILS.length, status: '已完成', operator: '系统', fails: LAST_FAILS.map(failCase) },
    { id: 'ER-0021', time: '2026-10-24 21:40', trigger: 'v2026.10.3 发布后回放', version: CUR_VERSION, scope: `全部 ${all} 题`, total: all, passed: all - LAST_FAILS.length, status: '已完成', operator: '系统', fails: LAST_FAILS.map(failCase), note: 'CR-0007（销售额改不含税）随 v2026.10.3 发布后整版回放，失败与发布前一致' },
    { id: 'ER-0020', time: '2026-10-24 16:20', trigger: 'CR-0008 审核通过后试跑', version: `${NEXT_VERSION}（候选）`, scope: `履约相关 ${nOf('CR-0008')} 题`, total: nOf('CR-0008'), passed: nOf('CR-0008'), status: '已完成', operator: '周晓峰', fails: [], cr: 'CR-0008', note: `同义词“发货及时率”生效后召回 ${nOf('CR-0008')}/${nOf('CR-0008')}（当前版本 ${EVAL_QS.filter((q) => q.cr === 'CR-0008' && q.last === '通过').length}/${nOf('CR-0008')}），发布后预计提升` },
  ];
  // 往前的每日定时：失败数逐步下降
  const days = ['10-24', '10-23', '10-22', '10-21', '10-20', '10-19', '10-18', '10-17', '10-16', '10-15', '10-14', '10-13', '10-12', '10-11', '10-10'];
  days.forEach((d, i) => {
    const extra = OLDER_FAILS.slice(0, Math.min(OLDER_FAILS.length, Math.floor(i / 3) + 1));
    const ids = [...LAST_FAILS, ...extra];
    const ver = i < 1 ? 'v2026.10.2' : i < 8 ? 'v2026.10.2' : 'v2026.10.1';
    runs.push({ id: `ER-${String(19 - i).padStart(4, '0')}`, time: `2026-${d} 02:00`, trigger: '每日定时', version: ver, scope: `全部 ${all} 题`, total: all, passed: all - ids.length, status: '已完成', operator: '系统', fails: ids.map(failCase) });
  });
  return runs;
}
export const EVAL_RUNS: EvalRun[] = buildRuns();
export const passRate = (r: EvalRun) => (r.total ? r.passed / r.total : 0);

/* ───────── 反馈与纠错 ───────── */
/** 待发布生效：修正已在已通过的变更单里，随整版发布后生效 */
export type FbStatus = '待处理' | '已转同义词' | '已转认证问答' | '已忽略' | '待发布生效';
export interface Feedback {
  id: string; time: string; user: string; dept: string; question: string; answer: string; reason: string;
  fix: { kind: '同义词' | '认证问答'; text: string; target?: string };
  status: FbStatus; resolvedBy?: string; handler?: string;
}
type FbSeed = [string, string, string, string, string, Feedback['fix'], FbStatus?, string?];
const FB_SEEDS: FbSeed[] = [
  ['李娜', '华东一区运营', '上周各门店发货及时率', '未识别“发货及时率”，按 有效订单数 v1 回答', '我问的是发货及时率，不是订单数', { kind: '同义词', text: '发货及时率', target: '出库及时率 v1' }, '待发布生效', 'CR-0008'],
  ['赵磊', '华东大区经营', '上个月 GMV 最高的门店', '未识别“GMV”，按 有效订单数 v1 回答', 'GMV 就是销售额', { kind: '同义词', text: 'GMV', target: '销售额（不含税）v3' }],
  ['钱芳', '会员运营', '老客销售额走势', '未识别“老客”，按 销售额（不含税）v3 回答', '老客应该是复购客户', { kind: '同义词', text: '老客', target: `${QA_METRIC.ex.name} ${QA_METRIC.ex.version}` }],
  ['孙悦', '华东二区运营', '哪个门店毛利最低', '按 品类 排序回答', '要的是门店，不是品类', { kind: '认证问答', text: '哪个门店毛利最低', target: '毛利率 v2 · 按 门店 · 上个月 · 升序 · 前 1' }],
  ['周涛', '财务', '本周退款率', '按 退款申请时间 统计', '财务按退款完成时间算', { kind: '认证问答', text: '本周退款率', target: '退款率（待 CR-0010 发布后按 v3）' }],
  ['吴静', '华东三区运营', '取消订单占比', '回答了有效订单数，没有按状态拆分', '要看取消的比例', { kind: '认证问答', text: '取消订单占比', target: '有效订单数 v1 · 按 订单状态 · 近 30 天' }],
  ['郑凯', '供应链', '仓库发货准时率', '未识别“发货准时率”，追问用户选择指标', '和出库及时率一个意思', { kind: '同义词', text: '发货准时率', target: '出库及时率 v1' }],
  ['王芳', '门店运营', '营业额', '按 销售额（含税）v2 回答', '日常说的营业额是不含税的', { kind: '同义词', text: '营业额', target: '销售额（不含税）v3' }, '已转同义词'],
  ['陈晨', '华东一区运营', '天鹅湖店上周卖了多少', '未识别门店“天鹅湖店”', '门店简称', { kind: '同义词', text: '天鹅湖店', target: '门店：合肥政务区天鹅湖店' }, '已转同义词'],
  ['刘洋', '数据权限', '华南大区销售额', '提示无权限查看华南大区', '我有华南权限', { kind: '认证问答', text: '华南大区销售额', target: '销售额（不含税）v3 · 大区 = 华南' }, '已忽略'],
  ['黄蕾', '品类管理', '生鲜毛利率近 12 周', '时间解析为近 12 个月', '是 12 周', { kind: '认证问答', text: '生鲜毛利率近 12 周', target: '毛利率 v2 · 按 周 · 近 12 周 · 品类 = 生鲜' }, '已转认证问答'],
  ['马超', '华东二区运营', '昨天出库及时率最差的仓库', '按 门店 排序', '应该按仓库', { kind: '认证问答', text: '昨天出库及时率最差的仓库', target: '出库及时率 v1 · 按 仓库 · 昨天 · 升序 · 前 1' }],
  ['林琳', '会员运营', '复购销售额（含税）', '按 不含税 口径回答', '我要含税的', { kind: '同义词', text: '复购销售额（含税）', target: `${QA_METRIC.in.name} ${QA_METRIC.in.version}` }],
  ['许强', '门店运营', '客单价', '提示“客单价（不含税）v1 仍是草稿”', '能不能先给个数', { kind: '认证问答', text: '各门店客单价', target: '客单价（不含税）v1（草稿，发布后认证）' }, '已忽略'],
  ['何静', '财务', '各品类毛利率排名', '按 v2（未扣促销补贴）回答', '月报已扣补贴，对不上', { kind: '认证问答', text: '各品类毛利率排名', target: '毛利率（待 CR-0011 发布后按 v3）' }],
  ['邓丽', '华东三区运营', '本月退单率', '未识别“退单率”', '退单率就是退款率', { kind: '同义词', text: '退单率', target: '退款率 v2' }],
  ['宋洁', '电商运营', '线上渠道本月成交单量', '未识别“成交单量”，追问用户', '成交单量就是有效订单数', { kind: '同义词', text: '成交单量', target: '有效订单数 v1' }, '待发布生效', 'CR-0015'],
  ['唐军', '华东一区运营', '各区县门店销售', '区县维度未下钻到门店', '要列到门店', { kind: '认证问答', text: '上海各区县门店销售额', target: '销售额（不含税）v3 · 按 区县、门店 · 上个月' }, '已转认证问答'],
  ['冯雪', '华东二区运营', '上周各门店发货及时率', '未识别“发货及时率”', '同上', { kind: '同义词', text: '发货及时率', target: '出库及时率 v1' }, '待发布生效', 'CR-0008'],
  ['韩梅', '供应链', '缺货率', '本体里暂无“缺货率”指标', '希望能加', { kind: '认证问答', text: '各门店缺货率', target: '需先在指标口径中新建' }],
  ['曹宇', '门店运营', '昨天销售额前五', '返回了前 10', '只要前五', { kind: '认证问答', text: '昨天销售额最高的 5 家门店', target: '销售额（不含税）v3 · 按 门店 · 昨天 · 降序 · 前 5' }, '已转认证问答'],
  ['彭飞', '华东大区经营', '大区销售（含税）', '按 不含税 回答', '要含税的', { kind: '同义词', text: '销售（含税）', target: '销售额（含税）v2' }],
  ['蒋琴', '会员运营', '会员复购占比', '按 会员等级 拆分正确，但未给占比', '要百分比', { kind: '认证问答', text: '会员复购客户销售额占比', target: `${QA_METRIC.ex.name} ${QA_METRIC.ex.version} · 按 会员等级 · 上个月` }],
  ['沈浩', '华东三区运营', '双十一备货销售', '“双十一备货”未解析为时间范围', '指 10-20 起', { kind: '同义词', text: '双十一备货期', target: '时间：2026-10-20 至今' }],
  ['田甜', '财务', '各渠道退款率', '按 申请时间 统计', '同退款口径问题', { kind: '认证问答', text: '各渠道退款率', target: '退款率（待 CR-0010 发布后按 v3）' }, '已忽略'],
  ['袁野', '电商运营', '昨天一共多少单', '按 今天 回答', '时间错了', { kind: '认证问答', text: '昨天一共多少单', target: '有效订单数 v1 · 汇总 · 昨天' }],
];
export const FEEDBACKS: Feedback[] = FB_SEEDS.map(([user, dept, question, answer, reason, fix, status, resolvedBy], i) => {
  const h = 9 + ((i * 7) % 12); const m = (i * 13) % 60;
  const day = 25 - Math.floor(i / 4);
  return {
    id: `FB-${String(248 - i)}`, time: `2026-10-${String(day).padStart(2, '0')} ${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`,
    user, dept, question, answer, reason, fix, status: status ?? '待处理', resolvedBy,
    handler: status && status !== '待处理' ? (resolvedBy ? '周晓峰' : PEOPLE_T[i % PEOPLE_T.length]) : undefined,
  };
});
export const fbStatusText = (f: Feedback) => (f.status === '待发布生效' && f.resolvedBy ? `待发布生效 · ${f.resolvedBy}` : f.status);

/* ───────── 高频问题推荐 ───────── */
export interface FreqQ { id: string; question: string; hits7: number; hits30: number; users: number; plan: SemPlan; consistency: number; lastAsked: string; certId?: string }
const FREQ_SEEDS: [string, number, number, SemPlan, number][] = [
  ['华东大区上周销售额', 312, 1265, P('gmv', [], '上周', '大区 = 华东大区'), 0.99],
  ['各大区本月销售额及环比', 241, 986, P('gmv', ['大区'], '本月至今', undefined, '环比'), 0.98],
  ['昨天销售额最高的 5 家门店', 203, 845, P('gmv', ['门店'], '昨天', undefined, '降序 · 前 5'), 0.97],
  ['近 6 个月复购客户销售额趋势', 177, 744, P('repeat', ['月份'], '近 6 个月'), 0.96],
  ['昨天各仓库出库及时率', 160, 655, P('ship', ['仓库'], '昨天'), 0.97],
  ['上周各门店发货及时率', 148, 512, P('ship', ['门店'], '上周'), 0.41],
  ['各品类毛利率排名', 131, 538, P('margin', ['品类'], '本月至今', undefined, '毛利率降序'), 0.88],
  ['本周有效订单数按渠道', 126, 577, P('orders', ['渠道'], '本周'), 0.99],
  ['上个月 GMV 最高的门店', 118, 466, P('gmv', ['门店'], '上个月', undefined, '降序 · 前 1'), 0.52],
  ['各品类销售额占比', 104, 433, P('gmv', ['品类'], '上个月'), 0.97],
  ['上个月各大区的退款率', 98, 412, P('refund', ['大区'], '上个月'), 0.9],
  ['各门店复购客户销售额 Top 10', 95, 402, P('repeat', ['门店'], '上个月', undefined, '降序 · 前 10'), 0.95],
  ['老客销售额走势', 91, 377, P('repeat', ['月份'], '近 6 个月'), 0.47],
  ['出库及时率低于 95% 的门店', 88, 389, P('ship', ['门店'], '近 7 天', '出库及时率 < 95%'), 0.96],
  ['华东一区本周退款率', 84, 351, P('refund', [], '本周', '大区 = 华东一区'), 0.63],
  ['华东三区本月销售额', 82, 352, P('gmv', [], '本月至今', '大区 = 华东三区'), 0.99],
  ['取消订单占比', 77, 318, P('orders', ['订单状态'], '近 30 天'), 0.58],
  ['上海各区县门店销售额', 75, 318, P('gmv', ['区县', '门店'], '上个月', '城市 = 上海'), 0.94],
  ['各大区出库及时率', 72, 302, P('ship', ['大区'], '上周'), 0.97],
  ['哪个门店毛利最低', 69, 291, P('margin', ['门店'], '上个月', undefined, '升序 · 前 1'), 0.55],
  ['毛利率低于 18% 的商品', 66, 284, P('margin', ['商品'], '近 30 天', '毛利率 < 18%'), 0.93],
  ['双 11 备货期间日销售额', 64, 271, P('gmv', ['日期'], '2026-10-20 至今'), 0.92],
  ['昨天一共多少单', 61, 255, P('orders', [], '昨天'), 0.7],
  ['华东一区复购客户销售额（含税）', 55, 231, P('repeat_tax', [], '本月至今', '大区 = 华东一区'), 0.95],
  ['各城市有效订单数排名', 53, 226, P('orders', ['城市'], '上周', undefined, '降序'), 0.98],
  ['昨天出库及时率最差的仓库', 50, 214, P('ship', ['仓库'], '昨天', undefined, '升序 · 前 1'), 0.66],
  ['各大区销售额（含税）', 48, 205, P('gmv_tax', ['大区'], '上个月'), 0.96],
  ['线上渠道本月有效订单数', 47, 203, P('orders', [], '本月至今', '渠道 = 线上'), 0.98],
  ['合肥门店上周销售额', 44, 189, P('gmv', ['门店'], '上周', '城市 = 合肥'), 0.97],
  ['本月退单率', 41, 172, P('refund', [], '本月至今'), 0.38],
  ['会员复购客户销售额占比', 39, 167, P('repeat', ['会员等级'], '上个月'), 0.9],
  ['线上单量', 36, 150, P('orders', [], '本周', '渠道 = 线上'), 0.44],
];
export const FREQ_QS: FreqQ[] = FREQ_SEEDS.map(([question, hits7, hits30, plan, consistency], i) => ({
  id: `HQ-${String(i + 1).padStart(3, '0')}`, question, hits7, hits30, users: Math.round(hits30 / (3.2 + (i % 5) * 0.4)),
  plan, consistency, lastAsked: `2026-10-25 ${String(16 - (i % 8)).padStart(2, '0')}:${String((i * 17) % 60).padStart(2, '0')}`,
  certId: CERT_QAS.find((c) => c.question === question)?.id,
}));
