import { STORES } from './stores';
import { PRODUCTS } from './products';
import { seeded } from './format';

export type OrderStatus = '待支付' | '待出库' | '拣货中' | '已出库' | '已签收' | '已取消';
export interface OrderLine { sku: string; qty: number; price: number }
export interface Order {
  id: string; storeId: string; channel: string; status: OrderStatus; createdAt: string; shippedAt: string | null;
  amount: number; discount: number; lines: OrderLine[]; warehouse: string; waybill: string | null; member: string | null;
}

const CHANNELS = ['门店POS', '小程序到家', '美团闪购', '饿了么', '京东到家', '社群团购'];
const WAREHOUSES = ['上海嘉定常温仓', '上海青浦冷链仓', '杭州萧山常温仓', '南京江宁中心仓'];
// 分布与真实导出接近：已出库占多数
const STATUS_POOL: OrderStatus[] = ['已出库', '已出库', '已出库', '已出库', '已签收', '已签收', '拣货中', '待出库', '待支付', '已取消'];

const pad = (n: number, w = 2) => String(n).padStart(w, '0');

function build(): Order[] {
  const r = seeded(20261025);
  const list: Order[] = [];
  let t = new Date('2026-10-25T15:42:18+08:00').getTime();
  for (let i = 0; i < 1286; i++) {
    t -= Math.floor(30 + r() * 420) * 1000;
    const d = new Date(t);
    const store = STORES[Math.floor(r() * STORES.length)];
    const n = 1 + Math.floor(r() * 5);
    const lines: OrderLine[] = [];
    for (let k = 0; k < n; k++) {
      const p = PRODUCTS[Math.floor(r() * PRODUCTS.length)];
      const qty = p.unit === 'kg' ? Math.round((0.3 + r() * 2.2) * 1000) / 1000 : 1 + Math.floor(r() * 3);
      lines.push({ sku: p.sku, qty, price: p.price });
    }
    const gross = lines.reduce((s, l) => s + l.qty * l.price, 0);
    const discount = r() < 0.38 ? Math.round(gross * (0.03 + r() * 0.12) * 100) / 100 : 0;
    const status = STATUS_POOL[Math.floor(r() * STATUS_POOL.length)];
    const shipped = status === '已出库' || status === '已签收';
    const fmt = (x: Date) =>
      `${x.getFullYear()}-${pad(x.getMonth() + 1)}-${pad(x.getDate())} ${pad(x.getHours())}:${pad(x.getMinutes())}:${pad(x.getSeconds())}`;
    list.push({
      id: `SO${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}${pad(7 + (i % 3))}${pad(Math.floor(r() * 99999), 5)}`,
      storeId: store.id,
      channel: CHANNELS[Math.floor(r() * CHANNELS.length)],
      status,
      createdAt: fmt(d),
      shippedAt: shipped ? fmt(new Date(t + Math.floor(600 + r() * 5400) * 1000)) : null,
      amount: Math.round((gross - discount) * 100) / 100,
      discount,
      lines,
      warehouse: WAREHOUSES[Math.floor(r() * WAREHOUSES.length)],
      waybill: shipped ? `SF${Math.floor(1e12 + r() * 9e12)}` : null,
      member: r() < 0.72 ? `1${3 + Math.floor(r() * 6)}${Math.floor(r() * 10)}****${pad(Math.floor(r() * 9999), 4)}` : null,
    });
  }
  return list;
}

export const ORDERS: Order[] = build();
export const orderById = (id: string) => ORDERS.find((o) => o.id === id);
