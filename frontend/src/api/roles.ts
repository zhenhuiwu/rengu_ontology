/**
 * 系统管理 · 角色与权限（Roles.vue，api-index §4.2；接口见 docs/backend/admin-roles-api.md）。
 * 开关键 admin，前缀 admin（/api/admin/v1/roles…、/users/{id}/effective-access）。
 *
 * 页面保留 mock 的本地数据和逻辑。loadRolesState() 返回 null = 用 mock（开关没切到真接口，或后台没起来）；
 * 返回数据时页面改用后台的角色、成员和用户，写操作走下面的接口，成功后页面整份重新拉取。
 * 有效权限仍在页面里用 roles.ts effectiveAccess() 算（同一套规则，后台 /effective-access 结果一致），
 * 这样预览抽屉能反映页面上还没保存的成员变化。
 */
import type { AdminUser } from '@/mock/admin';
import type { FuncLevel, ObjPerm, Role, RowScope } from '@/mock/roles';
import { apiClient, isBackendDown, type PageResult } from './http';

const api = apiClient('admin');
const MAX = 200; // 后台 MAX_PAGE_SIZE

export type RemoteRole = Role & { memberCount: number; lockVersion: number };
export interface RolesState { roles: RemoteRole[]; members: Record<string, AdminUser[]>; users: AdminUser[] }

const enc = encodeURIComponent;

async function allPages<T>(path: string): Promise<T[]> {
  const out: T[] = [];
  for (let page = 1; ; page++) {
    const r = await api.get<PageResult<T>>(path, { query: { page, size: MAX } });
    out.push(...r.items);
    if (out.length >= r.total || !r.items.length) return out;
  }
}

/** 写操作是否走后台：按开关判断（不看加载成败），后台断开时写操作直接报错，不改本地 mock */
export const rolesApiOn = () => api.isReal();

export async function loadRolesState(): Promise<RolesState | null> {
  if (!api.isReal()) return null;
  try {
    const roles = await api.get<RemoteRole[]>('/roles');
    const [users, ...lists] = await Promise.all([
      allPages<AdminUser>('/users'),
      ...roles.map((r) => allPages<AdminUser>(`/roles/${enc(r.id)}/members`)),
    ]);
    return { roles, users, members: Object.fromEntries(roles.map((r, i) => [r.id, lists[i]])) };
  } catch (e) {
    if (!isBackendDown(e)) throw e;
    console.warn('[api] 角色后台未启动，回退演示数据', e);
    return null;
  }
}

export const createRole = (b: { name: string; desc?: string; perms?: string[]; copyFrom?: string }) =>
  api.post<RemoteRole>('/roles', b);
export const updateRole = (id: string, b: { name: string; desc: string; lockVersion?: number }) =>
  api.patch<RemoteRole>(`/roles/${enc(id)}`, b);
export const saveRolePermissions = (id: string, b: { func: Record<string, FuncLevel>; objs: ObjPerm[]; lockVersion?: number }) =>
  api.put<RemoteRole>(`/roles/${enc(id)}/permissions`, b);
export const deleteRole = (id: string) => api.delete<void>(`/roles/${enc(id)}`).then(() => true);
export const addRoleScope = (id: string, s: Omit<RowScope, 'id'>) => api.post<RowScope>(`/roles/${enc(id)}/scopes`, s);
export const removeRoleScope = (id: string, scopeId: string) => api.delete<void>(`/roles/${enc(id)}/scopes/${enc(scopeId)}`).then(() => true);
export const addRoleMembers = async (id: string, userIds: string[]) =>
  (await api.post<{ added: number }>(`/roles/${enc(id)}/members`, { userIds })).added;
export const removeRoleMember = (id: string, userId: string) => api.delete<void>(`/roles/${enc(id)}/members/${enc(userId)}`).then(() => true);
