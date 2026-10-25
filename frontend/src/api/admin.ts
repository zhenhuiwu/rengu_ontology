/**
 * 系统管理 · 审计日志。开关键 admin，前缀 admin（GET /api/admin/v1/audit-logs、/audit-logs/actions，
 * POST /audit-logs/export；docs/backend/audit-logs-api.md）。
 *
 * - mock 模式：和原来 AuditLog.vue 里的过滤 + paginateCursor 完全一样（同步算，界面不变）；
 * - real 模式：走真接口；后台没起来（isBackendDown）时回退 mock，控制台报警。
 * 游标是不透明字符串：mock 是 `c<偏移>`，后台是 `k<keyset>`；两边互相传错时都按第一页处理。
 */
import { AUDIT_LOGS, type AuditRow } from '@/mock/admin';
import { paginateCursor, type CursorResult } from '@/mock/paginate';
import { apiClient, isBackendDown } from './http';

const api = apiClient('admin');

export type { AuditRow };

export interface AuditQuery {
  /** 'YYYY-MM-DD HH:mm:ss'（北京时间），两端都含 */
  from: string; to: string;
  action: string; result: string; field: 'actor' | 'target'; kw: string;
  cursor: string | null; size: number;
}

/** mock：原 AuditLog.vue 的 rows + paginateCursor，一字不差 */
export function queryAuditLogsMock(q: AuditQuery): CursorResult<AuditRow> {
  const k = q.kw.trim();
  const rows = AUDIT_LOGS.filter((r) =>
    r.time >= q.from && r.time <= q.to &&
    (q.action === '全部' || r.action === q.action) && (q.result === '全部' || r.result === q.result) &&
    (!k || r[q.field].includes(k)));
  return paginateCursor(rows, { cursor: q.cursor, size: q.size });
}

export const mockAuditActions = (): string[] => [...new Set(AUDIT_LOGS.map((r) => r.action))];

export async function queryAuditLogs(q: AuditQuery): Promise<CursorResult<AuditRow>> {
  if (!api.isReal()) return queryAuditLogsMock(q);
  try {
    return await api.get<CursorResult<AuditRow>>('/audit-logs', {
      query: { from: q.from, to: q.to, action: q.action, result: q.result, field: q.field, kw: q.kw, cursor: q.cursor, size: q.size },
    });
  } catch (e) {
    if (!isBackendDown(e)) throw e;
    console.warn('[api] 审计日志后台未启动，回退演示数据', e);
    return queryAuditLogsMock(q);
  }
}

/** 动作下拉（不含「全部」，页面自己加） */
export async function fetchAuditActions(): Promise<string[]> {
  if (!api.isReal()) return mockAuditActions();
  try {
    return await api.get<string[]>('/audit-logs/actions');
  } catch (e) {
    if (!isBackendDown(e)) throw e;
    return mockAuditActions();
  }
}

/** 导出（后台是桩：只返回 jobId，并记一条「导出」审计）。mock 模式返回 null。 */
export async function exportAuditLogs(q: Omit<AuditQuery, 'cursor' | 'size'>): Promise<string | null> {
  if (!api.isReal()) return null;
  const r = await api.post<{ jobId: string }>('/audit-logs/export', {
    from: q.from, to: q.to, action: q.action, result: q.result, field: q.field, kw: q.kw,
  });
  return r.jobId;
}
