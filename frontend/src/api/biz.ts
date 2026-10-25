/**
 * 演示业务数据（biz 模块，后台骨架 72204b0：GET /api/biz/v1/orders、/orders/{id}）。
 *
 * 只提供调用和适配，**还没有接到页面**：首页、对象抽屉等现在都在前端对 ORDERS 全量做统计和筛选，
 * 骨架的订单列表还缺 kw / cutoff、列表不带 lines，非管理员的行级范围是桩（一律 0 行），
 * 切过去会改变页面结果。差异见 docs/backend/integration.md §5。
 */
import type { Order, OrderLine, OrderStatus } from '@/mock/orders';
import { http, type PageResult } from './http';

/** 后台 OrderOut / OrderDetailOut（app/modules/biz/schemas.py） */
export interface OrderLineDto { lineNo: number; sku: string; name: string; qty: number; price: number }
export interface OrderDto {
  id: string; storeId: string; storeName: string; channel: string; status: OrderStatus;
  createdAt: string; shippedAt: string | null; amount: number; discount: number;
  warehouse: string | null; waybill: string | null; member: string | null; isTest: boolean;
}
export interface OrderDetailDto extends OrderDto { lines: OrderLineDto[] }

export interface OrderQuery { storeId?: string; status?: OrderStatus; channel?: string; page?: number; size?: number }

/** 薄适配：后台 → 前端 Order。列表不带 lines（返回 []）；warehouse 后台可空，前端是 string（空串兜底） */
export const toOrderLine = (l: OrderLineDto): OrderLine => ({ sku: l.sku, qty: l.qty, price: l.price });
export const toOrder = (o: OrderDto | OrderDetailDto): Order & { storeName: string } => ({
  id: o.id, storeId: o.storeId, storeName: o.storeName, channel: o.channel, status: o.status,
  createdAt: o.createdAt, shippedAt: o.shippedAt, amount: o.amount, discount: o.discount,
  lines: 'lines' in o ? o.lines.map(toOrderLine) : [],
  warehouse: o.warehouse ?? '', waybill: o.waybill, member: o.member,
});

export async function listOrders(q: OrderQuery = {}): Promise<PageResult<Order & { storeName: string }>> {
  const r = await http.get<PageResult<OrderDto>>('biz', '/orders', { query: { ...q } });
  return { ...r, items: r.items.map(toOrder) };
}
export async function getOrder(id: string): Promise<Order & { storeName: string }> {
  return toOrder(await http.get<OrderDetailDto>('biz', `/orders/${encodeURIComponent(id)}`));
}
