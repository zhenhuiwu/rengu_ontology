/**
 * 静态定义页（对象类型、关系类型、指标、维度与码表、业务知识）接变更单写接口（static-r3）。
 * mock 模式：start 原样交给 useDraftView.start，save / submit / discard 只做页面原来的事，不发请求。
 * real 模式：先写库（POST /change-requests、/targets、PUT /items、:submit、DELETE /targets/…），成功后再改页面状态；
 *   失败弹错误并返回 false，页面不改。单号以库为准（库里新建后用 { join: 库里单号 } 登记到 useDraftView）。
 */
import type { ComputedRef } from 'vue';
import { message } from 'ant-design-vue';
import type { DraftCR, TargetKind } from '@/mock/releases';
import { crDiscard, crErr, crSave, crStart, crSubmit, type CRTargetIn, type DraftChange } from '@/api/static';
import { forgetTarget } from './useDraftView';

type StartOpts = { join?: string; title?: string; target?: string };

export function useStaticCr(
  kind: TargetKind,
  target: () => Omit<CRTargetIn, 'kind'>,
  dv: { draft: ComputedRef<DraftCR | undefined>; start: (o: StartOpts) => string; setView: (v: 'published' | 'draft') => void },
) {
  const fail = (e: unknown) => { message.error(crErr(e)); return false as const; };
  /** 发起变更 / 加入：返回单号；失败返回 undefined。tid 传新建对象的 id（这时不切视图，同 useDraftView） */
  async function start(opts: { join?: string; title?: string }, tid?: { id: string; isNew?: boolean; name?: string }): Promise<string | undefined> {
    try {
      const t = tid ?? target();
      const o = await crStart({ kind, ...t }, opts);
      return dv.start(tid ? { ...o, target: tid.id } : o);
    } catch (e) { fail(e); return undefined; }
  }
  /** 保存到草稿（一个 section 的本次改动）；crId 默认当前草稿 */
  async function save(section: string, changes: DraftChange[], crId = dv.draft.value?.id, t = target()): Promise<boolean> {
    try { await crSave(crId, { kind, ...t }, section, changes); return true; } catch (e) { return fail(e); }
  }
  async function submit() {
    const d = dv.draft.value;
    if (!d) return;
    try { await crSubmit(d.id); message.success(`${d.id} 已提交审核，可在“变更与发布”查看`); } catch (e) { fail(e); }
  }
  async function discard() {
    const d = dv.draft.value;
    if (!d) { dv.setView('published'); return; }
    const { id } = target();
    try {
      if (await crDiscard(d.id, kind, id)) { forgetTarget(d.id, kind, id); message.success(`已放弃 ${d.id} 里的本对象草稿`); }
      dv.setView('published');
    } catch (e) { fail(e); }
  }
  return { start, save, submit, discard };
}
