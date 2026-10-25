/**
 * 本体发布记录。开关键 versioning，前缀 ontology（GET /api/ontology/v1/releases、/releases/current）。
 *
 * 页面从这里读 RELEASES / releaseByVer，不再直接读 src/mock/releases.ts：
 * - mock 模式：内容就是 mock 的 RELEASES（同样的值、同样的顺序），界面不变；
 * - real 模式：启动时 loadReleases() 用接口结果整体替换（新→旧，第一条是当前版本），并更新 app store 的当前版本；
 *   接口返回空列表或 current 为 null（例如默认 seed 跳过了 versioning）时保留 mock，不改当前版本，由调用方提示。
 */
import { shallowReactive } from 'vue';
import { RELEASES as MOCK_RELEASES, type Release } from '@/mock/releases';
import { apiClient } from './http';

const api = apiClient('versioning');

export type { Release };

/** 后台 ReleaseOut（app/modules/versioning/schemas.py）；比前端多 parentVer/kind/rollbackOf/changeOrderIds */
export interface ReleaseDto {
  ver: string; publishedAt: string; by: string; note: string;
  parentVer?: string | null; kind?: '发布' | '回滚'; rollbackOf?: string | null; changeOrderIds?: string[];
}
export interface ReleaseListDto { items: ReleaseDto[]; current: string | null }
export interface CurrentVersionDto { current: string | null; next: string; release: ReleaseDto | null }

/** 薄适配：只取前端 Release 的四个字段，多出的字段不进页面 */
export const toRelease = (r: ReleaseDto): Release => ({ ver: r.ver, publishedAt: r.publishedAt, by: r.by, note: r.note });

export const RELEASES: Release[] = shallowReactive([...MOCK_RELEASES]);
export const releaseByVer = (v: string) => RELEASES.find((r) => r.ver === v);

export const fetchReleases = () => api.get<ReleaseListDto>('/releases');
export const fetchCurrentRelease = () => api.get<CurrentVersionDto>('/releases/current');

export type LoadReleasesResult =
  | { kind: 'mock' }
  | { kind: 'empty' }
  | { kind: 'ok'; current: string; publishedAt: string; next: string };

/** real 模式下拉取发布记录。mock 模式什么都不做；接口为空时保留 mock 数据。失败抛 ApiError。 */
export async function loadReleases(): Promise<LoadReleasesResult> {
  if (!api.isReal()) return { kind: 'mock' };
  const [list, cur] = await Promise.all([fetchReleases(), fetchCurrentRelease()]);
  const head = list.items.find((r) => r.ver === list.current);
  if (!list.items.length || !list.current || !cur.current || !head) return { kind: 'empty' };
  RELEASES.splice(0, RELEASES.length, ...list.items.map(toRelease));
  return { kind: 'ok', current: cur.current, publishedAt: head.publishedAt, next: cur.next };
}
