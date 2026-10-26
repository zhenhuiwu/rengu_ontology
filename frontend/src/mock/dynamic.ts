/** 动态本体示例数据：告警、规则。与门店、商品、订单共用同一套 ID。 */
import { ORDERS } from './orders';

export type AlertLevel = '高' | '中' | '低';
export type AlertStatus = '待处理' | '处理中' | '已解决' | '已忽略';

export interface RuleRef { id: string; name: string; version: string; trigger: '定时' | '事件' | '阈值越线' | '状态机' }
export const RULES: Record<string, RuleRef & { condition: string; schedule: string; dedupe: string; action?: string; metric?: string }> = {
  'R-007': { id: 'R-007', name: '商品毛利率越线', version: 'v3', trigger: '阈值越线', metric: 'gross_margin', condition: '毛利率 < 18.00%，且上次计算 ≥ 18.00%', schedule: '每天 08:00', dedupe: '规则 + 商品 + 门店 + 日期，冷却 7 天', action: '发起调价审批' },
  'R-012': { id: 'R-012', name: '可用库存低于安全线', version: 'v2', trigger: '事件', condition: '收到“库存变动”事件后：可用库存 < 安全库存', schedule: '事件触发', dedupe: '规则 + 商品 + 门店，冷却 24 小时', action: '补货申请' },
  'R-015': { id: 'R-015', name: '出库超 48 小时未签收', version: 'v1', trigger: '定时', condition: '订单状态 = 已出库，且出库时长 > 48 小时', schedule: '每小时第 10 分', dedupe: '规则 + 订单，不重复', action: '发起物流催派' },
  'R-021': { id: 'R-021', name: '门店销售额环比下降', version: 'v2', trigger: '定时', metric: 'gmv', condition: '销售额（不含税）周环比 < −15.0%', schedule: '每周一 09:00', dedupe: '规则 + 门店 + 周，冷却 7 天' },
  'SM-ORD': { id: 'SM-ORD', name: '订单状态非法迁移', version: 'v4', trigger: '状态机', condition: '订单生命周期 v4 中不存在该迁移', schedule: '事件触发', dedupe: '事件 ID', action: '订单状态修正' },
};

export interface Alert {
  id: string; level: AlertLevel; ruleId: keyof typeof RULES;
  objKind: 'store' | 'product' | 'order'; objId: string; objLabel: string;
  /** 商品类告警发生在哪家门店（R-007 / R-012 的去重键含门店） */
  storeId?: string; storeLabel?: string;
  value: string; threshold: string; triggeredAt: string; status: AlertStatus; assignee: string | null;
  cutoff: string;
}

const o = (i: number) => ORDERS[i].id;

export const ALERTS: Alert[] = [
  { id: 'AL-20261025-0143', level: '高', ruleId: 'SM-ORD', objKind: 'order', objId: o(3), objLabel: o(3), value: '已签收 → 待支付', threshold: '非法迁移', triggeredAt: '2026-10-25 15:41:07', status: '待处理', assignee: null, cutoff: '2026-10-25 15:40' },
  { id: 'AL-20261025-0139', level: '高', ruleId: 'R-012', objKind: 'product', objId: '1011846', objLabel: '伊利纯牛奶 250ml×16盒', value: '可用 37 提', threshold: '安全 120 提', triggeredAt: '2026-10-25 14:22:51', status: '待处理', assignee: null, cutoff: '2026-10-25 14:20' },
  { id: 'AL-20261025-0136', level: '中', ruleId: 'R-015', objKind: 'order', objId: o(8), objLabel: o(8), value: '出库 53.4 小时', threshold: '48 小时', triggeredAt: '2026-10-25 14:10:00', status: '处理中', assignee: '陈立军', cutoff: '2026-10-25 14:00' },
  { id: 'AL-20261025-0131', level: '中', ruleId: 'R-015', objKind: 'order', objId: o(15), objLabel: o(15), value: '出库 49.1 小时', threshold: '48 小时', triggeredAt: '2026-10-25 13:10:00', status: '待处理', assignee: null, cutoff: '2026-10-25 13:00' },
  { id: 'AL-20261025-0127', level: '高', ruleId: 'R-007', objKind: 'product', objId: '1004512', objLabel: '金龙鱼黄金比例食用调和油 5L', value: '16.42%', threshold: '18.00%', triggeredAt: '2026-10-25 08:00:12', status: '处理中', assignee: '王敏', cutoff: '2026-10-24 23:00' },
  { id: 'AL-20261025-0126', level: '中', ruleId: 'R-007', objKind: 'product', objId: '1024418', objLabel: '五常大米 稻花香2号 5kg', value: '17.86%', threshold: '18.00%', triggeredAt: '2026-10-25 08:00:12', status: '待处理', assignee: null, cutoff: '2026-10-24 23:00' },
  { id: 'AL-20261024-0118', level: '高', ruleId: 'R-012', objKind: 'product', objId: '1002387', objLabel: '农夫山泉饮用天然水 550ml×24瓶', value: '可用 64 箱', threshold: '安全 200 箱', triggeredAt: '2026-10-24 19:37:28', status: '处理中', assignee: '刘婷', cutoff: '2026-10-24 19:35' },
  { id: 'AL-20261024-0112', level: '低', ruleId: 'R-015', objKind: 'order', objId: o(22), objLabel: o(22), value: '出库 48.6 小时', threshold: '48 小时', triggeredAt: '2026-10-24 16:10:00', status: '已解决', assignee: '陈立军', cutoff: '2026-10-24 16:00' },
  { id: 'AL-20261024-0105', level: '中', ruleId: 'SM-ORD', objKind: 'order', objId: o(31), objLabel: o(31), value: '已取消 → 已出库', threshold: '非法迁移', triggeredAt: '2026-10-24 11:02:45', status: '处理中', assignee: '孙浩然', cutoff: '2026-10-24 11:00' },
  { id: 'AL-20261023-0097', level: '中', ruleId: 'R-012', objKind: 'product', objId: '1031752', objLabel: '维达超韧抽纸 3层130抽×24包', value: '可用 41 提', threshold: '安全 60 提', triggeredAt: '2026-10-23 21:15:03', status: '已解决', assignee: '何静', cutoff: '2026-10-23 21:15' },
  { id: 'AL-20261020-0074', level: '中', ruleId: 'R-021', objKind: 'store', objId: 'HZ0058', objLabel: '杭州滨江星光大道店', value: '−21.6%', threshold: '−15.0%', triggeredAt: '2026-10-20 09:00:31', status: '已忽略', assignee: '吴佳', cutoff: '2026-10-19 23:00' },
  { id: 'AL-20261020-0073', level: '低', ruleId: 'R-021', objKind: 'store', objId: 'NJ0076', objLabel: '南京江宁百家湖店', value: '−15.8%', threshold: '−15.0%', triggeredAt: '2026-10-20 09:00:31', status: '已解决', assignee: '赵蕾', cutoff: '2026-10-19 23:00' },
  { id: 'AL-20261019-0068', level: '中', ruleId: 'R-007', objKind: 'product', objId: '1013309', objLabel: '蒙牛特仑苏有机纯牛奶 250ml×12盒', value: '17.21%', threshold: '18.00%', triggeredAt: '2026-10-19 08:00:09', status: '已解决', assignee: '王敏', cutoff: '2026-10-18 23:00' },
  { id: 'AL-20261018-0061', level: '低', ruleId: 'R-015', objKind: 'order', objId: o(40), objLabel: o(40), value: '出库 50.2 小时', threshold: '48 小时', triggeredAt: '2026-10-18 10:10:00', status: '已解决', assignee: '陈立军', cutoff: '2026-10-18 10:00' },
];

/** 单条告警的处理记录（告警自身的状态时间线） */
/** 'YYYY-MM-DD HH:mm:ss' 加分钟（本地时间） */
function plusMin(t: string, m: number) {
  const d = new Date(t.replace(' ', 'T')); d.setMinutes(d.getMinutes() + m);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
export function alertTimeline(a: Alert) {
  const t = a.triggeredAt;
  const items: { time: string; text: string; who: string; abnormal?: boolean }[] = [
    { time: t, text: `规则 ${a.ruleId} ${RULES[a.ruleId].version} 命中，生成告警`, who: '系统', abnormal: a.level === '高' },
  ];
  if (a.status !== '待处理') items.push({ time: plusMin(t, 12),  text: '认领，状态 待处理 → 处理中', who: a.assignee ?? '--' });
  if (a.status === '已解决') items.push({ time: plusMin(t, 95),  text: '关联动作执行成功，状态 处理中 → 已解决', who: a.assignee ?? '--' });
  if (a.status === '已忽略') items.push({ time: plusMin(t, 40),  text: '关闭为已忽略：门店装修，销售下降属预期', who: a.assignee ?? '--' });
  return items;
}

/** 历史告警：按固定种子补到真实量级，用来验证分页（10-01 至 10-17，基本已关闭） */
import { seeded } from './format';
import { PRODUCTS } from './products';
import { STORES } from './stores';
(function fillHistory() {
  const r = seeded(20261025);
  const people = ['王敏', '陈立军', '刘婷', '孙浩然', '何静', '赵蕾', '吴佳'];
  const pad = (n: number, w = 2) => String(n).padStart(w, '0');
  let seq = 60;
  for (let day = 17; day >= 1; day--) {
    const n = 5 + Math.floor(r() * 9);
    for (let k = 0; k < n; k++) {
      const pick = r();
      const ruleId = (pick < 0.3 ? 'R-015' : pick < 0.55 ? 'R-012' : pick < 0.75 ? 'R-007' : pick < 0.9 ? 'R-021' : 'SM-ORD') as Alert['ruleId'];
      const time = `2026-10-${pad(day)} ${pad(6 + Math.floor(r() * 16))}:${pad(Math.floor(r() * 60))}:${pad(Math.floor(r() * 60))}`;
      const st: AlertStatus = r() < 0.82 ? '已解决' : '已忽略';
      const who = people[Math.floor(r() * people.length)];
      const base = { id: `AL-202610${pad(day)}-${pad(seq--, 4)}`, triggeredAt: time, status: st, assignee: who, cutoff: time.slice(0, 16) };
      if (seq < 1) seq = 9999;
      if (ruleId === 'R-015' || ruleId === 'SM-ORD') {
        const ord = ORDERS[Math.floor(r() * ORDERS.length)].id;
        ALERTS.push({ ...base, level: ruleId === 'SM-ORD' ? '中' : '低', ruleId, objKind: 'order', objId: ord, objLabel: ord,
          value: ruleId === 'SM-ORD' ? '已签收 → 拣货中' : `出库 ${(48 + r() * 9).toFixed(1)} 小时`, threshold: ruleId === 'SM-ORD' ? '非法迁移' : '48 小时' });
      } else if (ruleId === 'R-021') {
        const s = STORES[Math.floor(r() * STORES.length)];
        ALERTS.push({ ...base, level: '低', ruleId, objKind: 'store', objId: s.id, objLabel: s.name, value: `−${(15 + r() * 9).toFixed(1)}%`, threshold: '−15.0%' });
      } else {
        const p = PRODUCTS[Math.floor(r() * PRODUCTS.length)];
        ALERTS.push({ ...base, level: r() < 0.3 ? '高' : '中', ruleId, objKind: 'product', objId: p.sku, objLabel: `${p.name} ${p.spec}`,
          value: ruleId === 'R-007' ? `${(15 + r() * 3).toFixed(2)}%` : `可用 ${20 + Math.floor(r() * 60)} ${p.unit}`,
          threshold: ruleId === 'R-007' ? '18.00%' : `安全 ${100 + Math.floor(r() * 100)} ${p.unit}` });
      }
    }
  }
})();

/** 商品类告警补上门店（按告警编号固定取，不影响上面的随机序列） */
export const storeOfKey = (key: string) => STORES[key.split('').reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 100003, 7) % STORES.length];
for (const a of ALERTS) if (a.objKind === 'product' && !a.storeId) { const st = storeOfKey(a.id); a.storeId = st.id; a.storeLabel = st.name; }

/** 通用维度值（后台 alert.dims / rule_hit dims，来自规则去重键）。演示数据里只有「所属门店」一个维度，页面按 dims 显示，不认门店；告警、命中、回放样本共用 */
export interface AlertDim { objKind: string; id: string; label: string; kindLabel?: string | null }
export const alertDims = (a: { storeId?: string; storeLabel?: string }): AlertDim[] => (a.storeId ? [{ objKind: 'store', id: a.storeId, label: a.storeLabel ?? a.storeId }] : []);

/** 告警转派可选的处理人（原在 pages/ops/Alerts.vue；后台 GET /people） */
export const PEOPLE = ['王敏', '陈立军', '刘婷', '孙浩然', '何静', '赵蕾', '吴佳'];
