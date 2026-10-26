/**
 * 当前用户逐对象类型的行级范围（GET /api/admin/v1/me/row-scope；问数「范围」、对象检索读它）。
 * 开关键 admin。返回 null = 用 mock（页面继续用 mock/objects-qa.ts OBJ_SCOPE 等本地数据）。
 * 每项：visible=false → 无权查看；unrestricted → 全部；否则 conditions 之间是「或」，text 是现成的显示文字。
 */
import { apiClient, isBackendDown } from './http';

const api = apiClient('admin');

export interface ScopeCond { prop: string; op: string; values: string[]; text: string }
export interface ObjRowScope { objType: string; visible: boolean; unrestricted: boolean; conditions: ScopeCond[]; text: string }
export interface MyRowScope { userId: string; userName: string; items: ObjRowScope[] }

export async function loadMyRowScope(): Promise<MyRowScope | null> {
  if (!api.isReal()) return null;
  try {
    return await api.get<MyRowScope>('/me/row-scope');
  } catch (e) {
    if (isBackendDown(e)) return null;
    throw e;
  }
}
