/**
 * 演示业务数据。开关键 biz，前缀 biz（后台骨架 72204b0：GET /api/biz/v1/orders、/orders/{id}）。
 *
 * 首页订单表在开关 biz 切到真接口时用 listOrderDtos（kw / cutoff / lineCount 后台已支持，7817996；
 * 行级范围初版不裁剪）。对象抽屉等其他地方仍在前端对 ORDERS 全量做统计，未切。差异见 docs/backend/integration.md §5。
 */
import type { Order, OrderLine, OrderStatus } from '@/mock/orders';
import { apiClient, type PageResult } from './http';

const api = apiClient('biz');

/** 后台 OrderOut / OrderDetailOut（app/modules/biz/schemas.py） */
export interface OrderLineDto { lineNo: number; sku: string; name: string; qty: number; price: number }
export interface OrderDto {
  id: string; storeId: string; storeName: string; channel: string; status: OrderStatus;
  createdAt: string; shippedAt: string | null; amount: number; discount: number;
  warehouse: string | null; waybill: string | null; member: string | null; isTest: boolean;
  /** 列表项的订单行数（首页“商品数”列）；详情不用它，看 lines */
  lineCount?: number;
}
export interface OrderDetailDto extends OrderDto { lines: OrderLineDto[] }

export interface OrderQuery {
  storeId?: string; status?: OrderStatus; channel?: string; page?: number; size?: number;
  /** 订单号或门店名包含（区分大小写） */ kw?: string;
  /** 下单时间截止（含）YYYY-MM-DD HH:mm[:ss] */ cutoff?: string;
}

/** 薄适配：后台 → 前端 Order。列表不带 lines（返回 []）；warehouse 后台可空，前端是 string（空串兜底） */
export const toOrderLine = (l: OrderLineDto): OrderLine => ({ sku: l.sku, qty: l.qty, price: l.price });
export const toOrder = (o: OrderDto | OrderDetailDto): Order & { storeName: string } => ({
  id: o.id, storeId: o.storeId, storeName: o.storeName, channel: o.channel, status: o.status,
  createdAt: o.createdAt, shippedAt: o.shippedAt, amount: o.amount, discount: o.discount,
  lines: 'lines' in o ? o.lines.map(toOrderLine) : [],
  warehouse: o.warehouse ?? '', waybill: o.waybill, member: o.member,
});

export async function listOrders(q: OrderQuery = {}): Promise<PageResult<Order & { storeName: string }>> {
  const r = await api.get<PageResult<OrderDto>>('/orders', { query: { ...q } });
  return { ...r, items: r.items.map(toOrder) };
}
export async function getOrder(id: string): Promise<Order & { storeName: string }> {
  return toOrder(await api.get<OrderDetailDto>(`/orders/${encodeURIComponent(id)}`));
}

/** 首页订单表用：原样返回后台列表项（带 storeName、lineCount），不转成前端 Order */
export function listOrderDtos(q: OrderQuery = {}): Promise<PageResult<OrderDto> & { dataAsOf?: string }> {
  return api.get<PageResult<OrderDto> & { dataAsOf?: string }>('/orders', { query: { ...q } });
}
