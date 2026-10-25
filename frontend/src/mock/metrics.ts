export interface Metric {
  id: string; name: string; formula: string; unit: string; owner: string; version: string; status: '已发布' | '草稿' | '已下线';
  dims: string[]; value: number; prev: number; updatedAt: string;
}

/**
 * 税率假设（mock）：零售商品统一按增值税 13% 计算，不含税 = 含税 ÷ (1 + 13%)，四舍五入到分。
 * 实际业务里生鲜、农产品等适用 9% 或免税，按订单行税率分别计算；这里为演示简化成单一税率。
 * 订单（ORDERS）里的“实付金额”是含税金额。
 */
export const TAX_RATE = 0.13;
export const exTax = (incl: number) => Math.round((incl / (1 + TAX_RATE)) * 100) / 100;
/** 本月累计销售额（含税）与上月同期，单位元 */
const GMV_INCL = { value: 128431762.4, prev: 131968205.17 };
const ORDER_CNT = { value: 412386, prev: 405117 };

export const METRICS: Metric[] = [
  { id: 'gmv', name: '销售额（不含税）', formula: 'SUM(订单行.不含税金额) − SUM(退款行.不含税退款金额)', unit: '元', owner: '陈立军', version: 'v3', status: '已发布', dims: ['门店', '区域', '渠道', '品类', '日期'], value: exTax(GMV_INCL.value), prev: exTax(GMV_INCL.prev), updatedAt: '2026-10-25 06:00:12' },
  { id: 'gmv_tax', name: '销售额（含税）', formula: 'SUM(订单.实付金额) − SUM(退款.退款金额)', unit: '元', owner: '陈立军', version: 'v2', status: '已发布', dims: ['门店', '区域', '渠道', '品类', '日期'], value: GMV_INCL.value, prev: GMV_INCL.prev, updatedAt: '2026-10-25 06:00:12' },
  { id: 'gross_margin', name: '毛利率', formula: '(销售额 − 销售成本) / 销售额', unit: '%', owner: '刘婷', version: 'v2', status: '已发布', dims: ['门店', '区域', '品类', '日期'], value: 0.2186, prev: 0.2231, updatedAt: '2026-10-25 06:00:12' },
  { id: 'order_cnt', name: '有效订单数', formula: 'COUNT(订单) WHERE 状态 ≠ 已取消', unit: '单', owner: '陈立军', version: 'v1', status: '已发布', dims: ['门店', '渠道', '日期'], value: ORDER_CNT.value, prev: ORDER_CNT.prev, updatedAt: '2026-10-25 06:00:12' },
  { id: 'ship_ontime', name: '出库及时率', formula: '30 分钟内变为“已出库”的订单数 / 应出库订单数', unit: '%', owner: '周晓峰', version: 'v1', status: '已发布', dims: ['门店', '仓库', '渠道', '日期'], value: 0.9682, prev: 0.9715, updatedAt: '2026-10-25 06:00:12' },
  { id: 'atv', name: '客单价（不含税）', formula: '销售额（不含税） / 有效订单数', unit: '元', owner: '刘婷', version: 'v1', status: '草稿', dims: ['门店', '渠道', '日期'], value: Math.round((exTax(GMV_INCL.value) / ORDER_CNT.value) * 100) / 100, prev: Math.round((exTax(GMV_INCL.prev) / ORDER_CNT.prev) * 100) / 100, updatedAt: '2026-10-24 18:31:47' },
];
export const metricById = (id: string) => METRICS.find((m) => m.id === id);

/** 近 30 日日销售额（含税，元）。不含税版本见 DAILY_GMV_EX（问数/页面默认口径用不含税） */
export const DAILY_GMV: { date: string; value: number }[] = (() => {
  const base = [4012876.55, 3987104.2, 4105593.81, 4398210.06, 4867305.92, 5120448.13, 4210337.4];
  const out: { date: string; value: number }[] = [];
  for (let i = 0; i < 30; i++) {
    const d = new Date(2026, 8, 26 + i);
    const wobble = ((i * 7919) % 211) / 211 - 0.5;
    out.push({ date: `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')}`, value: Math.round(base[d.getDay()] * (1 + wobble * 0.12) * 100) / 100 });
  }
  return out;
})();

/** 近 30 日日销售额（不含税，元）= DAILY_GMV ÷ (1 + TAX_RATE) */
export const DAILY_GMV_EX = DAILY_GMV.map((d) => ({ date: d.date, value: exTax(d.value) }));
