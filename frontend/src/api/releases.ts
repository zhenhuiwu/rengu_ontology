/**
 * 本体发布记录（ontology 模块，后台骨架 versioning：GET /api/ontology/v1/releases、/releases/current）。
 *
 * 页面从这里读 RELEASES / releaseByVer，不再直接读 src/mock/releases.ts：
 * - mock 模式：内容就是 mock 的 RELEASES（同样的值、同样的顺序），界面不变；
 * - real 模式：启动时 loadReleases() 用接口结果整体替换（新→旧，第一条是当前版本），并更新 app store 的当前版本。
 */
import { shallowReactive } from 'vue';
import { RELEASES as MOCK_RELEASES, type Release } from '@/mock/releases';
import { isReal } from './config';
import { http } from './http';

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

export const fetchReleases = () => http.get<ReleaseListDto>('ontology', '/releases');
export const fetchCurrentRelease = () => http.get<CurrentVersionDto>('ontology', '/releases/current');

/** real 模式下拉取发布记录；返回当前版本（给 app store 用）。mock 模式什么都不做，返回 null。 */
export async function loadReleases(): Promise<{ current: string; publishedAt: string; next: string } | null> {
  if (!isReal('ontology')) return null;
  const [list, cur] = await Promise.all([fetchReleases(), fetchCurrentRelease()]);
  RELEASES.splice(0, RELEASES.length, ...list.items.map(toRelease));
  const head = list.items.find((r) => r.ver === list.current) ?? list.items[0];
  if (!head || !cur.current) return null;
  return { current: cur.current, publishedAt: head.publishedAt, next: cur.next };
}
