/**
 * 静态定义页（对象类型、关系类型、指标、维度与码表、业务知识）接变更单写接口（static-r3）。
 * mock 模式：start 原样交给 useDraftView.start，save / submit / discard 只做页面原来的事，不发请求。
 * real 模式：先写库（POST /change-requests、/targets、PUT /items、:submit、DELETE /targets/…），成功后再改页面状态；
 *   失败弹错误并返回 false，页面不改。单号以库为准（库里新建后用 { join: 库里单号 } 登记到 useDraftView）。
 */
import { computed, shallowRef, watch, type ComputedRef } from 'vue';
import { currentApiContext } from '@/api/http';
import { message } from 'ant-design-vue';
import type { DraftCR, TargetKind } from '@/mock/releases';
import { crDiscard, crErr, crSave, crStart, crSubmit, type CRTargetIn, type DraftChange, fetchCR, fetchCRItems, registerDraft, isStaticReal, staticContextKey, type CRItemOut, type CRFull, fetchWorkflowRecords, type CRRow } from '@/api/static';

type StartOpts = { join?: string; title?: string; target?: string };

export function useStaticCr(
  kind: TargetKind,
  target: () => Omit<CRTargetIn, 'kind'>,
  dv: { draft: ComputedRef<DraftCR | undefined>; start: (o: StartOpts) => string; setView: (v: 'published' | 'draft') => void; cancel: (cr: string, target?: string) => void },
  afterChange?: () => Promise<void>,
) {
  const fail = (e: unknown) => { message.error(crErr(e)); return false as const; };
  const state = shallowRef<{ key: string; items: CRItemOut[]; head: CRFull }>();
  const loading = shallowRef(false);
  const error = shallowRef('');
  // Hydrated page rows also feed target(); an empty result must keep its identity across those updates.
  const emptyItems: CRItemOut[] = [];
  let sequence = 0;
  const key = () => JSON.stringify([staticContextKey(), dv.draft.value?.id, target().id]);
  const items = computed(() => state.value?.key === key() ? state.value.items : emptyItems);
  const head = computed(() => state.value?.key === key() ? state.value.head : undefined);
  async function reload() {
    const seq = ++sequence;
    const captured = key(), id = dv.draft.value?.id, tid = target().id;
    state.value = undefined; error.value = ''; loading.value = false;
    if (!isStaticReal() || !id || !tid) return;
    const ctx = currentApiContext();
    const options = { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } };
    const current = () => sequence === seq && captured === key();
    loading.value = true;
    try {
      const header = await fetchCR(id, options);
      if (!current()) return;
      const rows = await fetchCRItems(id, { kind, targetId: tid }, options);
      if (!current()) return;
      registerDraft(header);
      state.value = { key: captured, items: rows, head: header };
    } catch (e) { if (current()) { error.value = crErr(e); fail(e); } }
    finally { if (current()) loading.value = false; }
  }
  watch(key, () => { void reload(); }, { immediate: true });

  /** 发起变更 / 加入：返回单号；失败返回 undefined。tid 传新建对象的 id（这时不切视图，同 useDraftView） */
  async function start(opts: { join?: string; title?: string; desc?: string }, tid?: { id: string; isNew?: boolean; name?: string }): Promise<string | undefined> {
    const captured = staticContextKey(), t = tid ?? target();
    try {
      const o = await crStart({ kind, ...t }, opts);
      if (captured !== staticContextKey()) return undefined;
      const id = dv.start(tid || isStaticReal() ? { ...o, target: t.id } : o);
      if (isStaticReal()) {
        if (!tid && target().id === t.id) dv.setView('draft');
        await afterChange?.();
      }
      return captured === staticContextKey() ? id : undefined;
    } catch (e) { if (captured === staticContextKey()) fail(e); return undefined; }
  }
  /** 保存到草稿（一个 section 的本次改动）；crId 默认当前草稿 */
  async function save(section: string, changes: DraftChange[], crId = dv.draft.value?.id, t = target()): Promise<boolean> {
    if (isStaticReal() && (!crId || head.value?.status !== '草稿' || loading.value || error.value)) return fail(new Error('请先成功加载当前草稿后再保存'));
    const captured = staticContextKey();
    try { await crSave(crId, { kind, ...t }, section, changes); if (isStaticReal()) await reload(); return captured === staticContextKey(); } catch (e) { return captured === staticContextKey() ? fail(e) : false; }
  }
  async function submit() {
    const d = dv.draft.value;
    if (!d) return;
    if (isStaticReal() && (loading.value || head.value?.status !== '草稿')) { fail(new Error('当前变更单不是可提交的已加载草稿')); return; }
    const captured = staticContextKey();
    try { await crSubmit(d.id); if (captured !== staticContextKey()) return; if (isStaticReal()) { await reload(); if (captured !== staticContextKey()) return; await afterChange?.(); } if (captured !== staticContextKey()) return; message.success(`${d.id} 已提交审核，可在“变更与发布”查看`); } catch (e) { if (captured === staticContextKey()) fail(e); }
  }
  async function discard(): Promise<boolean> {
    const d = dv.draft.value;
    if (!d) { dv.setView('published'); return false; }
    const { id } = target(), captured = staticContextKey();
    try {
      await crDiscard(d.id, kind, id);
      if (captured !== staticContextKey()) return false;
      dv.cancel(d.id, id);
      if (target().id === id) dv.setView('published');
      if (isStaticReal()) await afterChange?.();
      if (captured !== staticContextKey()) return false;
      message.success(`已放弃 ${d.id} 里的本对象草稿`);
      return true;
    } catch (e) { return captured === staticContextKey() ? fail(e) : false; }
  }
  return { start, save, submit, discard, items, head, loading, error, reload };
}

/** CR scalar fields and keyed collections; never mutate the published source. */
export function draftFields(items: readonly CRItemOut[], section: string): Record<string, unknown> {
  return Object.fromEntries(items.filter((r) => r.section === section).map((r) => [r.key, r.op === 'delete' ? undefined : r.after]));
}
export function draftRows<T>(base: readonly T[], items: readonly CRItemOut[], section: string, id: (row: T) => string, make: (row: CRItemOut, previous?: T) => T): T[] {
  const rows = new Map(base.map((r) => [id(r), r]));
  for (const item of items.filter((r) => r.section === section)) {
    if (item.op === 'delete') rows.delete(item.key);
    else rows.set(item.key, make(item, rows.get(item.key)));
  }
  return [...rows.values()];
}

/** Page-local workflow records. Never merge API records into the mock CR registry. */
export function useRealCrRecords() {
  const state = shallowRef<{ key: string; rows: CRRow[] }>();
  const error = shallowRef(''), loading = shallowRef(false);
  let seq = 0;
  const rows = computed(() => state.value?.key === staticContextKey() ? state.value.rows : []);
  async function reload() {
    const my = ++seq, key = staticContextKey(), ctx = currentApiContext();
    state.value = undefined; error.value = ''; loading.value = false;
    if (!isStaticReal() || ctx.ver) return;
    loading.value = true;
    try {
      const result = await fetchWorkflowRecords({ readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } });
      if (my === seq && key === staticContextKey()) state.value = { key, rows: result };
    } catch (e) { if (my === seq && key === staticContextKey()) error.value = crErr(e); }
    finally { if (my === seq && key === staticContextKey()) loading.value = false; }
  }
  watch(staticContextKey, () => { void reload(); }, { immediate: true });
  return { rows, error, loading, reload };
}
export function activeTargetCr(rows: readonly CRRow[], kind: TargetKind, id: string) {
  return rows.find((r) => ['草稿', '审核中', '已通过', '已驳回'].includes(r.status) && r.detail.touches?.includes(`${kind}:${id}`));
}
