/**
 * 系统管理 · 角色与权限（Roles.vue，api-index §4.2；接口见 docs/backend/admin-roles-api.md）。
 * 开关键 admin，前缀 admin（/api/admin/v1/roles…、/users/{id}/effective-access）。
 *
 * loadRolesState() 仅在 mock 模式返回 null；真模式失败直接抛出，页面保持空态并显示错误。
 * 返回数据时页面改用后台的角色、成员和用户，写操作走下面的接口，成功后页面整份重新拉取。
 * 有效权限仍在页面里用 roles.ts effectiveAccess() 算（同一套规则，后台 /effective-access 结果一致），
 * 这样预览抽屉能反映页面上还没保存的成员变化。
 */
import type { AdminUser } from '@/mock/admin';
import type { FuncLevel, ObjPerm, Role, RowScope } from '@/mock/roles';
import { apiClient, currentApiContext, type PageResult } from './http';

const api = apiClient('admin');

export type RemoteRole = Role & { memberCount: number; lockVersion: number };
export interface RolesState { roles: RemoteRole[]; members: Record<string, AdminUser[]>; users: AdminUser[] }
export type ObjectPermissionRole = Pick<Role, 'id' | 'name' | 'defaultDeny' | 'objs' | 'scopes'> & { memberCount: number };
export interface ObjectPermissionPage extends PageResult<ObjectPermissionRole> { object: { id: string; name: string; api: string } }

const enc = encodeURIComponent;

const pinned = () => { const ctx = currentApiContext(); return { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } }; };
export const queryObjectPermissions = (objectId: string, page: number, size: number) => {
  const options = pinned();
  return api.get<ObjectPermissionPage>('/roles/object-permissions', { ...options, query: { ...options.query, objectId, page, size } });
};
export const queryRoleMembers = (id: string, page: number, size: number, kw: string) => {
  const options = pinned();
  return api.get<PageResult<AdminUser>>(`/roles/${enc(id)}/members`, { ...options, query: { ...options.query, page, size, kw: kw || undefined } });
};

/** 写操作是否走后台：按开关判断（不看加载成败），后台断开时写操作直接报错，不改本地 mock */
export const rolesApiOn = () => api.isReal();

export async function loadRolesState(): Promise<RolesState | null> {
  if (!api.isReal()) return null;
  return await api.get<RolesState>('/roles/state', pinned());
}

export interface PermObject { name: string; api: string; actions: string[] }
export interface ScopeProp { objType: string; prop: string; values: string[]; displayName?: string | null; dataType?: string }
export interface RolesMeta { permObjects: PermObject[]; scopeProps: ScopeProp[] }

/** 权限元数据：对象清单和可执行范围属性来自已发布本体，空本体由后台用 mock 兜底。
 * 仅 mock 模式返回 null；真实请求错误原样抛出，空 scopeProps 不代表加载失败。 */
export async function loadRolesMeta(): Promise<RolesMeta | null> {
  if (!api.isReal()) return null;
  return await api.get<RolesMeta>('/roles/meta');
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
