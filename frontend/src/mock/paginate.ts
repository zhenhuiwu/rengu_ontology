/**
 * 模拟服务端分页（前端没有后端，页面统一通过这两个函数取“一页”数据，接真接口时只换实现）。
 *   paginate(rows, { page, size })            → { items, total }          页码分页（full / simple）
 *   paginateCursor(rows, { cursor, size })    → { items, nextCursor }     游标分页（cursor：审计日志、事件流）
 * 游标是不透明字符串，页面不要解析它。
 */
export interface PageResult<T> { items: T[]; total: number }
export interface CursorResult<T> { items: T[]; nextCursor: string | null }

export function paginate<T>(rows: readonly T[], q: { page: number; size: number }): PageResult<T> {
  const size = Math.max(1, q.size);
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const page = Math.min(Math.max(1, q.page), pages);
  return { items: rows.slice((page - 1) * size, page * size), total: rows.length };
}

const encode = (offset: number) => `c${offset.toString(36)}`;
const decode = (cursor?: string | null) => {
  if (!cursor || cursor[0] !== 'c') return 0;
  const n = parseInt(cursor.slice(1), 36);
  return Number.isFinite(n) && n > 0 ? n : 0;
};

export function paginateCursor<T>(rows: readonly T[], q: { cursor?: string | null; size: number }): CursorResult<T> {
  const start = decode(q.cursor);
  const end = start + Math.max(1, q.size);
  return { items: rows.slice(start, end), nextCursor: end < rows.length ? encode(end) : null };
}
