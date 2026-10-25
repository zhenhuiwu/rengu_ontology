/**
 * 系统管理 · 审计日志 + 用户与组织（用户接口见 docs/backend/admin-users-api.md）。
 * 审计日志：开关键 admin，前缀 admin（GET /api/admin/v1/audit-logs、/audit-logs/actions，
 * POST /audit-logs/export；docs/backend/audit-logs-api.md）。
 *
 * - mock 模式：和原来 AuditLog.vue 里的过滤 + paginateCursor 完全一样（同步算，界面不变）；
 * - real 模式：走真接口；后台没起来（isBackendDown）时回退 mock，控制台报警。
 * 游标是不透明字符串：mock 是 `c<偏移>`，后台是 `k<keyset>`；两边互相传错时都按第一页处理。
 */
import { ADMIN_DEPTS, AUDIT_LOGS, type AdminUser, type AuditRow } from '@/mock/admin';
import { paginateCursor, type CursorResult } from '@/mock/paginate';
import { apiClient, isBackendDown, type PageResult } from './http';

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

// ---- 用户与组织（Users.vue，api-index §4.1）----
// 页面自己保留 mock 的本地数据和逻辑；real 模式且后台可用时，queryUsers 返回一页，写操作走下面的接口。
// queryUsers 返回 null = 用 mock（开关没切到真接口，或后台没起来）。

/** 页面用的行：后台字段 pendingApprovals 映射成页面原来的 pending */
export type UserRow = AdminUser & { pending: number };
export interface UserForm { name: string; account: string; dept: string; role: string; status: AdminUser['status'] }
export interface ImportRow { name: string; account: string; dept: string; role: string; error?: string | null }
export interface UserQuery { dept: string; status: string; kw: string; page: number; size: number }

type WireUser = AdminUser & { pendingApprovals: number };
const toRow = ({ pendingApprovals, ...u }: WireUser): UserRow => ({ ...u, pending: pendingApprovals });

export async function queryUsers(q: UserQuery): Promise<{ items: UserRow[]; total: number } | null> {
  if (!api.isReal()) return null;
  try {
    const r = await api.get<PageResult<WireUser>>('/users', { query: { dept: q.dept, status: q.status, kw: q.kw, page: q.page, size: q.size } });
    return { items: r.items.map(toRow), total: r.total };
  } catch (e) {
    if (!isBackendDown(e)) throw e;
    console.warn('[api] 用户后台未启动，回退演示数据', e);
    return null;
  }
}

export async function fetchDepts(): Promise<string[]> {
  if (!api.isReal()) return ADMIN_DEPTS;
  try {
    return await api.get<string[]>('/depts');
  } catch (e) {
    if (!isBackendDown(e)) throw e;
    return ADMIN_DEPTS;
  }
}

export const createUser = async (f: UserForm) => toRow(await api.post<WireUser>('/users', f));
export const updateUser = async (id: string, f: Omit<UserForm, 'account'>) =>
  toRow(await api.put<WireUser>(`/users/${encodeURIComponent(id)}`, { name: f.name, dept: f.dept, role: f.role, status: f.status }));
/** 导入预览（只校验不写库）：传模板 CSV 原文或已解析的行 */
export const previewImport = async (src: { csv: string } | { rows: ImportRow[] }) =>
  (await api.post<{ rows: ImportRow[] }>('/users:import-preview', src)).rows;
export const importUsers = async (rows: ImportRow[]) =>
  (await api.post<{ imported: number }>('/users:import', { rows: rows.map(({ name, account, dept, role }) => ({ name, account, dept, role })) })).imported;
export const enableUser = async (id: string) => toRow(await api.post<WireUser>(`/users/${encodeURIComponent(id)}:enable`));
export const disableUser = async (id: string, transferTo?: string) =>
  toRow(await api.post<WireUser>(`/users/${encodeURIComponent(id)}:disable`, transferTo ? { transferTo } : {}));
